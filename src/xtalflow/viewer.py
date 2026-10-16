"""The window: plate images, the targets picked on them, and their calibration.

The experiments those targets feed are the other half of the same window, and
live in ui/experiment_coordinator.py; ``self.experiments`` is the seam between
them.
"""

from __future__ import annotations

import argparse
import re
import sys
import socket
import threading
import traceback
from collections.abc import Callable
from typing import TypeVar
from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone

from PyQt5.QtCore import (
    QEventLoop,
    QStandardPaths,
    QStringListModel,
    QTimer,
    Qt,
)
from PyQt5.QtGui import QColor, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QDockWidget,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QProgressDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QStackedWidget,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application import (
    CalibrationDetectionError,
    ProjectController,
    ReviewController,
    ReviewPersistenceError,
    TargetValidationIssue,
    WellCalibrationService,
)
from xtalflow.domain import (
    PLATE_FORMATS,
    CalibrationMethod,
    ImageCalibration,
    ImageFilter,
    PlateFormat,
    PlateImages,
    plate_format_by_id,
)
from xtalflow.domain.instruments import (
    ECHO_650,
    SHIFTER_1,
    SHIFTER_2,
)
from xtalflow.application.experiment_workflow import (
    WorkflowStep,
)
from xtalflow.infrastructure import (
    ImageSourceUnavailable,
    OpenCVWellDetector,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.infrastructure.instrument_config import load_instrument_destinations
from xtalflow.infrastructure.examples import load_examples
from xtalflow.infrastructure.user_preferences import JsonUserPreferencesStore
from xtalflow.presentation import ProjectImageSetListModel
from xtalflow.settings import (
    ApplicationSettings,
    DEFAULT_SETTINGS,
    with_network_path_policy,
)
from xtalflow.text import counted
from xtalflow.ui.review_widgets import (
    ImageCanvas,
    ImagePathStatusLabel,
    StatusMessageLabel,
    ImageSetListView,
)
from xtalflow.ui.examples_panel import ExamplesPanel
from xtalflow.ui.help_button import HelpButton
from xtalflow.ui.experiment_page import ExperimentPage
from xtalflow.ui.workspace_sidebar import WorkspaceSidebar
from xtalflow.ui.home_page import (
    HomePage,
    WorkspaceEntry,
)
from xtalflow.ui import theme
from xtalflow.ui.experiment_coordinator import ExperimentCoordinator
from xtalflow.ui.errors import IMAGE_SOURCE_ERRORS, STORE_ERRORS
from xtalflow.ui.calibration_inspector import CalibrationInspector, calibration_status
from xtalflow.ui.load_plates_dialog import (
    LoadPlatesDialog,
    LoadPlatesForm,
    PlateRequest,
    PlateSource,
    resolve_plate_sources,
)
from xtalflow.ui.plate_list import PlateCardDelegate
from xtalflow.ui.shortcuts_dialog import ShortcutsDialog


T = TypeVar("T")


class ViewerWindow(QMainWindow):
    """Review plate images and keep the experiments made from them in step."""

    def __init__(
        self,
        repository: RockMakerImageRepository,
        review_store: SQLiteReviewStore | None = None,
        auto_advance_target_count: int = 1,
        settings: ApplicationSettings | None = None,
        preferences_store: JsonUserPreferencesStore | None = None,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.review_store = review_store
        self.settings = settings or DEFAULT_SETTINGS
        self.preferences_store = preferences_store or JsonUserPreferencesStore()
        self.user_preferences = self.preferences_store.load()
        self._global_auto_advance_target_count = (
            self.user_preferences.auto_advance_target_count
            if self.user_preferences.auto_advance_target_count is not None
            else auto_advance_target_count
        )
        self._trusted_auto_well_image_sets: set[str] = set()
        self._auto_well_opted_out_image_sets: set[str] = set()
        self.project_controller = ProjectController(
            repository,
            review_store.workspace if review_store is not None else None,
            self._global_auto_advance_target_count,
        )
        self.controller: ReviewController | None = None
        self.calibration_service: WellCalibrationService | None = None
        self.current_calibration: ImageCalibration | None = None
        self._manual_calibration_points: list[tuple[float, float]] | None = None
        self.error_log_path: Path | None = None
        self._handling_unexpected_error = False
        self._target_summary_available = True
        # The other half of this window: the experiments and their steps.
        self.experiments = ExperimentCoordinator(self)
        self.setStyleSheet(theme.APPLICATION_STYLE_SHEET)
        self.setWindowTitle("XtalFlow")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        # Home lists what XtalFlow prepares; each experiment then walks its steps.
        self.home_page = HomePage()
        self.experiment_page = ExperimentPage()
        self._home_workspace_id: str | None = None
        self.select_wells_page = self._build_image_review_tab()
        self._selection_sync_timer = QTimer(self)
        self._selection_sync_timer.setSingleShot(True)
        self._selection_sync_timer.setInterval(400)
        self._selection_sync_timer.timeout.connect(self.experiments._sync_current_selection)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.experiment_page)
        # The workspace panel runs the full height beside every page.
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_sidebar = WorkspaceSidebar()
        layout.setSpacing(0)
        layout.addWidget(self.workspace_sidebar)
        layout.addWidget(self.pages, 1)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self._build_target_summary_dock()
        self._build_status_bar()
        self._connect_signals()
        self._update_navigation()
        self._initialize_projects()
        self.set_workspace_panel_visible(self.user_preferences.workspace_panel_visible)
        self.experiments.show_home()
        self.experiments._show_planning_migration_status()

    # -- Layout -----------------------------------------------------------------

    def _build_image_review_tab(self) -> QWidget:
        # Plates: loaded a few times per session, so they sit to the side.
        plates_title = QLabel("Plates")
        plates_title.setObjectName("PrimaryHeading")
        self.add_plates_button = QToolButton()
        self.add_plates_button.setText("+")
        self.add_plates_button.setToolTip("Load Plates…")
        self.add_plates_button.setAccessibleName("Load plates")
        self.plate_filter_input = QLineEdit()
        self.plate_filter_input.setPlaceholderText("Find plate…")
        self.plate_filter_input.setClearButtonEnabled(True)
        self.image_set_list = ImageSetListView()
        self.image_set_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_set_model = ProjectImageSetListModel(self._target_count_for_image_set)
        self.image_set_list.setModel(self.image_set_model)
        self.image_set_list.setItemDelegate(PlateCardDelegate(self.image_set_list))
        self.image_set_list.setToolTip(
            "Click to open · ↑/↓ switch plates · right-click to reorder, "
            "change format, or remove"
        )
        plates_header = QHBoxLayout()
        plates_header.addWidget(plates_title)
        plates_header.addStretch()
        plates_header.addWidget(self.add_plates_button)
        plates_layout = QVBoxLayout()
        plates_layout.setContentsMargins(0, 0, 0, 0)
        plates_layout.addLayout(plates_header)
        plates_layout.addWidget(self.plate_filter_input)
        plates_layout.addWidget(self.image_set_list, 1)
        plates_panel = QWidget()
        plates_panel.setMinimumWidth(200)
        plates_panel.setLayout(plates_layout)

        # Navigation above the image: repeated hundreds of times per session.
        self.navigation_label = QLabel("No image set loaded")
        self.navigation_label.setObjectName("PrimaryHeading")
        self.position_label = QLabel()
        self.position_label.setObjectName("Muted")
        self.image_filter_input = QComboBox()
        self.image_filter_input.addItem("All images", ImageFilter.ALL)
        self.image_filter_input.addItem("With targets", ImageFilter.WITH_TARGETS)
        self.image_filter_input.addItem("Reviewed, no targets", ImageFilter.WITHOUT_TARGETS)
        self.image_filter_input.addItem("Unreviewed", ImageFilter.UNREVIEWED)
        self.image_filter_input.setAccessibleName("Image filter")
        self.well_input = QLineEdit()
        self.well_input.setMaximumWidth(90)
        self.well_input.setPlaceholderText("A01a")
        self.well_input.setAccessibleName("Well address")
        self.well_input.setToolTip(
            "Enter a subwell address and press Enter · Esc restores the current well"
        )
        self.well_completion_model = QStringListModel(self)
        self.well_completer = QCompleter(self.well_completion_model, self)
        self.well_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.well_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.well_input.setCompleter(self.well_completer)
        self._well_escape_shortcut = QShortcut(QKeySequence("Escape"), self.well_input)
        self._well_escape_shortcut.setContext(Qt.WidgetShortcut)
        self._well_escape_shortcut.activated.connect(self._restore_current_well_address)
        self._well_destinations: dict[str, tuple[str, int]] = {}
        self._current_well_address = ""
        self._refreshing_target_summary = False
        self.previous_button = QPushButton("◀")
        self.previous_button.setFixedWidth(36)
        self.previous_button.setToolTip("Previous image (←)")
        self.previous_button.setAccessibleName("Previous image")
        self.next_button = QPushButton("▶")
        self.next_button.setFixedWidth(36)
        self.next_button.setToolTip("Next image (→)")
        self.next_button.setAccessibleName("Next image")
        navigation = QHBoxLayout()
        navigation.setSpacing(theme.SPACING_M)
        navigation.addWidget(self.navigation_label)
        navigation.addWidget(self.position_label)
        navigation.addStretch()
        navigation.addWidget(self.image_filter_input)
        well_label = QLabel("Well")
        well_label.setObjectName("Muted")
        navigation.addWidget(well_label)
        navigation.addWidget(self.well_input)
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        navigation.addSpacing(theme.SPACING_L)
        self.target_summary_button = QPushButton("Selected wells")
        self.target_summary_button.setCheckable(True)
        self.target_summary_button.setToolTip(
            "Show or hide this experiment's selected wells and their warnings (Ctrl+Shift+T)"
        )
        navigation.addWidget(self.target_summary_button)

        self.image_canvas = ImageCanvas()
        self.image_canvas.setAccessibleName("Crystal image")
        self.review_hint = QLabel(
            "Click to place a soaking position · right-click to remove\n"
            "← → images   ↑ ↓ plates   ? all shortcuts",
            self.image_canvas,
        )
        self.review_hint.setStyleSheet(
            "background: rgba(20, 20, 20, 215); color: white; border-radius: 6px; "
            "padding: 8px 12px;"
        )
        self.review_hint.move(theme.SPACING_L, theme.SPACING_L)
        self.review_hint.hide()
        self._review_hint_timer = QTimer(self)
        self._review_hint_timer.setSingleShot(True)
        self._review_hint_timer.setInterval(12000)
        self._review_hint_timer.timeout.connect(self.review_hint.hide)
        self.empty_state = QWidget()
        empty_title = QLabel("Load plates")
        empty_title.setObjectName("PrimaryHeading")
        self.load_plates_form = LoadPlatesForm(self.repository, PLATE_FORMATS)
        empty_layout = QVBoxLayout()
        empty_layout.addStretch()
        empty_layout.addWidget(empty_title, 0, Qt.AlignHCenter)
        empty_layout.addWidget(self.load_plates_form, 0, Qt.AlignHCenter)
        empty_layout.addWidget(self.load_plates_form.load_button, 0, Qt.AlignHCenter)
        empty_layout.addStretch()
        self.empty_state.setLayout(empty_layout)
        self.image_stack = QStackedWidget()
        self.image_stack.addWidget(self.empty_state)
        self.image_stack.addWidget(self.image_canvas)

        # Below the image: display and well-boundary adjustments.
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("−")
        self.zoom_out_button.setToolTip("Zoom out (−)")
        self.zoom_out_button.setAccessibleName("Zoom out")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")
        self.zoom_in_button.setToolTip("Zoom in (+)")
        self.zoom_in_button.setAccessibleName("Zoom in")
        self.fit_button = QPushButton("Fit")
        self.fit_button.setToolTip("Fit image to window (0)")
        self.fit_button.setAccessibleName("Fit image")
        self.calibration_label = QLabel("Well boundary: not loaded")
        self.calibration_label.setAccessibleName("Well calibration status")
        self.calibration_accept_inline_button = QPushButton("Accept")
        self.calibration_accept_inline_button.setToolTip("Accept the detected well boundary")
        self.calibration_accept_inline_button.hide()
        self.calibration_adjust_button = QPushButton("Adjust…")
        self.calibration_adjust_button.setToolTip("Well calibration details and actions")
        self.calibration_inspector = CalibrationInspector(self)
        self.auto_calibration_button = self.calibration_inspector.detect_button
        self.manual_calibration_button = self.calibration_inspector.manual_button
        self.accept_calibration_button = self.calibration_inspector.accept_button
        self.auto_confirm_plate_checkbox = self.calibration_inspector.auto_accept_checkbox
        self.auto_confirm_confidence_input = self.calibration_inspector.minimum_score_input
        self.auto_confirm_confidence_input.setValue(
            self.user_preferences.auto_confirm_confidence_percent
        )
        self.auto_confirm_confidence_input.setToolTip(
            f"Saved per user in {self.preferences_store.path}"
        )
        display_row = QHBoxLayout()
        display_row.setSpacing(theme.SPACING_S)
        display_row.addWidget(self.zoom_out_button)
        display_row.addWidget(self.zoom_label)
        display_row.addWidget(self.zoom_in_button)
        display_row.addWidget(self.fit_button)
        display_row.addStretch()
        display_row.addWidget(self.calibration_label)
        display_row.addWidget(self.calibration_accept_inline_button)
        display_row.addWidget(self.calibration_adjust_button)

        self.auto_advance_input = QSpinBox()
        self.auto_advance_input.setRange(1, 100)
        self.auto_advance_input.setValue(self._global_auto_advance_target_count)
        self.auto_advance_input.setPrefix("Next well after ")
        self.auto_advance_input.setSuffix(" position")
        self.auto_advance_input.valueChanged.connect(
            lambda count: self.auto_advance_input.setSuffix(
                " position" if count == 1 else " positions"
            )
        )
        self.auto_advance_input.setToolTip(
            "Move to the next image after this many positions. You can always move "
            "on with fewer; this is not a required count."
        )
        self.review_summary_label = QLabel(
            "Left-click adds a soaking position · right-click removes · ←/→ images"
        )
        self.review_summary_label.setObjectName("Muted")
        self.examples_button = QToolButton()
        self.examples_button.setText("Examples")
        self.examples_button.setToolTip("Lab-approved examples of where to place positions")
        self.shortcuts_button = HelpButton(
            "Click a crystal to place a soaking position, right-click to remove it.\n"
            "← → wells · ↑ ↓ plates · Space+drag pan · 0 fit · ? all shortcuts"
        )
        targets_row = QHBoxLayout()
        targets_row.addWidget(self.auto_advance_input)
        targets_row.addWidget(self.review_summary_label, 1)
        targets_row.addWidget(self.examples_button)
        targets_row.addWidget(self.shortcuts_button)

        viewer_layout = QVBoxLayout()
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(theme.SPACING_M)
        viewer_layout.addLayout(navigation)
        viewer_layout.addWidget(self.image_stack, 1)
        viewer_layout.addLayout(display_row)
        viewer_layout.addLayout(targets_row)
        viewer_panel = QWidget()
        viewer_panel.setLayout(viewer_layout)

        self.review_splitter = QSplitter(Qt.Horizontal)
        self.review_splitter.addWidget(plates_panel)
        self.review_splitter.addWidget(viewer_panel)
        self.review_splitter.setCollapsible(0, True)
        self.review_splitter.setCollapsible(1, False)
        self.review_splitter.setStretchFactor(1, 1)
        self.review_splitter.setSizes([240, 1100])

        review_layout = QVBoxLayout()
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.addWidget(self.review_splitter, 1)
        review_tab = QWidget()
        review_tab.setLayout(review_layout)
        return review_tab

    def _build_target_summary_dock(self) -> None:
        self.target_summary_table = QTableWidget(0, 6)
        self.target_summary_table.setHorizontalHeaderLabels(
            ("Plate", "Well", "Pos", "X (mm)", "Y (mm)", "Status")
        )
        self.target_summary_table.setAccessibleName("Selected soaking positions")
        self.target_summary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.target_summary_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.target_summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.target_summary_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.target_summary_table.verticalHeader().setVisible(False)
        self.target_summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.target_summary_table.horizontalHeader().setStretchLastSection(True)
        self.target_summary_status_label = QLabel("No positions selected")
        self.target_summary_status_label.setObjectName("Muted")
        self.target_summary_filter = QComboBox()
        self.target_summary_filter.addItem("All positions", "all")
        self.target_summary_filter.addItem("Warnings", "warnings")
        self.target_summary_filter.setAccessibleName("Summary filter")
        target_summary_controls = QHBoxLayout()
        target_summary_controls.addWidget(self.target_summary_status_label, 1)
        target_summary_controls.addWidget(self.target_summary_filter)
        self.remove_targets_button = QPushButton("Delete Selected")
        self.remove_targets_button.setToolTip("Delete the selected positions (Delete)")
        self.remove_targets_button.setEnabled(False)
        self.accept_valid_auto_wells_button = QPushButton("Confirm detected boundaries")
        target_summary_layout = QVBoxLayout()
        target_summary_layout.setContentsMargins(0, 0, 0, 0)
        target_summary_layout.addLayout(target_summary_controls)
        target_summary_layout.addWidget(self.target_summary_table, 1)
        target_summary_actions = QHBoxLayout()
        target_summary_actions.addWidget(self.accept_valid_auto_wells_button)
        target_summary_actions.addWidget(self.remove_targets_button)
        target_summary_layout.addLayout(target_summary_actions)
        target_summary_panel = QWidget()
        target_summary_panel.setLayout(target_summary_layout)
        # Shown only while selecting wells; Review shows the plan's own table.
        self.target_summary_dock = QDockWidget("Selected wells", self)
        self._target_summary_visible_in_review = False
        self.target_summary_dock.setObjectName("target_summary_dock")
        self.target_summary_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.target_summary_dock.setWidget(target_summary_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.target_summary_dock)
        self.target_summary_dock.hide()
        self.view_menu = self.menuBar().addMenu("View")
        self.target_summary_action = self.target_summary_dock.toggleViewAction()
        self.target_summary_action.setText("Selected Wells")
        self.target_summary_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.view_menu.addAction(self.target_summary_action)
        # Small screens can give the plate list's width to the image.
        self.plates_panel_action = self.view_menu.addAction("Plate List")
        self.plates_panel_action.setCheckable(True)
        self.plates_panel_action.setChecked(True)
        self.plates_panel_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.plates_panel_action.toggled.connect(self.review_splitter.widget(0).setVisible)
        # People who want the full width hide the workspace panel; the choice is remembered.
        self.workspace_panel_action = self.view_menu.addAction("Workspace Panel")
        self.workspace_panel_action.setCheckable(True)
        self.workspace_panel_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.workspace_panel_action.toggled.connect(self.set_workspace_panel_visible)
        self.help_menu = self.menuBar().addMenu("Help")
        self.shortcuts_action = self.help_menu.addAction("Keyboard Shortcuts")
        self.shortcuts_action.setShortcut(QKeySequence("?"))
        self.shortcuts_action.triggered.connect(self.show_shortcuts)

    def _build_status_bar(self) -> None:
        self.status_message_label = StatusMessageLabel()
        self.save_status_label = QLabel("Not loaded")
        self.image_path_status = ImagePathStatusLabel()
        self.statusBar().addWidget(self.save_status_label)
        self.statusBar().addWidget(self.status_message_label)
        self.statusBar().addPermanentWidget(self.image_path_status, 1)
        self.statusBar().show()

    def _connect_signals(self) -> None:
        self.add_plates_button.clicked.connect(self.open_load_plates_dialog)
        self.load_plates_form.submitted.connect(self._load_plates_from_form)
        self.examples_button.clicked.connect(self.show_examples)
        self.workspace_sidebar.start_requested.connect(self.experiments.start_experiment_choice)
        self.home_page.resume_requested.connect(self.experiments.resume_experiment)
        self.home_page.delete_requested.connect(self.experiments.delete_experiment)
        self.experiment_page.home_requested.connect(self.experiments.show_home)
        self.experiment_page.delete_requested.connect(self.experiments.delete_current_experiment)
        self.experiment_page.back_requested.connect(self.experiments._go_back)
        self.experiment_page.primary_requested.connect(self.experiments._primary_action)
        self.experiment_page.stepper.step_selected.connect(self.experiments.go_to_step)
        self.plate_filter_input.textChanged.connect(self._filter_plate_list)
        self.target_summary_button.toggled.connect(self.target_summary_dock.setVisible)
        self.target_summary_dock.visibilityChanged.connect(
            self._target_summary_visibility_changed
        )
        self.target_summary_table.currentCellChanged.connect(
            self._target_summary_current_cell_changed
        )
        self.target_summary_table.customContextMenuRequested.connect(
            self._show_target_summary_context_menu
        )
        self.remove_targets_button.clicked.connect(self._remove_selected_targets)
        self.accept_valid_auto_wells_button.clicked.connect(self._accept_valid_auto_wells)
        self.target_summary_filter.currentIndexChanged.connect(self._refresh_target_summary)
        self.target_summary_table.itemSelectionChanged.connect(
            self._update_delete_selected_button
        )
        for key in ("Delete", "Backspace"):
            shortcut = QShortcut(QKeySequence(key), self.target_summary_table)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(self._remove_selected_targets)
        sidebar = self.workspace_sidebar
        sidebar.workspace_selected.connect(self._select_home_workspace)
        sidebar.create_workspace_requested.connect(self.create_workspace)
        sidebar.rename_workspace_requested.connect(self.rename_workspace)
        sidebar.hide_workspace_requested.connect(self.hide_workspace)
        sidebar.restore_workspace_requested.connect(self.restore_workspace)
        sidebar.collapse_requested.connect(lambda: self.set_workspace_panel_visible(False))
        self.home_page.rename_workspace_requested.connect(self._edit_workspace_name)
        self.home_page.hide_workspace_requested.connect(self.hide_workspace)
        self.home_page.show_panel_requested.connect(
            lambda: self.set_workspace_panel_visible(True)
        )
        self.experiment_page.show_panel_requested.connect(
            lambda: self.set_workspace_panel_visible(True)
        )
        self.image_set_list.clicked.connect(self._image_set_selected)
        self.image_set_list.customContextMenuRequested.connect(
            self._show_image_set_context_menu
        )
        self.previous_button.clicked.connect(self.show_previous)
        self.next_button.clicked.connect(self.show_next)
        self.fit_button.clicked.connect(self.image_canvas.fit_image)
        self.zoom_in_button.clicked.connect(self.image_canvas.zoom_in)
        self.zoom_out_button.clicked.connect(self.image_canvas.zoom_out)
        self.image_canvas.zoom_changed.connect(self._update_zoom_label)
        self.image_canvas.image_clicked.connect(self._handle_image_click)
        self.image_canvas.previous_requested.connect(self.show_previous)
        self.image_canvas.next_requested.connect(self.show_next)
        for source in (self.image_canvas, self.image_set_list):
            source.previous_plate_requested.connect(
                lambda: self._switch_active_image_set(-1)
            )
            source.next_plate_requested.connect(lambda: self._switch_active_image_set(1))
        self.auto_advance_input.valueChanged.connect(self._change_auto_advance_target_count)
        self.calibration_adjust_button.clicked.connect(self._open_calibration_inspector)
        self.calibration_accept_inline_button.clicked.connect(
            self._accept_current_calibration
        )
        self.auto_calibration_button.clicked.connect(self._auto_detect_calibration)
        self.accept_calibration_button.clicked.connect(self._accept_current_calibration)
        self.manual_calibration_button.clicked.connect(self._start_manual_calibration)
        self.auto_confirm_plate_checkbox.toggled.connect(
            self._toggle_auto_confirm_for_active_plate
        )
        self.auto_confirm_confidence_input.valueChanged.connect(
            self._change_auto_confirm_confidence
        )
        self.image_filter_input.currentIndexChanged.connect(self._change_image_filter)
        self.well_input.returnPressed.connect(self._go_to_entered_well)
        self.well_input.editingFinished.connect(self._go_to_entered_well)
        focus_well = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_well.activated.connect(self._focus_well_input)
        self.image_canvas.cancel_requested.connect(
            self._cancel_manual_calibration_from_keyboard
        )

    def show_examples(self) -> None:
        directory = self.settings.examples_directory
        panel = ExamplesPanel(load_examples(directory), directory, self)
        panel.setAttribute(Qt.WA_DeleteOnClose)
        panel.show()
        self.examples_panel = panel

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec_()

    def _cancel_manual_calibration_from_keyboard(self) -> None:
        if self._manual_calibration_points is not None:
            self._cancel_manual_calibration()
            self.status_message_label.show_message("Well calibration cancelled", 3000)

    def _show_review_hint_once(self) -> None:
        if self.user_preferences.review_hint_shown:
            return
        self.review_hint.adjustSize()
        self.review_hint.show()
        self.review_hint.raise_()
        self._review_hint_timer.start()
        preferences = replace(self.user_preferences, review_hint_shown=True)
        try:
            self.preferences_store.save(preferences)
        except OSError:
            return
        self.user_preferences = preferences

    def _focus_well_input(self) -> None:
        if self.experiments.current_editor is None or self.pages.currentWidget() is not self.experiment_page:
            return
        if self.experiments._current_step is not WorkflowStep.SELECT_WELLS:
            self.experiments.go_to_step(WorkflowStep.SELECT_WELLS)
        self.well_input.setFocus(Qt.ShortcutFocusReason)
        self.well_input.selectAll()



    # -- Experiments ------------------------------------------------------------

































































    def _initialize_projects(self) -> None:
        unavailable: Exception | None = None
        visible = self.project_controller.visible_projects
        if visible:
            project_ids = {project.id for project in visible}
            project_id = self.project_controller.last_open_project_id
            if project_id not in project_ids:
                project_id = visible[0].id
            try:
                self.project_controller.open_project(project_id)
            except IMAGE_SOURCE_ERRORS as error:
                # Start without images so the user can retry or switch workspace.
                unavailable = error
        else:
            self.project_controller.create_project("Untitled Workspace")
        self._show_active_review()
        if unavailable is not None:
            self._show_images_unavailable(unavailable)

    def _show_images_unavailable(self, error: Exception) -> None:
        self.navigation_label.setText("Images unavailable")
        self.review_summary_label.setText(
            f"Images unavailable: {error} · select the plate again to retry"
        )
        self.status_message_label.show_message(f"Images unavailable: {error}")

    def _target_summary_visibility_changed(self, visible: bool) -> None:
        # The dock takes space from the image instead of resizing the window.
        self.target_summary_button.blockSignals(True)
        self.target_summary_button.setChecked(visible)
        self.target_summary_button.blockSignals(False)
        if visible:
            self._refresh_target_summary()

    # -- Workspaces ---------------------------------------------------------------

    def _select_home_workspace(self, workspace_id: str | None) -> None:
        """Show one folder's experiments; new experiments are created in it."""
        if self.pages.currentWidget() is self.experiment_page:
            self.experiments.show_home()
        self._home_workspace_id = workspace_id
        self._refresh_home_workspaces()

    def set_workspace_panel_visible(self, visible: bool) -> None:
        self.workspace_sidebar.setVisible(visible)
        self.home_page.panel_button.setVisible(not visible)
        self.experiment_page.panel_button.setVisible(not visible)
        self.workspace_panel_action.blockSignals(True)
        self.workspace_panel_action.setChecked(visible)
        self.workspace_panel_action.blockSignals(False)
        if self.user_preferences.workspace_panel_visible == visible:
            return
        preferences = replace(self.user_preferences, workspace_panel_visible=visible)
        try:
            self.preferences_store.save(preferences)
        except OSError:
            return
        self.user_preferences = preferences

    def _edit_workspace_name(self, workspace_id: str) -> None:
        self.set_workspace_panel_visible(True)
        self.workspace_sidebar.edit_workspace_name(workspace_id)

    def _target_workspace(self):
        """Where a new experiment goes: the folder being viewed, else the last one used."""
        visible = self.project_controller.visible_projects
        for project in visible:
            if project.id == self._home_workspace_id:
                return project
        active = self.project_controller.active_project
        if active is not None and not active.is_hidden:
            return active
        return visible[0] if visible else None

    def _refresh_home_workspaces(self, recent=None) -> None:
        if recent is None:
            recent = self.experiments._recent_experiments()
        counts: dict[str | None, int] = {None: len(recent)}
        for experiment in recent:
            counts[experiment.workspace_id] = counts.get(experiment.workspace_id, 0) + 1
        entries = tuple(
            WorkspaceEntry(project.id, project.name, project.is_hidden)
            for project in self.project_controller.projects
        )
        self.workspace_sidebar.show_workspaces(entries, self._home_workspace_id, counts)
        self.home_page.show_workspaces(entries, self._home_workspace_id)
        if self.experiments.current_editor is not None and self.pages.currentWidget() is self.experiment_page:
            self.experiments._refresh_experiment_status(self.experiments.current_editor)

    def _open_workspace(self, workspace_id: str) -> bool:
        active = self.project_controller.active_project
        if active is not None and active.id == workspace_id:
            return True
        if self._open_project_and_show(workspace_id):
            return True
        # The workspace is open without its images; experiments can still start.
        return self.project_controller.active_project is not None

    def create_workspace(self, name: str | None = None) -> None:
        names = {project.name for project in self.project_controller.projects}
        if name is None:
            name, number = "New workspace", 2
            while name in names:
                name = f"New workspace {number}"
                number += 1
        try:
            if self.controller is not None:
                self.controller.checkpoint_current()
            project = self.project_controller.create_project(name)
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot create workspace", str(error))
            return
        self._show_active_review()
        self._home_workspace_id = project.id
        self.experiments.show_home()
        self._edit_workspace_name(project.id)

    def rename_workspace(self, workspace_id: str, name: str) -> None:
        try:
            self.project_controller.rename_project(workspace_id, name)
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot rename workspace", str(error))
        self._refresh_home_workspaces()

    def hide_workspace(self, workspace_id: str) -> None:
        project = next(
            (item for item in self.project_controller.projects if item.id == workspace_id), None
        )
        if project is None:
            return
        if len(self.project_controller.visible_projects) <= 1:
            QMessageBox.information(
                self, "Cannot hide workspace",
                "Keep at least one workspace in the list for new experiments.",
            )
            return
        count = sum(
            experiment.workspace_id == workspace_id for experiment in self.experiments._recent_experiments()
        )
        if QMessageBox.question(
            self,
            "Hide workspace",
            f"Hide '{project.name}' from the list?\n\n"
            f"Its {counted(count, 'experiment')} and plates are hidden with it. Nothing is "
            "deleted; show it again from Hidden at the bottom of the workspace list.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self.project_controller.hide_project(workspace_id)
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot hide workspace", str(error))
            return
        if self._home_workspace_id == workspace_id:
            self._home_workspace_id = None
        active = self.project_controller.active_project
        if active is not None and active.id == workspace_id:
            self._open_workspace(self.project_controller.visible_projects[0].id)
        self.experiments.show_home()

    def restore_workspace(self, workspace_id: str) -> None:
        try:
            self.project_controller.restore_project(workspace_id)
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot show workspace", str(error))
            return
        self._home_workspace_id = workspace_id
        self.experiments.show_home()

    def _image_set_selected(self, index) -> None:
        image_set_id = index.data(ProjectImageSetListModel.ImageSetIdRole)
        if not image_set_id:
            return
        try:
            self.project_controller.activate_image_set(image_set_id)
            self._show_active_review()
        except IMAGE_SOURCE_ERRORS as error:
            self._sync_project_widgets()
            QMessageBox.warning(self, "Cannot open image set", str(error))

    def _selected_image_set_id(self) -> str | None:
        index = self.image_set_list.currentIndex()
        return index.data(ProjectImageSetListModel.ImageSetIdRole) if index.isValid() else None

    def _switch_active_image_set(self, offset: int) -> None:
        project = self.project_controller.active_project
        if project is None or project.active_image_set_id is None:
            return
        image_sets = project.active_image_sets
        current = next(
            index
            for index, image_set in enumerate(image_sets)
            if image_set.id == project.active_image_set_id
        )
        destination = current + offset
        if not 0 <= destination < len(image_sets):
            return
        try:
            self.project_controller.activate_image_set(image_sets[destination].id)
            self._show_active_review()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot open image set", str(error))

    def _move_selected_image_set(self, offset: int) -> None:
        image_set_id = self._selected_image_set_id()
        if image_set_id is None:
            return
        try:
            self.project_controller.move_image_set(image_set_id, offset)
            self._sync_project_widgets()
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot reorder image set", str(error))

    def _archive_selected_image_set(self) -> None:
        image_set_id = self._selected_image_set_id()
        if image_set_id is None:
            return
        target_count = self._target_count_for_image_set(image_set_id)
        message = "Remove this image set from the project? It can be restored from the database."
        if target_count:
            message = (
                f"This image set has {target_count} targets. Remove it from the project view? "
                "Targets will be preserved."
            )
        if (
            QMessageBox.question(
                self,
                "Remove image set",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            self.project_controller.archive_image_set(image_set_id)
            self._show_active_review()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot remove image set", str(error))

    def _restore_archived_image_set(self) -> None:
        project = self.project_controller.active_project
        if project is None:
            return
        archived = [item for item in project.image_sets if item.is_archived]
        if not archived:
            QMessageBox.information(self, "Restore image set", "No archived image sets")
            return
        labels = [
            f"Plate {item.plate_code} · Batch {item.batch_id} · {item.profile}"
            for item in archived
        ]
        label, accepted = QInputDialog.getItem(
            self, "Restore image set", "Archived image set:", labels, 0, False
        )
        if not accepted:
            return
        image_set = archived[labels.index(label)]
        try:
            self.project_controller.restore_image_set(image_set.id)
            self._show_active_review()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot restore image set", str(error))

    def _show_active_review(self) -> None:
        """Take up the review the controller now holds, and redraw around it."""
        self._adopt_active_review()
        self._sync_project_widgets()

    def _open_project_and_show(self, project_id: str) -> bool:
        """Open a workspace and show it; False when its images cannot be read."""
        try:
            self.project_controller.open_project(project_id)
        except IMAGE_SOURCE_ERRORS as error:
            self._show_active_review()
            self._show_images_unavailable(error)
            return False
        self._show_active_review()
        return True

    def _adopt_active_review(self) -> None:
        self.controller = self.project_controller.review_controller
        if self.controller is None:
            self.calibration_service = None
            self.current_calibration = None
            self._manual_calibration_points = None
            self.image_canvas.clear_image()
            self.well_input.clear()
            self._well_destinations.clear()
            self.well_completion_model.setStringList([])
            self.navigation_label.setText("No image set loaded")
            self.position_label.setText("")
            self.review_summary_label.setText("Add a plate to the active workspace")
            self.calibration_label.setText("")
            self.calibration_accept_inline_button.hide()
            self.calibration_adjust_button.setEnabled(False)
            self.image_stack.setCurrentWidget(self.empty_state)
            self.save_status_label.setText("Not loaded")
            self.save_status_label.setStyleSheet(theme.status_style("muted"))
            self.image_path_status.set_image_path(None)
            self.status_message_label.clear()
            self.accept_calibration_button.setEnabled(False)
            self.auto_confirm_plate_checkbox.setEnabled(False)
            self.auto_confirm_plate_checkbox.setChecked(False)
            self._update_navigation()
            return
        self.calibration_service = None
        self.image_stack.setCurrentWidget(self.image_canvas)
        self.calibration_adjust_button.setEnabled(True)
        active_image_set = self.project_controller.active_image_set
        if (
            active_image_set is not None
            and active_image_set.id not in self._auto_well_opted_out_image_sets
        ):
            self._trusted_auto_well_image_sets.add(active_image_set.id)
        self.auto_confirm_plate_checkbox.blockSignals(True)
        self.auto_confirm_plate_checkbox.setEnabled(active_image_set is not None)
        self.auto_confirm_plate_checkbox.setChecked(
            active_image_set is not None
            and active_image_set.id in self._trusted_auto_well_image_sets
        )
        self.auto_confirm_plate_checkbox.blockSignals(False)
        self.auto_advance_input.blockSignals(True)
        self.controller.preferences.auto_advance_target_count = (
            self._global_auto_advance_target_count
        )
        self.auto_advance_input.setValue(self._global_auto_advance_target_count)
        self.auto_advance_input.blockSignals(False)
        self.image_filter_input.blockSignals(True)
        filter_index = self.image_filter_input.findData(self.controller.image_filter)
        self.image_filter_input.setCurrentIndex(filter_index)
        self.image_filter_input.blockSignals(False)
        self.well_input.clear()
        plate_format = self._active_plate_format()
        addresses: list[str] = []
        self._well_destinations.clear()
        for index, image in enumerate(self.controller.plate.images):
            if plate_format is None:
                label = f"{image.well_number}/d{image.drop_number}"
            else:
                label = str(
                    plate_format.address_for(image.well_number, image.drop_number)
                )
            addresses.append(label)
            self._well_destinations[label.casefold()] = (label, index)
        self.well_completion_model.setStringList(addresses)
        self._show_current_image()
        self._set_save_status("saved")
        self.image_canvas.setFocus(Qt.OtherFocusReason)
        self._show_review_hint_once()

    def _sync_project_widgets(self) -> None:
        active = self.project_controller.active_project
        self.image_set_model.set_project(active)
        if active is not None and active.active_image_set_id is not None:
            for row, image_set in enumerate(self.image_set_model.image_sets):
                if image_set.id == active.active_image_set_id:
                    self.image_set_list.setCurrentIndex(self.image_set_model.index(row, 0))
                    break
        self._filter_plate_list(self.plate_filter_input.text())
        self._update_review_summary()
        self._refresh_target_summary()

    def _target_count_for_image_set(self, image_set_id: str) -> int:
        if (
            self.project_controller.active_project is not None
            and self.project_controller.active_project.active_image_set_id == image_set_id
            and self.controller is not None
        ):
            return self.controller.session.target_count
        if self.review_store is not None:
            return self.review_store.workspace.target_count_for_image_set(image_set_id)
        return 0

    def open_load_plates_dialog(self) -> None:
        dialog = LoadPlatesDialog(
            self.repository, PLATE_FORMATS, self._active_plate_format(), self
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            requests = dialog.form.requested_plates()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot load plates", str(error))
            return
        plates = self._read_plates(requests)
        if plates is None:
            return
        self.load_plates(dialog.plate_format, plates)

    def _load_plates_from_form(self) -> None:
        form = self.load_plates_form
        try:
            requests = form.requested_plates()
        except IMAGE_SOURCE_ERRORS as error:
            form.error_label.setText(str(error))
            form.error_label.show()
            return
        plates = self._read_plates(requests, form.error_label)
        if plates is None:
            return
        self.load_plates(form.plate_format, plates)
        form.plate_codes_input.clear()

    def _read_plates(
        self, requests: tuple[PlateRequest, ...], error_label: QLabel | None = None
    ) -> tuple[tuple[PlateSource, PlateImages], ...] | None:
        """Read the chosen plates from the share, once, off the window's thread.

        None means the user cancelled, or the plate is not on the share; the
        plates come back with their images so adding them reads nothing again.
        """
        def read() -> tuple[tuple[PlateSource, PlateImages], ...]:
            sources = resolve_plate_sources(self.repository, requests)
            return tuple(
                (
                    source,
                    self.repository.load_plate_batch(
                        source.plate_code, source.batch_id, source.profile
                    ),
                )
                for source in sources
            )

        message = f"Reading {counted(len(requests), 'plate')} from the RockMaker share…"
        try:
            return self._read_images(message, read)
        except IMAGE_SOURCE_ERRORS as error:
            # Not the share: this plate is not on it, or has no images yet.
            plate_code = getattr(error, "plate_code", "")
            if error_label is not None:
                error_label.setText(
                    f"No plate {plate_code} on the RockMaker share · check the number"
                    if plate_code else str(error)
                )
                error_label.show()
            else:
                QMessageBox.warning(
                    self, "Cannot load plates",
                    f"No plate {plate_code} on the RockMaker share.\n\nCheck the "
                    "number. A plate appears once RockMaker has imaged it."
                    if plate_code else str(error),
                )
            return None

    def load_plates(
        self, plate_format: PlateFormat, plates: tuple[tuple[PlateSource, PlateImages], ...]
    ) -> None:
        try:
            for source, images in plates:
                self.project_controller.add_pinned_image_set(
                    source.plate_code, source.batch_id, source.profile, plate_format,
                    images,
                )
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot load plates", str(error))
        self._show_active_review()

    def load_plate(
        self, plate_code: str, plate_format: PlateFormat, profile: str = "profileID_1"
    ) -> None:
        self.project_controller.add_latest_image_set(plate_code, plate_format, profile)
        self._show_active_review()

    def _filter_plate_list(self, text: str) -> None:
        query = text.strip().casefold()
        for row, image_set in enumerate(self.image_set_model.image_sets):
            self.image_set_list.setRowHidden(
                row, bool(query) and query not in image_set.plate_code.casefold()
            )

    def _active_plate_format(self) -> PlateFormat | None:
        image_set = self.project_controller.active_image_set
        if image_set is None:
            return None
        return plate_format_by_id(
            image_set.plate_format_id, image_set.plate_format_version
        )

    def _show_image_set_context_menu(self, position) -> None:
        index = self.image_set_list.indexAt(position)
        if not index.isValid():
            return
        self.image_set_list.setCurrentIndex(index)
        image_set_id = index.data(ProjectImageSetListModel.ImageSetIdRole)
        if image_set_id is None:
            return
        menu = self._build_image_set_context_menu(image_set_id)
        menu.exec_(self.image_set_list.viewport().mapToGlobal(position))

    def _build_image_set_context_menu(self, image_set_id: str) -> QMenu:
        project = self.project_controller.active_project
        if project is None:
            raise ValueError("no project is open")
        image_set = next(item for item in project.image_sets if item.id == image_set_id)
        menu = QMenu(self.image_set_list)
        menu.addAction("Move Up").triggered.connect(
            lambda: self._move_selected_image_set(-1)
        )
        menu.addAction("Move Down").triggered.connect(
            lambda: self._move_selected_image_set(1)
        )
        format_menu = menu.addMenu("Set format")
        for plate_format in PLATE_FORMATS:
            action = format_menu.addAction(plate_format.display_name)
            action.setCheckable(True)
            action.setChecked(image_set.plate_format_id == plate_format.id)
            action.setEnabled(image_set.plate_format_id != plate_format.id)
            action.triggered.connect(
                lambda checked=False, selected=plate_format: self._change_image_set_format(
                    image_set_id, selected
                )
            )
        menu.addSeparator()
        menu.addAction("Remove from Workspace…").triggered.connect(
            self._archive_selected_image_set
        )
        restore = menu.addAction("Restore Removed Plate…")
        restore.setEnabled(any(item.is_archived for item in project.image_sets))
        restore.triggered.connect(self._restore_archived_image_set)
        return menu

    def _change_image_set_format(
        self, image_set_id: str, plate_format: PlateFormat
    ) -> None:
        try:
            project = self.project_controller.active_project
            image_set = next(
                item for item in project.image_sets if item.id == image_set_id
            )
            if (
                image_set.plate_format_id is not None
                and image_set.plate_format_id != plate_format.id
                and QMessageBox.question(
                    self,
                    "Change plate format",
                    "Changing the format changes well addresses and pixel-to-mm conversion. "
                    "Existing target pixels are preserved. Continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self.project_controller.set_image_set_plate_format(image_set_id, plate_format)
            self._show_active_review()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot set plate format", str(error))

    def _refresh_target_summary(self) -> None:
        if not self.target_summary_dock.isVisible():
            return
        current_item = self.target_summary_table.item(
            self.target_summary_table.currentRow(), 0
        )
        current_target_id = (
            current_item.data(Qt.UserRole + 2) if current_item is not None else None
        )
        try:
            all_summaries = self.project_controller.project_target_summaries()
        except IMAGE_SOURCE_ERRORS as error:
            self.status_message_label.show_message(
                f"Target summary unavailable: {error}"
            )
            return
        ready_count = sum(summary.is_ready for summary in all_summaries)
        warning_count = len(all_summaries) - ready_count
        well_count = len({summary.image.image_key for summary in all_summaries})
        self.target_summary_status_label.setText(
            f"{counted(len(all_summaries), 'position')} in {counted(well_count, 'well')} · "
            + (f"{warning_count} need attention" if warning_count else "all ready")
            if all_summaries else "No positions selected"
        )
        self.target_summary_status_label.setStyleSheet(
            theme.status_style("attention" if warning_count else "muted")
        )
        self.target_summary_filter.setItemText(0, f"All positions ({len(all_summaries)})")
        self.target_summary_filter.setItemText(1, f"Warnings ({warning_count})")
        try:
            acceptable_count = (
                self.project_controller
                .valid_unconfirmed_automatic_calibration_count()
            )
        except IMAGE_SOURCE_ERRORS:
            acceptable_count = 0
        self.accept_valid_auto_wells_button.setText(
            f"Confirm detected boundaries ({acceptable_count})"
        )
        self.accept_valid_auto_wells_button.setEnabled(acceptable_count > 0)
        summaries = (
            tuple(summary for summary in all_summaries if not summary.is_ready)
            if self.target_summary_filter.currentData() == "warnings"
            else all_summaries
        )
        project = self.project_controller.active_project
        image_sets = {item.id: item for item in project.image_sets} if project else {}
        self._refreshing_target_summary = True
        restored_row = None
        try:
            self.target_summary_table.setSortingEnabled(False)
            self.target_summary_table.clearContents()
            self.target_summary_table.setRowCount(len(summaries))
            for row, summary in enumerate(summaries):
                image_set = image_sets[summary.image_set_id]
                plate_format = plate_format_by_id(
                    image_set.plate_format_id, image_set.plate_format_version
                )
                well = (
                    str(
                        plate_format.address_for(
                            summary.image.well_number, summary.image.drop_number
                        )
                    )
                    if plate_format is not None
                    else f"{summary.image.well_number}/d{summary.image.drop_number}"
                )
                if summary.calibration is None:
                    x_mm = y_mm = "—"
                    calibration_status = "No well boundary"
                else:
                    x_value, y_value = summary.calibration.pixel_to_mm(
                        summary.target.x_px, summary.target.y_px
                    )
                    x_mm, y_mm = f"{x_value:.3f}", f"{y_value:.3f}"
                    method = (
                        "Manual"
                        if summary.calibration.method
                        is CalibrationMethod.MANUAL_THREE_POINT
                        else "Auto"
                    )
                    confirmation = (
                        "Confirmed"
                        if summary.calibration.confirmed
                        else "Unconfirmed"
                    )
                    calibration_status = (
                        f"{method} {summary.calibration.confidence:.0%} · "
                        f"{confirmation}"
                    )
                issue_labels = {
                    TargetValidationIssue.CALIBRATION_MISSING: "No well boundary",
                    TargetValidationIssue.CALIBRATION_UNCONFIRMED: "Unconfirmed well boundary",
                    TargetValidationIssue.OUTSIDE_WELL: "Outside well",
                }
                validation_status = (
                    f"{theme.SYMBOL_OK} Ready"
                    if summary.is_ready
                    else f"{theme.SYMBOL_ATTENTION} " + " · ".join(
                        issue_labels[issue] for issue in summary.validation_issues
                    )
                )
                values = (
                    summary.image.plate_code,
                    well,
                    str(summary.target_number),
                    x_mm,
                    y_mm,
                    validation_status,
                )
                tooltip = (
                    f"Well boundary: {calibration_status}\n"
                    f"Pixel: ({summary.target.x_px:.1f}, {summary.target.y_px:.1f})\n"
                    f"{summary.image.path.resolve()}"
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(tooltip)
                    if column in (3, 4):
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if column == 5:
                        item.setForeground(
                            QColor(theme.OK if summary.is_ready else theme.ATTENTION)
                        )
                    if column == 0:
                        item.setData(Qt.UserRole, summary.image_set_id)
                        item.setData(Qt.UserRole + 1, summary.image.image_key)
                        item.setData(Qt.UserRole + 2, summary.target.id)
                    self.target_summary_table.setItem(row, column, item)
                if summary.target.id == current_target_id:
                    restored_row = row
            if restored_row is not None:
                self.target_summary_table.setCurrentCell(restored_row, 0)
        finally:
            self._refreshing_target_summary = False
        self._update_delete_selected_button()

    def _update_delete_selected_button(self) -> None:
        count = len(self.target_summary_table.selectionModel().selectedRows())
        self.remove_targets_button.setText(
            f"Delete Selected ({count})" if count else "Delete Selected"
        )
        self.remove_targets_button.setEnabled(count > 0)

    def _accept_valid_auto_wells(self) -> None:
        try:
            count = (
                self.project_controller
                .valid_unconfirmed_automatic_calibration_count()
            )
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot confirm wells", str(error))
            return
        if not count:
            return
        if QMessageBox.question(
            self,
            "Confirm automatic well calibration",
            f"Confirm {count} automatically detected well(s)?\n\n"
            "Only wells whose selected targets are inside the detected boundary "
            "will be confirmed.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            confirmed = self.project_controller.confirm_valid_automatic_calibrations()
            self._show_active_review()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot confirm wells", str(error))
            return
        self.status_message_label.show_message(
            f"Confirmed {confirmed} automatic well calibration(s)", 4000
        )

    def _target_summary_current_cell_changed(
        self, row: int, column: int, previous_row: int, previous_column: int
    ) -> None:
        if self._refreshing_target_summary or row < 0:
            return
        self._target_summary_activated(row, column)

    def _show_target_summary_context_menu(self, position) -> None:
        index = self.target_summary_table.indexAt(position)
        if not index.isValid():
            return
        selected_rows = {
            selected.row()
            for selected in self.target_summary_table.selectionModel().selectedRows()
        }
        if index.row() not in selected_rows:
            self.target_summary_table.selectRow(index.row())
        menu = QMenu(self.target_summary_table)
        remove_action = menu.addAction("Delete selected positions")
        remove_action.triggered.connect(self._remove_selected_targets)
        menu.exec_(self.target_summary_table.viewport().mapToGlobal(position))

    def _remove_selected_targets(self) -> None:
        rows = sorted(
            index.row()
            for index in self.target_summary_table.selectionModel().selectedRows()
        )
        if not rows:
            return
        items = [self.target_summary_table.item(row, 0) for row in rows]
        target_ids = tuple(item.data(Qt.UserRole + 2) for item in items if item)
        descriptions = [
            f"{self.target_summary_table.item(row, 0).text()} "
            f"{self.target_summary_table.item(row, 1).text()} "
            f"target {self.target_summary_table.item(row, 2).text()}"
            for row in rows[:5]
        ]
        detail = "\n".join(descriptions)
        if len(rows) > 5:
            detail += f"\n…and {len(rows) - 5} more"
        if (
            QMessageBox.question(
                self,
                "Remove targets",
                f"Permanently remove {len(target_ids)} selected target(s)?\n\n{detail}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            removed = self.project_controller.remove_project_targets(target_ids)
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot remove targets", str(error))
            return
        if self.controller is not None:
            self.image_canvas.set_targets(self.controller.current_targets)
            self._set_save_status(
                "unsaved" if self.controller.has_unsaved_changes else "saved"
            )
        self._refresh_target_summary()
        self._update_review_summary()
        self.image_set_model.refresh_counts()
        self.status_message_label.show_message(
            f"Removed {removed} target(s)", 3000
        )

    def _target_summary_activated(self, row: int, column: int) -> None:
        identity_item = self.target_summary_table.item(row, 0)
        if identity_item is None:
            return
        image_set_id = identity_item.data(Qt.UserRole)
        image_key = identity_item.data(Qt.UserRole + 1)
        target_id = identity_item.data(Qt.UserRole + 2)
        try:
            self.project_controller.activate_image_set(image_set_id)
            self._adopt_active_review()
            destination = next(
                index
                for index, image in enumerate(self.controller.plate.images)
                if image.image_key == image_key
            )
            if destination != self.controller.image_index:
                self.controller.move_to(destination)
                self._show_current_image()
                self._set_save_status("saved")
            self._sync_project_widgets()
            self.image_canvas.set_highlighted_target(target_id)
            self.target_summary_table.setFocus(Qt.OtherFocusReason)
        except (StopIteration, *IMAGE_SOURCE_ERRORS) as error:
            QMessageBox.warning(self, "Cannot open target", str(error))

    def show_previous(self) -> bool:
        if self.controller is None:
            return False
        if self.controller.can_move_previous:
            return self._move(self.controller.move_previous)
        return self._move_across_image_sets(-1)

    def show_next(self) -> bool:
        if self.controller is None:
            return False
        if self.controller.can_move_next:
            return self._move(self.controller.move_next)
        return self._move_across_image_sets(1)

    def _move(self, move_command: Callable[[], bool]) -> bool:
        if self.controller is None:
            return False
        try:
            moved = move_command()
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return False
        if not moved:
            return False
        self.status_message_label.show_message("Review checkpoint saved", 2000)
        self._show_current_image()
        self._set_save_status("saved")
        self.image_set_model.refresh_counts()
        return True

    def _move_across_image_sets(self, direction: int) -> bool:
        if self.controller is None:
            return False
        try:
            moved = self.project_controller.move_across_image_sets(
                direction, self.controller.image_filter
            )
        except IMAGE_SOURCE_ERRORS as error:
            self._show_persistence_error(error)
            return False
        if not moved:
            return False
        self._show_active_review()
        self.status_message_label.show_message("Moved to another plate", 2000)
        return True

    def _show_current_image(self) -> None:
        if self.controller is None:
            return
        image = self.controller.current_image
        self.image_path_status.set_image_path(image.path)
        pixmap = QPixmap(str(image.path))
        if pixmap.isNull():
            raise ValueError(f"invalid image: {image.path}")
        targets = self.controller.current_targets
        self.image_canvas.set_image(pixmap, targets)
        self._manual_calibration_points = None
        self.image_canvas.set_calibration_points(())
        plate_format = self._active_plate_format()
        if plate_format is None:
            self.calibration_service = None
            self.current_calibration = None
            self.image_canvas.set_calibration(None)
            self._show_calibration_status(None, "unsupported plate format")
            image_label = image.navigation_label
        else:
            lens = plate_format.lens_for(image.drop_number)
            self.calibration_service = WellCalibrationService(
                OpenCVWellDetector(),
                lens.physical_diameter_mm,
                self.controller.store,
            )
            self._load_current_calibration()
            address = str(
                plate_format.address_for(image.well_number, image.drop_number)
            )
            image_label = f"Plate {image.plate_code} · {address}"
        if plate_format is not None:
            self._current_well_address = str(
                plate_format.address_for(image.well_number, image.drop_number)
            )
            self.well_input.setText(self._current_well_address)
        self.navigation_label.setText(image_label)
        self.navigation_label.setToolTip(
            f"RockMaker well {image.well_number}, drop {image.drop_number}"
        )
        self.position_label.setText(
            f"{self.controller.image_index + 1} / {len(self.controller.plate.images)} · "
            f"Batch {image.batch_id}"
        )
        self._update_review_summary()
        self._update_navigation()

    def _handle_image_click(self, x_px: float, y_px: float, button: int) -> None:
        self.review_hint.hide()
        if self.controller is None:
            return
        if self._manual_calibration_points is not None:
            if button == Qt.RightButton:
                self._cancel_manual_calibration()
                return
            if button != Qt.LeftButton:
                return
            self._manual_calibration_points.append((x_px, y_px))
            self.image_canvas.set_calibration_points(
                tuple(self._manual_calibration_points)
            )
            if len(self._manual_calibration_points) == 3:
                self._finish_manual_calibration()
            return
        if button == Qt.LeftButton:
            pixmap = self.image_canvas.pixmap()
            should_auto_advance = self.controller.add_target(
                x_px, y_px, pixmap.width(), pixmap.height()
            )
        elif button == Qt.RightButton:
            should_auto_advance = False
            transform = self.image_canvas.transform()
            if transform is not None:
                self.controller.remove_nearest_target(x_px, y_px, 18 / transform.scale)
        else:
            return
        self.image_canvas.set_targets(self.controller.current_targets)
        self._update_review_summary()
        if self.controller.has_unsaved_changes:
            self._set_save_status("unsaved")
        self.image_set_model.refresh_counts()
        self._refresh_target_summary()
        if should_auto_advance:
            if not self.show_next():
                try:
                    self.controller.checkpoint_current(mark_reviewed=True)
                    self.status_message_label.show_message(
                        "Review complete; final image saved", 5000
                    )
                    self._set_save_status("saved")
                except ReviewPersistenceError as error:
                    self._show_persistence_error(error)

    def _load_current_calibration(self, force_detection: bool = False) -> None:
        if self.controller is None or self.calibration_service is None:
            return
        try:
            calibration = self.calibration_service.calibration_for(
                self.controller.current_image, force_detection
            )
        except (CalibrationDetectionError, ReviewPersistenceError) as error:
            self.current_calibration = None
            self.image_canvas.set_calibration(None)
            self._show_calibration_status(None, str(error))
            return
        active_image_set = self.project_controller.active_image_set
        should_auto_confirm = (
            active_image_set is not None
            and active_image_set.id in self._trusted_auto_well_image_sets
            and calibration.method is CalibrationMethod.AUTO_CIRCLE
            and not calibration.confirmed
            and calibration.confidence
            >= self.auto_confirm_confidence_input.value() / 100
        )
        if should_auto_confirm:
            try:
                calibration = self.calibration_service.confirm(calibration)
            except ReviewPersistenceError as error:
                self._show_persistence_error(error)
        self.current_calibration = calibration
        self.image_canvas.set_calibration(calibration)
        self._show_calibration_status(calibration)
        self._refresh_target_summary()

    def _show_calibration_status(
        self, calibration: ImageCalibration | None, unavailable_reason: str = ""
    ) -> None:
        text, kind = calibration_status(calibration, unavailable_reason)
        self.calibration_label.setText(text)
        self.calibration_label.setToolTip(unavailable_reason or text)
        self.calibration_label.setStyleSheet(theme.status_style(kind))
        self.calibration_accept_inline_button.setVisible(
            calibration is not None and not calibration.confirmed
        )
        plate_format = self._active_plate_format()
        lens_diameter = None
        if plate_format is not None and self.controller is not None:
            lens_diameter = plate_format.lens_for(
                self.controller.current_image.drop_number
            ).physical_diameter_mm
        self.calibration_inspector.show_calibration(calibration, lens_diameter)

    def _open_calibration_inspector(self) -> None:
        self.calibration_inspector.open_below(self.calibration_adjust_button)

    def _toggle_auto_confirm_for_active_plate(self, enabled: bool) -> None:
        image_set = self.project_controller.active_image_set
        if image_set is None:
            return
        if enabled:
            self._auto_well_opted_out_image_sets.discard(image_set.id)
            self._trusted_auto_well_image_sets.add(image_set.id)
            try:
                self.project_controller.confirm_valid_automatic_calibrations(
                    self.auto_confirm_confidence_input.value() / 100,
                    image_set.id,
                )
            except ReviewPersistenceError as error:
                self._trusted_auto_well_image_sets.discard(image_set.id)
                self.auto_confirm_plate_checkbox.blockSignals(True)
                self.auto_confirm_plate_checkbox.setChecked(False)
                self.auto_confirm_plate_checkbox.blockSignals(False)
                self._show_persistence_error(error)
                return
            self._load_current_calibration()
            self.status_message_label.show_message(
                "Automatic well confirmation enabled for this plate", 4000
            )
        else:
            self._trusted_auto_well_image_sets.discard(image_set.id)
            self._auto_well_opted_out_image_sets.add(image_set.id)
            self.status_message_label.show_message(
                "Automatic well confirmation disabled for this plate", 3000
            )
        self._refresh_target_summary()

    def _change_auto_confirm_confidence(self, percent: int) -> None:
        preferences = replace(
            self.user_preferences, auto_confirm_confidence_percent=percent
        )
        try:
            self.preferences_store.save(preferences)
        except OSError as error:
            self.status_message_label.show_message(
                f"Could not save user preferences: {error}", 5000
            )
            return
        self.user_preferences = preferences
        self.status_message_label.show_message(
            f"Auto-confirm confidence saved at {percent}%", 3000
        )
        if self.auto_confirm_plate_checkbox.isChecked():
            self._toggle_auto_confirm_for_active_plate(True)

    def _auto_detect_calibration(self) -> None:
        current = self.current_calibration
        if current is not None and current.confirmed:
            method = (
                "manual three-point"
                if current.method is CalibrationMethod.MANUAL_THREE_POINT
                else "confirmed automatic"
            )
            if QMessageBox.question(
                self,
                "Replace well calibration",
                f"Replace the {method} well boundary with a new automatic detection?"
                "\n\nSoaking positions on this image will be converted with the "
                "new boundary.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            ) != QMessageBox.Yes:
                return
        self._cancel_manual_calibration()
        self._load_current_calibration(force_detection=True)

    def _accept_current_calibration(self) -> None:
        if self.calibration_service is None or self.current_calibration is None:
            return
        try:
            self.current_calibration = self.calibration_service.confirm(
                self.current_calibration
            )
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return
        self.image_canvas.set_calibration(self.current_calibration)
        self._load_current_calibration()
        self.status_message_label.show_message("Well calibration confirmed", 3000)

    def _start_manual_calibration(self) -> None:
        if self.controller is None:
            return
        self._manual_calibration_points = []
        self.calibration_inspector.hide()
        self.image_canvas.set_calibration_points(())
        self.calibration_label.setText(
            "Click three points on the outer well edge · right-click or Esc cancels"
        )
        self.calibration_label.setStyleSheet(theme.status_style("attention"))
        self.calibration_accept_inline_button.hide()
        self.image_canvas.setFocus(Qt.OtherFocusReason)

    def _finish_manual_calibration(self) -> None:
        if (
            self.controller is None
            or self.calibration_service is None
            or self._manual_calibration_points is None
        ):
            return
        points = tuple(self._manual_calibration_points)
        try:
            calibration = self.calibration_service.save_manual_three_point(
                self.controller.current_image, points
            )
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot calibrate well", str(error))
            self._cancel_manual_calibration()
            return
        self._manual_calibration_points = None
        self.image_canvas.set_calibration_points(())
        self.current_calibration = calibration
        self.image_canvas.set_calibration(calibration)
        self._load_current_calibration()

    def _cancel_manual_calibration(self) -> None:
        self._manual_calibration_points = None
        self.image_canvas.set_calibration_points(())
        if self.current_calibration is not None:
            self.image_canvas.set_calibration(self.current_calibration)
            self._load_current_calibration()

    def _change_auto_advance_target_count(self, count: int) -> None:
        preferences = replace(
            self.user_preferences, auto_advance_target_count=count
        )
        try:
            self.preferences_store.save(preferences)
        except OSError as error:
            self.auto_advance_input.blockSignals(True)
            self.auto_advance_input.setValue(
                self._global_auto_advance_target_count
            )
            self.auto_advance_input.blockSignals(False)
            self.status_message_label.show_message(
                f"Could not save user preferences: {error}", 5000
            )
            return
        self.user_preferences = preferences
        self._global_auto_advance_target_count = count
        if self.controller is None:
            return
        try:
            self.controller.change_auto_advance_target_count(count)
        except ReviewPersistenceError as error:
            # The user-level setting remains authoritative even if the legacy
            # plate checkpoint cannot be updated.
            self.controller.preferences.auto_advance_target_count = count
            self._show_persistence_error(error)
            return
        self.status_message_label.show_message(
            f"Targets/img saved at {count}", 3000
        )
        self._update_review_summary()

    def _change_image_filter(self) -> None:
        if self.controller is None:
            return
        image_filter = self.image_filter_input.currentData()
        try:
            moved = self.controller.change_image_filter(image_filter)
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return
        if not moved and not self.controller.current_matches_filter:
            moved = self._move_across_image_sets(1)
            if not moved:
                moved = self._move_across_image_sets(-1)
            if moved:
                return
        if moved:
            self._show_current_image()
            self._set_save_status("saved")
        else:
            self._update_review_summary()
            self._update_navigation()

    def _go_to_entered_well(self) -> None:
        if self.controller is None:
            return
        normalized = self._normalize_well_address(self.well_input.text())
        destination = self._well_destinations.get(normalized.casefold())
        if destination is None:
            self._reject_well_entry()
            return
        address, image_index = destination
        self.well_input.setText(address)
        self.well_input.setStyleSheet("")
        if image_index == self.controller.image_index:
            self._current_well_address = address
            return
        try:
            moved = self.controller.move_to(image_index)
        except STORE_ERRORS as error:
            QMessageBox.warning(self, "Cannot go to well", str(error))
            return
        if moved:
            self._show_current_image()
            self._set_save_status("saved")

    @staticmethod
    def _normalize_well_address(value: str) -> str:
        match = re.fullmatch(r"\s*([A-Ha-h])0?([1-9]|1[0-2])([A-Za-z])\s*", value)
        if match is None:
            return value.strip()
        row, column, suffix = match.groups()
        return f"{row.upper()}{int(column):02d}{suffix.lower()}"

    def _reject_well_entry(self) -> None:
        self.well_input.setText(self._current_well_address)
        self.well_input.setStyleSheet(f"border: 1px solid {theme.ERROR};")
        self.status_message_label.show_message("Invalid or unavailable subwell", 2000)
        QTimer.singleShot(800, lambda: self.well_input.setStyleSheet(""))

    def _restore_current_well_address(self) -> None:
        self.well_input.setText(self._current_well_address)
        self.well_input.setStyleSheet("")

    def _update_review_summary(self) -> None:
        if self.controller is not None:
            positions = len(self.controller.current_targets)
            review_state = (
                "reviewed"
                if self.controller.session.is_reviewed(self.controller.current_image)
                else "not reviewed yet"
            )
            filtered = len(self.controller.filtered_indices)
            total = len(self.controller.plate.images)
            matches = "" if filtered == total else f" · {filtered} images match filter"
            others = self.experiments._other_experiments_using_current_image()
            used = f" · △ also used in {', '.join(others)}" if others else ""
            self.review_summary_label.setText(
                f"This well: {counted(positions, 'position')} · {review_state}{matches}{used}"
            )
            self.review_summary_label.setStyleSheet(
                theme.status_style("attention") if others else ""
            )
        if self.project_controller.active_project is None:
            return
        try:
            per_image_set = self.project_controller.image_set_review_statistics()
        except IMAGE_SOURCE_ERRORS:
            self._update_navigation()
            return
        self.image_set_model.set_statistics(per_image_set)
        self._update_navigation()
        if (
            self.experiments.current_editor is not None
            and self.experiments._current_step is WorkflowStep.SELECT_WELLS
        ):
            self._selection_sync_timer.start()

    def _update_navigation(self) -> None:
        self.previous_button.setEnabled(
            self.controller is not None
            and (
                self.controller.can_move_previous
                or self.project_controller.has_adjacent_image_set(-1)
            )
        )
        self.next_button.setEnabled(
            self.controller is not None
            and (
                self.controller.can_move_next
                or self.project_controller.has_adjacent_image_set(1)
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self.experiments.current_editor is not None:
            self.experiments._refresh_experiment_status(self.experiments.current_editor)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        for editor in tuple(self.experiments._editors.values()):
            self.experiments._leave_experiment(editor)
        while self.controller is not None:
            try:
                self.controller.checkpoint_current()
                self._set_save_status("saved")
                break
            except ReviewPersistenceError as error:
                self._set_save_status("failed")
                answer = QMessageBox.warning(
                    self,
                    "Review was not saved",
                    f"{error}\n\nRetry, close without saving, or cancel closing?",
                    QMessageBox.Retry | QMessageBox.Discard | QMessageBox.Cancel,
                    QMessageBox.Retry,
                )
                if answer == QMessageBox.Retry:
                    continue
                if answer == QMessageBox.Cancel:
                    event.ignore()
                    return
                break
        if self.review_store is not None:
            self.review_store.close()
        super().closeEvent(event)

    def _read_images(self, message: str, task: Callable[[], T]) -> T | None:
        """Run a read of the image share, offering to look again while it fails.

        None means the user gave up. XtalFlow cannot mount the share itself, so
        trying again only checks whether it has come back.
        """
        attempts = 0
        while True:
            try:
                return self._run_in_background(message, task)
            except ImageSourceUnavailable as error:
                attempts += 1
                if self._ask_to_try_again(error, attempts) != QMessageBox.Retry:
                    return None

    def _ask_to_try_again(self, error: ImageSourceUnavailable, attempts: int) -> int:
        reason = error.reason.rstrip(".")
        text = (
            "Plate images cannot be read from the RockMaker share.\n\n"
            f"{error.root}\n{reason[:1].upper() + reason[1:]}.\n\n"
            "XtalFlow cannot connect the share itself. Try again once it is "
            "back, or cancel."
        )
        if attempts > 1:
            # The administrator needs to know which computer lost its mount.
            text += (
                "\n\nStill unavailable. Ask the administrator to check the "
                f"RockMaker share on this computer:\n{socket.gethostname()} · "
                f"{error.root} · {reason}"
            )
        return QMessageBox.warning(
            self, "Plate images unavailable", text,
            QMessageBox.Retry | QMessageBox.Cancel,
            QMessageBox.Retry,
        )

    def _show_persistence_error(self, error: ReviewPersistenceError) -> None:
        self._set_save_status("failed")
        self.status_message_label.show_message(f"Not saved: {error}")
        QMessageBox.warning(self, "Review was not saved", str(error))

    def _run_in_background(self, message: str, task: Callable[[], T]) -> T:
        """Run slow network or share I/O without freezing the window.

        The task runs on a worker thread and must not touch Qt widgets or the
        SQLite review store. User input is excluded until it finishes, so the
        plan cannot change underneath it, while the window keeps repainting.
        """
        outcome: dict[str, object] = {}

        def work() -> None:
            try:
                outcome["result"] = task()
            except BaseException as error:  # noqa: BLE001 - re-raised on the UI thread
                outcome["error"] = error

        worker = threading.Thread(target=work, name="xtalflow-io", daemon=True)
        progress = QProgressDialog(message, None, 0, 0, self)
        progress.setWindowTitle("XtalFlow")
        progress.setWindowModality(Qt.WindowModal)
        # QProgressDialog's own delayed show ignores later setMinimumDuration
        # calls, so reveal it explicitly only when the work is not instant.
        progress.setMinimumDuration(2**31 - 1)
        reveal = QTimer()
        reveal.setSingleShot(True)
        reveal.timeout.connect(progress.show)
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: None if worker.is_alive() else loop.quit())
        worker.start()
        reveal.start(400)
        poll.start()
        loop.exec_(QEventLoop.ExcludeUserInputEvents)
        poll.stop()
        reveal.stop()
        progress.close()
        progress.deleteLater()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def handle_unexpected_error(self, error_type, error, error_traceback) -> None:
        """Report an exception raised in a Qt slot instead of letting PyQt abort."""
        if issubclass(error_type, KeyboardInterrupt):
            sys.__excepthook__(error_type, error, error_traceback)
            return
        details = "".join(
            traceback.format_exception(error_type, error, error_traceback)
        )
        print(details, file=sys.stderr)
        if self.error_log_path is not None:
            try:
                self.error_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.error_log_path.open("a", encoding="utf-8") as stream:
                    stream.write(f"{datetime.now(timezone.utc).isoformat()}\n{details}\n")
            except OSError:
                pass
        if self._handling_unexpected_error:
            return
        self._handling_unexpected_error = True
        try:
            saved = "There were no review targets to save."
            if self.controller is not None:
                try:
                    self.controller.checkpoint_current()
                    self._set_save_status("saved")
                    saved = "Targets on the current image were saved."
                except Exception:  # noqa: BLE001 - already reporting a failure
                    self._set_save_status("failed")
                    saved = "Targets on the current image could NOT be saved."
            location = (
                f"Details were written to {self.error_log_path}."
                if self.error_log_path is not None
                else "Details were written to the terminal."
            )
            QMessageBox.critical(
                self,
                "Unexpected error",
                f"{error_type.__name__}: {error}\n\n{saved}\n{location}",
            )
        finally:
            self._handling_unexpected_error = False

    def _set_save_status(self, state: str) -> None:
        styles = {
            "saved": (f"{theme.SYMBOL_OK} Saved locally", "ok"),
            "unsaved": (f"{theme.SYMBOL_UNSAVED} Unsaved changes", "attention"),
            "failed": (f"{theme.SYMBOL_ERROR} Save failed", "error"),
        }
        text, kind = styles[state]
        self.save_status_label.setText(text)
        self.save_status_label.setStyleSheet(theme.status_style(kind))

    def _update_zoom_label(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review crystal images from RMServer")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SETTINGS.rmserver_root,
        help=f"RMServer image root (default: {DEFAULT_SETTINGS.rmserver_root})",
    )
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=DEFAULT_SETTINGS.fragment_library_directory,
        help="Fragment library CSV directory",
    )
    parser.add_argument(
        "--worksheet-dir",
        type=Path,
        default=DEFAULT_SETTINGS.worksheet_staging_directory,
        help="Local worksheet staging directory",
    )
    parser.add_argument(
        "--echo-dir",
        type=Path,
        help="ECHO 650 worksheet output directory (overrides the configured echo650)",
    )
    parser.add_argument(
        "--shifter1-dir",
        type=Path,
        help="SHIFTER 1 worksheet output directory (overrides the configured shifter1)",
    )
    parser.add_argument(
        "--shifter2-dir",
        type=Path,
        help="SHIFTER 2 worksheet output directory (overrides the configured shifter2)",
    )
    parser.add_argument(
        "--allow-local-instrument-dirs",
        action="store_true",
        help="Allow ECHO/SHIFTER directories that are not mounted network shares "
        "(testing only; instruments will not see these files)",
    )
    parser.add_argument(
        "--allow-local-image-root",
        action="store_true",
        help="Allow an image root that is not a mounted network share "
        "(testing only; a local folder stands in for the RockMaker share)",
    )
    parser.add_argument(
        "--examples-dir",
        type=Path,
        help="Folder of lab-approved example images, each with a same-named .txt caption",
    )
    parser.add_argument("--plate", help="Plate code to load at startup")
    parser.add_argument(
        "--plate-format",
        choices=[plate_format.id for plate_format in PLATE_FORMATS],
        help="Plate format ID (required with --plate)",
    )
    parser.add_argument(
        "--review-db",
        type=Path,
        help="SQLite review database (default: application data directory)",
    )
    parser.add_argument("--mxlive-url", default=DEFAULT_SETTINGS.mxlive_base_url)
    parser.add_argument("--mxlive-key", type=Path, default=DEFAULT_SETTINGS.mxlive_key_path)
    parser.add_argument("--mxlive-ca", type=Path, default=DEFAULT_SETTINGS.mxlive_ca_bundle)
    parser.add_argument(
        "--mxlive-config", type=Path, default=DEFAULT_SETTINGS.mxlive_config_path,
        help="site TOML file with MxLive account mappings and [[instruments]]",
    )
    return parser


def settings_from_arguments(args: argparse.Namespace) -> ApplicationSettings:
    """Combine built-in defaults, the site TOML file, and command-line overrides."""
    settings = replace(
        DEFAULT_SETTINGS,
        rmserver_root=args.root,
        fragment_library_directory=args.library_dir,
        worksheet_staging_directory=args.worksheet_dir,
        mxlive_base_url=args.mxlive_url,
        mxlive_key_path=args.mxlive_key,
        mxlive_ca_bundle=args.mxlive_ca,
        mxlive_config_path=args.mxlive_config,
        examples_directory=args.examples_dir,
    )
    configured = load_instrument_destinations(args.mxlive_config)
    if configured is not None:
        settings = replace(settings, instruments=configured)
    for instrument_id, directory in (
        (ECHO_650, args.echo_dir),
        (SHIFTER_1, args.shifter1_dir),
        (SHIFTER_2, args.shifter2_dir),
    ):
        if directory is not None:
            settings = settings.with_instrument_directory(instrument_id, directory)
    return with_network_path_policy(
        settings, args.allow_local_instrument_dirs, args.allow_local_image_root
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = QApplication.instance() or QApplication(
        sys.argv if argv is None else [sys.argv[0], *argv]
    )
    app.setApplicationName("XtalFlow")
    app.setOrganizationName("XtalFlow")
    database_path = args.review_db or (
        Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        / DEFAULT_SETTINGS.review_database_filename
    )
    try:
        settings = settings_from_arguments(args)
    except ValueError as error:
        print(f"xtalflow-viewer: {error}", file=sys.stderr)
        return 2
    try:
        review_store = SQLiteReviewStore(database_path)
    except ReviewPersistenceError as error:
        print(f"xtalflow-viewer: {error}", file=sys.stderr)
        return 2
    window = ViewerWindow(
        RockMakerImageRepository(
            settings.rmserver_root,
            require_network_mount=settings.require_network_image_root,
        ),
        review_store,
        settings=settings,
    )
    window.error_log_path = database_path.parent / "xtalflow-errors.log"
    # PyQt5 aborts the application on an unhandled slot exception unless a
    # custom hook is installed.
    sys.excepthook = window.handle_unexpected_error
    if args.plate:
        if not args.plate_format:
            print("xtalflow-viewer: --plate-format is required with --plate", file=sys.stderr)
            window.close()
            return 2
        plate_format = plate_format_by_id(args.plate_format)
        try:
            window.load_plate(args.plate, plate_format)
        except IMAGE_SOURCE_ERRORS as error:
            print(f"xtalflow-viewer: {error}", file=sys.stderr)
            window.close()
            return 2
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
