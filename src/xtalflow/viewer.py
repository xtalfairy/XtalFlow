from __future__ import annotations

import argparse
import getpass
import re
import sys
import threading
import traceback
from collections.abc import Callable
from typing import TypeVar
from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from PyQt5.QtCore import (
    QEventLoop,
    QPoint,
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
    QCheckBox,
    QCompleter,
    QDockWidget,
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QProgressDialog,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QStackedWidget,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
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
    CrystalSelection,
    ImageCalibration,
    ImageFilter,
    PlateFormat,
    PlateImages,
    plate_format_by_id,
    ExperimentProject,
    PlanType,
    Project,
    crystal_selection_from_selected_crystals,
)
from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    FragmentLibrary,
    SelectedCrystal,
)
from xtalflow.domain.experiment_naming import suggest_experiment_id
from xtalflow.domain.labwork import build_fragment_labworks, build_raw_crystal_labworks
from xtalflow.domain.plan_lifecycle import (
    PlanningDraft,
    PlanRevision,
    WebDBUploadEvent,
)
from xtalflow.domain.mxlive import MxLiveReadError
from xtalflow.application.planning_service import (
    EXPERIMENT_ID_PREFIXES,
    PlanningService,
    PlanStatus,
    fragment_plan_snapshot,
    raw_crystal_plan_snapshot,
    restored_plan_status,
    saved_plan_status,
)
from xtalflow.application.worksheet_export import (
    CANCELLED as WORKSHEETS_CANCELLED,
    FAILED as WORKSHEETS_FAILED,
    SUCCEEDED as WORKSHEETS_SUCCEEDED,
    WorksheetExportService,
)
from xtalflow.application.labwork_upload import (
    FAILED,
    PARTIAL,
    SUCCEEDED,
    LabworkUploadService,
    UploadAvailability,
    UploadLockState,
    send_labworks,
)
from xtalflow.infrastructure import (
    LegacyMxLiveReadClient,
    LegacyMxLiveWriteClient,
    OpenCVWellDetector,
    RockMakerImageRepository,
    SQLiteReviewStore,
    latest_image_source,
)
from xtalflow.infrastructure.mxlive_client import labworks_endpoint
from xtalflow.infrastructure.fragment_library_csv import (
    FragmentLibraryCsvError,
    load_fragment_library,
)
from xtalflow.infrastructure.worksheet_exporter import (
    WorksheetDestinationUnavailable,
    WorksheetExporter,
)
from xtalflow.infrastructure.mxlive_config import (
    MxLiveConfigurationError,
    resolve_mxlive_account,
)
from xtalflow.infrastructure.user_preferences import JsonUserPreferencesStore
from xtalflow.presentation import ProjectImageSetListModel
from xtalflow.settings import (
    ApplicationSettings,
    DEFAULT_SETTINGS,
    with_instrument_output_policy,
)
from xtalflow.ui.review_widgets import (
    ImageCanvas,
    ImagePathStatusLabel,
    StatusMessageLabel,
    ImageSetListView,
)
from xtalflow.ui.plan_editors import FragmentScreeningEditor, RawCrystalEditor
from xtalflow.ui.plate_source_dialog import PlateSourceDialog


# Image sets live on the RockMaker SMB share. A dropped share raises plain OSError
# (for example "Host is down"), not only PlateImagesNotFoundError.
IMAGE_SOURCE_ERRORS = (ValueError, OSError, ReviewPersistenceError)

T = TypeVar("T")

class ViewerWindow(QMainWindow):
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
        self.planning_service = (
            PlanningService(review_store) if review_store is not None else None
        )
        self.upload_service = (
            LabworkUploadService(review_store) if review_store is not None else None
        )
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
        try:
            self.mxlive_account = resolve_mxlive_account(
                self.settings.mxlive_config_path,
                base_url=self.settings.mxlive_base_url,
                beamline=self.settings.mxlive_beamline,
                key_path=self.settings.mxlive_key_path,
                ca_bundle=self.settings.mxlive_ca_bundle,
            )
            self.mxlive_configuration_error: str | None = None
        except MxLiveConfigurationError as error:
            self.mxlive_account = None
            self.mxlive_configuration_error = str(error)
        self.project_controller = ProjectController(
            repository, review_store, self._global_auto_advance_target_count
        )
        self.plate: PlateImages | None = None
        self.controller: ReviewController | None = None
        self.calibration_service: WellCalibrationService | None = None
        self.current_calibration: ImageCalibration | None = None
        self._manual_calibration_points: list[tuple[float, float]] | None = None
        self._target_summary_window_expansion = 0
        self._planning_project_id: str | None = None
        self.error_log_path: Path | None = None
        self._mxlive_experiment_ids: set[str] = set()
        self._handling_unexpected_error = False
        self._planning_drafts: dict[
            str, list[tuple[str, FragmentScreeningEditor]]
        ] = {}
        self.setWindowTitle("XtalFlow Viewer")
        self.resize(1100, 850)

        self.project_selector = QComboBox()
        self.new_project_button = QPushButton("New Workspace")
        self.rename_project_button = QPushButton("Rename")
        self.plate_input = QLineEdit()
        self.plate_input.setPlaceholderText("Plate codes (e.g. 1070, 1100, 2070)")
        self.plate_format_input = QComboBox()
        self.plate_format_input.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.plate_format_input.setMinimumContentsLength(12)
        self.plate_format_input.addItem("Select plate format…", None)
        for plate_format in PLATE_FORMATS:
            self.plate_format_input.addItem(plate_format.display_name, plate_format)
        self.load_button = QPushButton("Load")
        self.previous_button = QPushButton("◀")
        self.previous_button.setFixedWidth(36)
        self.previous_button.setToolTip("Previous image (Left Arrow)")
        self.previous_button.setAccessibleName("Previous image")
        self.next_button = QPushButton("▶")
        self.next_button.setFixedWidth(36)
        self.next_button.setToolTip("Next image (Right Arrow)")
        self.next_button.setAccessibleName("Next image")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fit_button = QPushButton("Fit")
        self.fit_button.setToolTip("Fit image to window (0)")
        self.image_filter_input = QComboBox()
        self.image_filter_input.addItem("All images", ImageFilter.ALL)
        self.image_filter_input.addItem("With targets", ImageFilter.WITH_TARGETS)
        self.image_filter_input.addItem("Reviewed, no targets", ImageFilter.WITHOUT_TARGETS)
        self.image_filter_input.addItem("Unreviewed", ImageFilter.UNREVIEWED)
        self.well_input = QLineEdit()
        self.well_input.setMinimumWidth(90)
        self.well_input.setPlaceholderText("A01a")
        self.well_input.setToolTip(
            "Enter a visible subwell address and press Enter. Esc restores the current address."
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
        self.auto_advance_input = QSpinBox()
        self.auto_advance_input.setRange(1, 100)
        self.auto_advance_input.setValue(self._global_auto_advance_target_count)
        self.auto_advance_input.setPrefix("Targets/img: ")
        self.auto_advance_input.setToolTip(
            "Automatically move to the next image after selecting this many targets. "
            "This is not a required target count."
        )
        self.image_canvas = ImageCanvas()
        self.navigation_label = QLabel("No plate loaded")
        self.review_summary_label = QLabel(
            "Click image to focus · Left add · Right remove · ←/→ navigate"
        )
        self.save_status_label = QLabel("Not loaded")
        self.image_set_list = ImageSetListView()
        self.image_set_list.setMinimumWidth(220)
        self.image_set_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_set_model = ProjectImageSetListModel(self._target_count_for_image_set)
        self.image_set_list.setModel(self.image_set_model)
        self.move_image_set_up_button = QPushButton("Up")
        self.move_image_set_down_button = QPushButton("Down")
        self.archive_image_set_button = QPushButton("Remove")
        self.restore_image_set_button = QPushButton("Restore")
        self.project_progress_label = QLabel("Workspace: no images")
        self.target_summary_button = QPushButton("View Target Summary")
        self.calibration_label = QLabel("Well calibration: not loaded")
        self.auto_calibration_button = QPushButton("Auto Well")
        self.accept_calibration_button = QPushButton("Accept Well")
        self.accept_calibration_button.setEnabled(False)
        self.manual_calibration_button = QPushButton("Set Well (3 points)")
        self.auto_confirm_plate_checkbox = QCheckBox("Auto-confirm this plate")
        self.auto_confirm_plate_checkbox.setToolTip(
            "Automatically confirm detected wells on this plate when confidence "
            "meets the selected threshold. Plate trust lasts for this session."
        )
        self.auto_confirm_confidence_input = QSpinBox()
        self.auto_confirm_confidence_input.setRange(0, 100)
        # Lowering the threshold confirms calibrations permanently, so apply only
        # the finished value rather than intermediate digits such as 8 of 85.
        self.auto_confirm_confidence_input.setKeyboardTracking(False)
        self.auto_confirm_confidence_input.setValue(
            self.user_preferences.auto_confirm_confidence_percent
        )
        self.auto_confirm_confidence_input.setPrefix("≥ ")
        self.auto_confirm_confidence_input.setSuffix("%")
        self.auto_confirm_confidence_input.setToolTip(
            f"Saved per user in {self.preferences_store.path}"
        )
        self.status_message_label = StatusMessageLabel()
        self.image_path_status = ImagePathStatusLabel()

        project_controls = QHBoxLayout()
        project_controls.addWidget(QLabel("Workspace:"))
        project_controls.addWidget(self.project_selector, 1)
        project_controls.addWidget(self.new_project_button)
        project_controls.addWidget(self.rename_project_button)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addWidget(self.auto_advance_input)
        controls.addWidget(self.image_filter_input)
        controls.addStretch()
        navigation_controls = QHBoxLayout()
        navigation_controls.setSpacing(6)
        navigation_controls.addWidget(QLabel("Well:"))
        navigation_controls.addWidget(self.well_input)
        navigation_controls.addWidget(self.previous_button)
        navigation_controls.addWidget(self.next_button)
        controls.addLayout(navigation_controls)

        viewer_layout = QVBoxLayout()
        viewer_layout.addLayout(controls)
        viewer_layout.addWidget(self.image_canvas, 1)
        image_info_controls = QHBoxLayout()
        image_info_controls.addWidget(self.navigation_label, 1)
        image_info_controls.addWidget(self.zoom_label)
        image_info_controls.addWidget(self.fit_button)
        viewer_layout.addLayout(image_info_controls)
        viewer_layout.addWidget(self.review_summary_label)
        viewer_layout.addWidget(self.calibration_label)
        calibration_actions = QHBoxLayout()
        calibration_actions.addWidget(self.auto_confirm_plate_checkbox)
        calibration_actions.addWidget(self.auto_confirm_confidence_input)
        calibration_actions.addStretch()
        calibration_actions.addWidget(self.auto_calibration_button)
        calibration_actions.addWidget(self.accept_calibration_button)
        calibration_actions.addWidget(self.manual_calibration_button)
        viewer_layout.addLayout(calibration_actions)
        viewer_panel = QWidget()
        viewer_panel.setLayout(viewer_layout)

        sidebar_layout = QVBoxLayout()
        plate_type_row = QHBoxLayout()
        plate_type_row.addWidget(QLabel("Plate type"))
        plate_type_row.addWidget(self.plate_format_input, 1)
        sidebar_layout.addLayout(plate_type_row)
        plate_load_row = QHBoxLayout()
        plate_load_row.addWidget(QLabel("Plate codes"))
        plate_load_row.addWidget(self.plate_input, 1)
        plate_load_row.addWidget(self.load_button)
        sidebar_layout.addLayout(plate_load_row)
        sidebar_layout.addWidget(self.target_summary_button)
        sidebar_layout.addWidget(self.image_set_list, 1)
        sidebar_layout.addWidget(self.project_progress_label)
        image_set_actions = QHBoxLayout()
        image_set_actions.addWidget(self.move_image_set_up_button)
        image_set_actions.addWidget(self.move_image_set_down_button)
        image_set_actions.addWidget(self.archive_image_set_button)
        image_set_actions.addWidget(self.restore_image_set_button)
        sidebar_layout.addLayout(image_set_actions)
        sidebar = QWidget()
        sidebar.setMinimumWidth(230)
        sidebar.setLayout(sidebar_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(sidebar)
        splitter.addWidget(viewer_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 850])

        review_tab = QWidget()
        review_tab_layout = QVBoxLayout()
        review_tab_layout.setContentsMargins(0, 0, 0, 0)
        review_tab_layout.addWidget(splitter)
        review_tab.setLayout(review_tab_layout)

        self.plan_list = QListWidget()
        self.plan_list.setMinimumWidth(210)
        self.plan_list.setToolTip(
            "Experiment projects: each owns a selected-well snapshot and one plan"
        )
        self.plan_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.new_plan_button = QPushButton("+ New Project")
        self.plan_list_empty_label = QLabel(
            "No experiment projects yet.\nCreate one from the current selection."
        )
        self.plan_list_empty_label.setAlignment(Qt.AlignCenter)
        self.plan_list_empty_label.setStyleSheet("color: #666")
        plan_sidebar_layout = QVBoxLayout()
        plan_sidebar_layout.addWidget(QLabel("Experiment Projects"))
        plan_sidebar_layout.addWidget(self.new_plan_button)
        plan_sidebar_layout.addWidget(self.plan_list_empty_label)
        plan_sidebar_layout.addWidget(self.plan_list, 1)
        plan_sidebar = QWidget()
        plan_sidebar.setLayout(plan_sidebar_layout)

        self.plan_stack = QStackedWidget()
        planning_placeholder = QLabel(
            "Create a Project to snapshot selected wells and apply one plan."
        )
        planning_placeholder.setAlignment(Qt.AlignCenter)
        self.plan_stack.addWidget(planning_placeholder)
        planning_splitter = QSplitter(Qt.Horizontal)
        planning_splitter.addWidget(plan_sidebar)
        planning_splitter.addWidget(self.plan_stack)
        planning_splitter.setStretchFactor(1, 1)
        planning_splitter.setSizes([230, 870])
        planning_tab = QWidget()
        planning_tab_layout = QVBoxLayout()
        planning_tab_layout.setContentsMargins(0, 0, 0, 0)
        planning_tab_layout.addWidget(planning_splitter)
        planning_tab.setLayout(planning_tab_layout)

        self.main_tabs = QTabWidget()
        self.image_review_tab_index = self.main_tabs.addTab(
            review_tab, "Image Review"
        )
        self.planning_tab_index = self.main_tabs.addTab(planning_tab, "Planning")

        layout = QVBoxLayout()
        layout.addLayout(project_controls)
        layout.addWidget(self.main_tabs, 1)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.target_summary_table = QTableWidget(0, 7)
        self.target_summary_table.setHorizontalHeaderLabels(
            (
                "Plate",
                "Well",
                "Target",
                "X (mm)",
                "Y (mm)",
                "Calibration",
                "Status",
            )
        )
        self.target_summary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.target_summary_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.target_summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.target_summary_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.target_summary_table.verticalHeader().setVisible(False)
        self.target_summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.target_summary_table.horizontalHeader().setStretchLastSection(True)
        self.target_summary_status_label = QLabel("Ready 0 · Warnings 0")
        self.target_summary_filter = QComboBox()
        self.target_summary_filter.addItem("All targets", "all")
        self.target_summary_filter.addItem("Warnings only", "warnings")
        target_summary_controls = QHBoxLayout()
        target_summary_controls.addWidget(self.target_summary_status_label, 1)
        target_summary_controls.addWidget(self.target_summary_filter)
        self.remove_targets_button = QPushButton("Remove Selected")
        self.accept_valid_auto_wells_button = QPushButton(
            "Accept Valid Auto Wells"
        )
        target_summary_layout = QVBoxLayout()
        target_summary_layout.setContentsMargins(0, 0, 0, 0)
        target_summary_layout.addLayout(target_summary_controls)
        target_summary_layout.addWidget(self.target_summary_table, 1)
        target_summary_layout.addWidget(self.accept_valid_auto_wells_button)
        target_summary_layout.addWidget(self.remove_targets_button)
        target_summary_panel = QWidget()
        target_summary_panel.setLayout(target_summary_layout)
        self.target_summary_dock = QDockWidget("Target Summary", self)
        self.target_summary_dock.setObjectName("target_summary_dock")
        self.target_summary_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.target_summary_dock.setWidget(target_summary_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.target_summary_dock)
        self.target_summary_dock.hide()
        self.view_menu = self.menuBar().addMenu("View")
        self.target_summary_action = self.target_summary_dock.toggleViewAction()
        self.target_summary_action.setText("Target Summary")
        self.view_menu.addAction(self.target_summary_action)

        self.statusBar().addWidget(self.status_message_label)
        self.statusBar().addPermanentWidget(self.save_status_label)
        self.statusBar().addPermanentWidget(self.image_path_status, 1)
        self.statusBar().show()

        self.load_button.clicked.connect(self.load_entered_plate)
        self.target_summary_button.clicked.connect(self.target_summary_dock.show)
        self.new_plan_button.clicked.connect(self._show_new_plan_menu)
        self.plan_list.currentRowChanged.connect(
            self._planning_row_changed
        )
        self.plan_list.customContextMenuRequested.connect(
            self._show_plan_context_menu
        )
        self.main_tabs.currentChanged.connect(self._main_tab_changed)
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
        self.accept_valid_auto_wells_button.clicked.connect(
            self._accept_valid_auto_wells
        )
        self.target_summary_filter.currentIndexChanged.connect(
            self._refresh_target_summary
        )
        self._delete_targets_shortcut = QShortcut(
            QKeySequence("Delete"), self.target_summary_table
        )
        self._delete_targets_shortcut.setContext(Qt.WidgetShortcut)
        self._delete_targets_shortcut.activated.connect(self._remove_selected_targets)
        self._backspace_targets_shortcut = QShortcut(
            QKeySequence("Backspace"), self.target_summary_table
        )
        self._backspace_targets_shortcut.setContext(Qt.WidgetShortcut)
        self._backspace_targets_shortcut.activated.connect(
            self._remove_selected_targets
        )
        self.new_project_button.clicked.connect(self.create_project_interactively)
        self.rename_project_button.clicked.connect(self.rename_project_interactively)
        self.project_selector.currentIndexChanged.connect(self._project_selected)
        self.image_set_list.clicked.connect(self._image_set_selected)
        self.image_set_list.customContextMenuRequested.connect(
            self._show_image_set_context_menu
        )
        self.move_image_set_up_button.clicked.connect(
            lambda: self._move_selected_image_set(-1)
        )
        self.move_image_set_down_button.clicked.connect(
            lambda: self._move_selected_image_set(1)
        )
        self.archive_image_set_button.clicked.connect(self._archive_selected_image_set)
        self.restore_image_set_button.clicked.connect(self._restore_archived_image_set)
        self.plate_input.returnPressed.connect(self.load_entered_plate)
        self.previous_button.clicked.connect(self.show_previous)
        self.next_button.clicked.connect(self.show_next)
        self.fit_button.clicked.connect(self.image_canvas.fit_image)
        self.image_canvas.zoom_changed.connect(self._update_zoom_label)
        self.image_canvas.image_clicked.connect(self._handle_image_click)
        self.image_canvas.previous_requested.connect(self.show_previous)
        self.image_canvas.next_requested.connect(self.show_next)
        self.image_canvas.previous_plate_requested.connect(
            lambda: self._switch_active_image_set(-1)
        )
        self.image_canvas.next_plate_requested.connect(
            lambda: self._switch_active_image_set(1)
        )
        self.image_set_list.previous_plate_requested.connect(
            lambda: self._switch_active_image_set(-1)
        )
        self.image_set_list.next_plate_requested.connect(
            lambda: self._switch_active_image_set(1)
        )
        self.auto_advance_input.valueChanged.connect(self._change_auto_advance_target_count)
        self.auto_calibration_button.clicked.connect(self._auto_detect_calibration)
        self.accept_calibration_button.clicked.connect(
            self._accept_current_calibration
        )
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
        self._update_navigation()
        self._initialize_projects()
        self._show_planning_migration_status()

    def _show_planning_migration_status(self) -> None:
        if self.review_store is None:
            return
        report = self.review_store.planning_project_migration
        details: list[str] = []
        if report.migrated:
            details.append(
                f"Migrated {report.migrated} finalized legacy project(s)"
            )
        if report.legacy_drafts_without_revision:
            details.append(
                f"{report.legacy_drafts_without_revision} legacy draft(s) still "
                "need selection review"
            )
        if report.invalid_snapshots:
            details.append(
                f"{len(report.invalid_snapshots)} invalid finalized snapshot(s) "
                "left unchanged"
            )
            details.extend(
                f"{plan_id}: {error}"
                for plan_id, error in report.invalid_snapshots
            )
        if self.review_store.upgrade_backup_path is not None:
            details.insert(
                0,
                "Review database upgraded · backup saved to "
                f"{self.review_store.upgrade_backup_path}",
            )
        if details:
            self.status_message_label.show_message(" · ".join(details))

    def _open_fragment_screening(self) -> None:
        crystals = self._crystals_for_new_plan("fragment plan")
        if crystals is not None:
            self._add_fragment_plan(None, crystals)

    def _open_raw_crystal_plan(self) -> None:
        crystals = self._crystals_for_new_plan("raw crystal plan")
        if crystals is not None:
            self._add_raw_crystal_plan(crystals)

    def _crystals_for_new_plan(
        self, plan_label: str
    ) -> tuple[SelectedCrystal, ...] | None:
        try:
            crystals = self.project_controller.selected_crystals_for_plan()
            if not crystals:
                raise ValueError("select at least one crystal target first")
        except IMAGE_SOURCE_ERRORS as error:
            try:
                candidate_count = (
                    self.project_controller
                    .valid_unconfirmed_automatic_calibration_count()
                )
                warning_count = sum(
                    not summary.is_ready
                    for summary in self.project_controller.project_target_summaries()
                )
            except IMAGE_SOURCE_ERRORS:
                candidate_count = 0
                warning_count = 0
            if not warning_count:
                QMessageBox.warning(self, f"Cannot create {plan_label}", str(error))
                return None
            action = self._planning_calibration_action(
                candidate_count, warning_count
            )
            if action == "review":
                self._review_target_warnings()
                return None
            if action == "accept":
                try:
                    self.project_controller.confirm_valid_automatic_calibrations()
                    self._adopt_active_review()
                    self._sync_project_widgets()
                    crystals = self.project_controller.selected_crystals_for_plan()
                except IMAGE_SOURCE_ERRORS as retry_error:
                    remaining = sum(
                        not summary.is_ready
                        for summary in self.project_controller.project_target_summaries()
                    )
                    if remaining:
                        retry_action = self._planning_calibration_action(0, remaining)
                        if retry_action == "review":
                            self._review_target_warnings()
                    else:
                        QMessageBox.warning(
                            self, f"Cannot create {plan_label}", str(retry_error)
                        )
                    return None
            else:
                return None
        return crystals

    def _show_new_plan_menu(self) -> None:
        menu = QMenu(self.new_plan_button)
        raw_action = menu.addAction("Raw Crystal Plan")
        fragment_action = menu.addAction("Fragment Screening")
        menu.addSeparator()
        for label in (
            "Solvent Duration (coming later)",
            "Cryo Plan (coming later)",
            "Custom Soaking (coming later)",
        ):
            menu.addAction(label).setEnabled(False)
        fragment_action.triggered.connect(self._open_fragment_screening)
        raw_action.triggered.connect(self._open_raw_crystal_plan)
        menu.exec_(
            self.new_plan_button.mapToGlobal(
                self.new_plan_button.rect().bottomLeft()
            )
        )

    def _planning_row_changed(self, row: int) -> None:
        self.plan_stack.setCurrentIndex(max(0, row + 1))

    def _show_plan_context_menu(self, position: QPoint) -> None:
        row = self.plan_list.indexAt(position).row()
        if row < 0:
            return
        self.plan_list.setCurrentRow(row)
        menu = QMenu(self.plan_list)
        delete_action = menu.addAction("Delete Project…")
        project = self.project_controller.active_project
        drafts = self._planning_drafts.get(project.id, []) if project else []
        if row >= len(drafts):
            delete_action.setEnabled(False)
        else:
            _, editor = drafts[row]
            try:
                has_uploads = (
                    self.review_store is not None
                    and self.review_store.planning_plan_has_upload_history(
                        editor.plan_id
                    )
                )
            except ReviewPersistenceError as error:
                has_uploads = True
                delete_action.setToolTip(str(error))
            delete_action.setEnabled(not has_uploads)
            if has_uploads and not delete_action.toolTip():
                delete_action.setToolTip(
                    "Projects with MxLive upload history cannot be deleted"
                )
        delete_action.triggered.connect(self._delete_selected_draft_plan)
        menu.exec_(self.plan_list.viewport().mapToGlobal(position))

    def _delete_selected_draft_plan(self) -> None:
        project = self.project_controller.active_project
        row = self.plan_list.currentRow()
        drafts = self._planning_drafts.get(project.id, []) if project else []
        if row < 0 or row >= len(drafts):
            return
        name, editor = drafts[row]
        answer = QMessageBox.question(
            self,
            "Delete experiment project",
            f"Permanently delete Project '{name}'?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if hasattr(editor, "autosave_timer"):
            editor.autosave_timer.stop()
        if self.review_store is not None:
            try:
                self.review_store.delete_planning_draft(editor.plan_id)
            except (ValueError, ReviewPersistenceError) as error:
                QMessageBox.warning(self, "Cannot delete plan", str(error))
                return
        drafts.pop(row)
        self.plan_list.takeItem(row)
        self.plan_stack.removeWidget(editor)
        editor.deleteLater()
        if self.plan_list.count() == 0:
            self.plan_list_empty_label.show()
            self.plan_stack.setCurrentIndex(0)
        else:
            self.plan_list.setCurrentRow(min(row, self.plan_list.count() - 1))
        self._refresh_project_well_usage()

    def _set_editor_well_usage(self, editor) -> None:
        if (
            self.review_store is None
            or not getattr(editor, "selection_snapshot_owned", False)
        ):
            editor.set_well_usage({})
            return
        image_keys = tuple(well.image_key for well in editor.selection.wells)
        try:
            usage = self.review_store.prior_selected_well_usage(
                editor.plan_id, image_keys
            )
        except ReviewPersistenceError as error:
            editor.error_label.setText(f"Reuse history unavailable: {error}")
            return
        editor.set_well_usage(usage)

    def _refresh_project_well_usage(self) -> None:
        project = self.project_controller.active_project
        if project is None:
            return
        for _, editor in self._planning_drafts.get(project.id, []):
            self._set_editor_well_usage(editor)

    def _add_fragment_plan(
        self,
        library: FragmentLibrary | None,
        crystals: tuple[SelectedCrystal, ...],
        restored: PlanningDraft | None = None,
    ) -> None:
        project = self._require_planning_workspace()
        existing = self._planning_drafts.setdefault(project.id, [])
        name = (
            restored.name if restored
            else f"Fragment Screening Project #{len(existing) + 1}"
        )
        plan_id = restored.id if restored else str(uuid4())
        owned_project, selection = self._plan_selection(plan_id, crystals)
        editor = FragmentScreeningEditor(library, selection, self.plan_stack)
        editor.set_library_choices(self._fragment_library_choices())
        if restored is not None:
            editor.restore_draft(restored)
        editor.refresh_libraries_button.setToolTip(
            str(self.settings.fragment_library_directory)
        )
        editor.library_refresh_requested.connect(
            self._refresh_fragment_library_choices
        )
        editor.save_worksheets_requested.connect(
            lambda selected_editor=editor: self._save_plan_worksheets(selected_editor)
        )
        self._register_plan_editor(
            editor, PlanType.FRAGMENT_SCREENING, name, plan_id, restored, owned_project
        )

    def _add_raw_crystal_plan(
        self, crystals: tuple[SelectedCrystal, ...],
        restored: PlanningDraft | None = None,
    ) -> None:
        project = self._require_planning_workspace()
        existing = self._planning_drafts.setdefault(project.id, [])
        name = (
            restored.name if restored
            else "Raw Crystal Project #"
            f"{sum(isinstance(item[1], RawCrystalEditor) for item in existing) + 1}"
        )
        plan_id = restored.id if restored else str(uuid4())
        owned_project, selection = self._plan_selection(plan_id, crystals)
        editor = RawCrystalEditor(selection, self.plan_stack)
        if restored is not None:
            editor.restore_draft(restored)
        editor.save_worksheet_requested.connect(
            lambda selected_editor=editor: self._save_plan_worksheets(selected_editor)
        )
        self._register_plan_editor(
            editor, PlanType.RAW_CRYSTAL, name, plan_id, restored, owned_project
        )

    def _require_planning_workspace(self) -> Project:
        project = self.project_controller.active_project
        if project is None:
            raise ValueError("no project is open")
        return project

    def _plan_selection(
        self, plan_id: str, crystals: tuple[SelectedCrystal, ...]
    ) -> tuple[ExperimentProject | None, CrystalSelection]:
        owned_project = (
            self.review_store.load_experiment_project(plan_id)
            if self.review_store is not None else None
        )
        selection = (
            owned_project.crystal_selection
            if owned_project is not None
            else crystal_selection_from_selected_crystals(plan_id, crystals)
        )
        return owned_project, selection

    def _register_plan_editor(
        self,
        editor,
        plan_type: PlanType,
        name: str,
        plan_id: str,
        restored: PlanningDraft | None,
        owned_project: ExperimentProject | None,
    ) -> None:
        """Attach plan identity, autosave, and lifecycle actions shared by plan types."""
        project = self._require_planning_workspace()
        editor.plan_type = plan_type
        editor.plan_id = plan_id
        editor.project_id = project.id
        editor.plan_name = name
        editor.plan_created_at = restored.created_at if restored else datetime.now(timezone.utc)
        editor.last_revision = None
        editor.last_revision_snapshot = None
        editor.selection_snapshot_owned = owned_project is not None or restored is None
        editor.autosave_timer = QTimer(editor)
        editor.autosave_timer.setSingleShot(True)
        editor.autosave_timer.setInterval(750)
        editor.autosave_timer.timeout.connect(
            lambda selected_editor=editor: self._persist_draft(selected_editor)
        )
        editor.set_experiment_id_provider(
            lambda protein, selected_type=plan_type: self._suggest_experiment_id(
                selected_type, protein
            )
        )
        if self.mxlive_account is not None:
            editor.set_mxlive_account(self.mxlive_account)
        elif self.mxlive_configuration_error:
            editor.webdb_status_label.setText(
                f"{self.mxlive_configuration_error} · "
                f"{editor.webdb_table.rowCount()} preview records"
            )
        editor.finalize_requested.connect(
            lambda selected_editor=editor: self._finalize_plan(selected_editor)
        )
        editor.webdb_upload_requested.connect(
            lambda selected_editor=editor: self._upload_plan_labworks(selected_editor)
        )
        editor.adopt_selection_requested.connect(
            lambda selected_editor=editor: self._adopt_legacy_selection(selected_editor)
        )
        editor.draft_changed.connect(
            lambda selected_editor=editor: self._plan_draft_changed(selected_editor)
        )
        self._planning_drafts.setdefault(project.id, []).append((name, editor))
        self.plan_stack.addWidget(editor)
        self.plan_list.addItem(name)
        self.plan_list_empty_label.hide()
        self.plan_list.setCurrentRow(self.plan_list.count() - 1)
        if restored is None:
            self.main_tabs.setCurrentIndex(self.planning_tab_index)
            self._save_selection_snapshot(editor, editor.selection)
            self._persist_draft(editor)
        elif self.planning_service is not None:
            self._restore_plan_lifecycle(editor)
        self._refresh_project_well_usage()

    def _restore_plan_lifecycle(self, editor) -> None:
        editor.last_revision = self.planning_service.latest_revision(editor.plan_id)
        if editor.last_revision is not None:
            editor.last_revision_snapshot = editor.last_revision.snapshot_json
        snapshot = self._plan_snapshot(editor)
        status = restored_plan_status(
            editor.last_revision, snapshot, editor.selection_snapshot_owned
        )
        if (
            editor.last_revision is not None
            and snapshot == editor.last_revision_snapshot
        ):
            self._sync_webdb_upload_state(editor)
        if not editor.selection_snapshot_owned:
            editor.adopt_selection_button.show()
        if status is not None:
            self._show_plan_status(editor, status)

    def _plan_draft_changed(self, editor) -> None:
        if not hasattr(editor, "autosave_timer"):
            return
        editor.lifecycle_label.setText("Draft · saving…")
        editor.webdb_upload_button.setEnabled(False)
        self._set_plan_list_status(editor, "Draft")
        editor.autosave_timer.start()

    def _adopt_legacy_selection(self, editor) -> None:
        try:
            crystals = self.project_controller.selected_crystals_for_plan()
            if not crystals:
                raise ValueError("select at least one well first")
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot adopt selection", str(error))
            return
        well_count = len(crystals)
        position_count = sum(len(crystal.targets) for crystal in crystals)
        if QMessageBox.question(
            self,
            "Adopt current selection",
            f"Replace this legacy draft's unfixed selection with the current "
            f"{well_count} selected well(s) and {position_count} soaking "
            "position(s)?\n\nFuture Image Review changes will not alter it.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        selection = crystal_selection_from_selected_crystals(
            editor.plan_id,
            crystals,
            created_at=editor.plan_created_at,
        )
        editor.set_selection(selection)
        if not self._save_selection_snapshot(editor, selection):
            return
        editor.selection_snapshot_owned = True
        editor.adopt_selection_button.hide()
        editor.lifecycle_label.setText("Draft · selection fixed")
        self._set_plan_list_status(editor, "Draft · Selection fixed")
        self._refresh_project_well_usage()
        self._persist_draft(editor)

    def _save_selection_snapshot(self, editor, selection: CrystalSelection) -> bool:
        if self.planning_service is None:
            return True
        try:
            self.planning_service.save_selection_snapshot(
                editor.plan_id, editor.plan_name, editor.plan_type, selection,
                editor.plan_created_at,
            )
        except ReviewPersistenceError as error:
            editor.selection_snapshot_owned = False
            self._show_persistence_error(error)
            return False
        return True

    @staticmethod
    def _plan_snapshot(editor) -> str | None:
        plan = editor.current_plan
        if plan is None:
            return None
        protein = editor.protein_input.text()
        if editor.plan_type is PlanType.RAW_CRYSTAL:
            return raw_crystal_plan_snapshot(plan, protein)
        return fragment_plan_snapshot(
            plan, protein, editor.library_input.currentData(Qt.UserRole),
            editor.rows_input.text(),
        )

    @staticmethod
    def _draft_from_editor(editor) -> PlanningDraft:
        now = datetime.now(timezone.utc)
        if editor.plan_type is PlanType.RAW_CRYSTAL:
            return PlanningDraft(
                editor.plan_id, editor.project_id, PlanType.RAW_CRYSTAL.value,
                editor.plan_name, None, "", editor.protein_input.text(), "0",
                editor.order_input.currentData().value, editor.plan_created_at, now,
                editor.assigned_experiment_id,
            )
        return PlanningDraft(
            editor.plan_id, editor.project_id, PlanType.FRAGMENT_SCREENING.value,
            editor.plan_name, editor.library_input.currentData(Qt.UserRole),
            editor.rows_input.text(), editor.protein_input.text(),
            str(editor.volume_input.value()), editor.order_input.currentData().value,
            editor.plan_created_at, now, editor.assigned_experiment_id,
        )

    def _persist_draft(self, editor) -> None:
        if self.planning_service is None:
            editor.lifecycle_label.setText("Draft · memory only")
            return
        try:
            self.planning_service.save_draft(self._draft_from_editor(editor))
        except ReviewPersistenceError as error:
            editor.lifecycle_label.setText("Draft · save failed")
            self._show_persistence_error(error)
            return
        status = saved_plan_status(
            editor.last_revision, editor.last_revision_snapshot,
            self._plan_snapshot(editor),
        )
        self._show_plan_status(editor, status)
        if status.finalized:
            self._sync_webdb_upload_state(editor)

    def _show_plan_status(self, editor, status: PlanStatus) -> None:
        editor.lifecycle_label.setText(status.label)
        self._set_plan_list_status(editor, status.list_status)

    def _suggest_experiment_id(self, plan_type: PlanType, protein: str) -> str:
        if self.planning_service is None:
            return suggest_experiment_id(
                EXPERIMENT_ID_PREFIXES[plan_type], protein, self._mxlive_experiment_ids
            )
        return self.planning_service.suggest_experiment_id(
            plan_type, protein, self._mxlive_experiment_ids
        )

    def _finalize_plan(self, editor) -> PlanRevision | None:
        if self.planning_service is None:
            QMessageBox.warning(self, "Cannot finalize plan", "Open a writable review database first.")
            return None
        snapshot = self._plan_snapshot(editor)
        if snapshot is None:
            plan_kind = (
                "raw crystal" if editor.plan_type is PlanType.RAW_CRYSTAL else "fragment"
            )
            QMessageBox.warning(
                self, "Cannot finalize plan", f"The {plan_kind} plan is not valid."
            )
            return None
        self._persist_draft(editor)
        if snapshot == editor.last_revision_snapshot:
            return editor.last_revision
        if (
            editor.assigned_experiment_id is None
            and not self._check_new_experiment_id_against_mxlive()
        ):
            return None
        try:
            revision = self.planning_service.finalize(
                editor.plan_id, editor.plan_type, editor.protein_input.text(),
                snapshot, editor.assigned_experiment_id, getpass.getuser(),
                self._mxlive_experiment_ids,
            )
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot finalize plan", str(error))
            return None
        editor.last_revision = revision
        editor.assigned_experiment_id = revision.experiment_id
        editor.last_revision_snapshot = snapshot
        editor._refresh_experiment_id()
        self._persist_draft(editor)
        self._sync_webdb_upload_state(editor)
        self._refresh_project_well_usage()
        return revision

    def _set_plan_list_status(self, editor, status: str) -> None:
        editor.plan_list_status = status
        project = self.project_controller.active_project
        if project is None or project.id != self._planning_project_id:
            return
        for index, (_, candidate) in enumerate(self._planning_drafts.get(project.id, [])):
            if candidate is editor and index < self.plan_list.count():
                self.plan_list.item(index).setText(f"{editor.plan_name} · {status}")
                return

    def _sync_webdb_upload_state(self, editor, failure: str = "") -> None:
        editor.webdb_upload_button.setEnabled(False)
        editor.webdb_upload_button.setText("Upload Finalized Revision…")
        self._set_webdb_upload_state(editor, failure)
        account = self.mxlive_account
        revision = getattr(editor, "last_revision", None)
        if account is None or revision is None or not account.upload_ready:
            return
        lock = self._webdb_upload_lock(revision.experiment_id)
        if lock is None:
            return
        if not lock.can_upload:
            state = {
                UploadAvailability.UPLOADED: "Uploaded",
                UploadAvailability.PARTIAL: "Partial upload · review MxLive",
                UploadAvailability.NEEDS_VERIFICATION:
                    "Upload result unknown · verify on MxLive",
            }[lock.availability]
            if lock.event is not None and lock.event.revision_id != revision.id:
                state += " (earlier revision)"
            self._set_webdb_upload_state(editor, state)
            if lock.can_verify:
                editor.webdb_upload_button.setText("Verify on MxLive…")
                editor.webdb_upload_button.setEnabled(True)
                editor.webdb_upload_button.setToolTip(
                    "Check which records MxLive stored before allowing another upload."
                )
            else:
                editor.webdb_upload_button.setToolTip(
                    "This experiment ID is already in MxLive, which cannot update "
                    "or delete labworks."
                )
            return
        if self._plan_snapshot(editor) != editor.last_revision_snapshot:
            return
        editor.webdb_upload_button.setEnabled(True)
        editor.webdb_upload_button.setToolTip(
            "Upload this exact finalized revision to MxLive labworks."
        )

    @staticmethod
    def _set_webdb_upload_state(editor, state: str) -> None:
        # Stored on the editor so preview refreshes keep showing the upload state.
        if editor.webdb_upload_state != state:
            editor.webdb_upload_state = state
            editor._refresh_webdb_preview()

    def _webdb_upload_lock(self, experiment_id: str) -> UploadLockState | None:
        """Return None when upload history is unreadable, which blocks uploading."""
        if self.upload_service is None:
            return UploadLockState(UploadAvailability.AVAILABLE)
        try:
            return self.upload_service.lock_state(experiment_id)
        except ReviewPersistenceError as error:
            self.status_message_label.show_message(
                f"Upload history unavailable: {error}"
            )
            return None

    def _upload_plan_labworks(self, editor) -> None:
        if editor.current_plan is None:
            return
        build_labworks = (
            build_raw_crystal_labworks
            if editor.plan_type is PlanType.RAW_CRYSTAL
            else build_fragment_labworks
        )
        self._upload_labworks(
            editor,
            build_labworks(
                editor.current_plan,
                experiment_id=editor.last_revision.experiment_id
                if editor.last_revision else "",
                protein_name=editor.protein_input.text().strip(),
                username=self.mxlive_account.username if self.mxlive_account else "",
                account_id=self.mxlive_account.account_id if self.mxlive_account else "",
            ),
        )

    def _upload_labworks(self, editor, records: tuple) -> None:
        account = self.mxlive_account
        revision = getattr(editor, "last_revision", None)
        if account is None or revision is None:
            QMessageBox.warning(
                self, "Cannot upload", "Finalize the current plan revision first."
            )
            return
        if not account.upload_ready:
            QMessageBox.warning(
                self, "Cannot upload", "\n".join(account.upload_blockers)
            )
            return
        if self.upload_service is None:
            QMessageBox.warning(
                self, "Cannot upload",
                "Open a writable review database so the upload can be audited.",
            )
            return
        lock = self._webdb_upload_lock(revision.experiment_id)
        if lock is None:
            QMessageBox.warning(
                self, "Cannot upload",
                "Upload history could not be read, so a duplicate upload cannot be "
                "ruled out.",
            )
            return
        if lock.can_verify and lock.event is not None:
            self._verify_labworks_upload(editor, revision.experiment_id, lock.event)
            return
        if not lock.can_upload:
            QMessageBox.information(
                self, "Already uploaded",
                f"{revision.experiment_id} is already in MxLive. MxLive cannot update "
                "labworks, so it will not be uploaded again.",
            )
            self._sync_webdb_upload_state(editor)
            return
        if self._plan_snapshot(editor) != editor.last_revision_snapshot:
            QMessageBox.warning(
                self, "Cannot upload", "Finalize the current plan revision first."
            )
            return
        endpoint = labworks_endpoint(account.base_url, account.beamline)
        answer = QMessageBox.question(
            self,
            "Upload finalized revision",
            f"Upload {len(records)} records for {revision.experiment_id}?\n\n"
            f"Account: {account.username}\n"
            f"API project_id: {account.account_id}\n"
            f"Endpoint: {endpoint}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        payload = tuple(record.to_payload() for record in records)
        try:
            pending = self.upload_service.begin(
                revision, account.username, account.account_id, endpoint, payload
            )
        except ReviewPersistenceError as error:
            QMessageBox.critical(
                self, "Upload was not sent",
                f"The upload audit record could not be saved:\n{error}",
            )
            return

        def upload():
            client = LegacyMxLiveWriteClient(
                account.base_url, account.beamline, account.username,
                account.key_path, ca_bundle=account.ca_bundle,
                timeout_seconds=self.settings.mxlive_timeout_seconds,
            )
            return client.upload_labworks(payload)

        outcome = self._run_in_background(
            f"Uploading {len(records)} records to MxLive…",
            lambda: send_labworks(upload),
        )
        try:
            self.upload_service.complete(pending, outcome)
        except ReviewPersistenceError as error:
            QMessageBox.critical(
                self, "Upload audit could not be saved",
                "MxLive may have accepted the upload, but its result could not be "
                f"saved:\n{error}\n\nVerify on MxLive before uploading again.",
            )
            self._sync_webdb_upload_state(editor)
            return
        if outcome.status == FAILED:
            self._sync_webdb_upload_state(editor, "Last upload failed")
            QMessageBox.critical(
                self, "WebDB upload failed", outcome.error_message or "Unknown error"
            )
            return
        self._sync_webdb_upload_state(editor)
        self._refresh_project_well_usage()
        if outcome.status == SUCCEEDED:
            QMessageBox.information(
                self, "WebDB upload complete",
                f"Uploaded {len(records)} records for {revision.experiment_id}.",
            )
        elif outcome.status == PARTIAL:
            QMessageBox.critical(
                self, "WebDB upload partially completed",
                outcome.error_message or "Some records may have been uploaded.",
            )
        else:
            QMessageBox.critical(
                self, "WebDB upload result unknown",
                f"{outcome.error_message}\n\nMxLive may have stored these records. "
                "Use Verify on MxLive before uploading again.",
            )

    def _verify_labworks_upload(
        self, editor, experiment_id: str, event: WebDBUploadEvent
    ) -> None:
        account = self.mxlive_account
        if account is None or not account.upload_ready or self.upload_service is None:
            QMessageBox.warning(
                self, "Cannot verify upload", "MxLive access is not configured."
            )
            return
        try:
            reader = self._mxlive_reader()
            labworks = self._run_in_background(
                f"Checking {experiment_id} on MxLive…",
                lambda: reader.labworks(experiment_id),
            )
        except (MxLiveReadError, ValueError) as error:
            QMessageBox.warning(self, "Could not verify upload", str(error))
            return
        try:
            verified = self.upload_service.verify(event, experiment_id, labworks)
        except ReviewPersistenceError as error:
            QMessageBox.warning(self, "Could not record verification", str(error))
            return
        self._sync_webdb_upload_state(editor)
        self._refresh_project_well_usage()
        if verified.status == FAILED:
            QMessageBox.information(
                self, "Upload not found on MxLive",
                f"MxLive has no records for {experiment_id}. You can upload again.",
            )
        elif verified.status == SUCCEEDED:
            QMessageBox.information(
                self, "Upload verified",
                f"MxLive has all {event.record_count} records for {experiment_id}.",
            )
        else:
            QMessageBox.critical(
                self, "Upload incomplete on MxLive",
                f"{verified.error_message}\n\nReview {experiment_id} in WebDB before "
                "taking further action.",
            )

    def _fragment_library_choices(
        self,
    ) -> tuple[tuple[str, str, FragmentLibrary], ...]:
        choices = []
        library_directory = self.settings.fragment_library_directory
        if not library_directory.is_dir():
            return ()
        for path in sorted(
            library_directory.glob("*.csv"),
            key=lambda item: item.name.casefold(),
        ):
            try:
                library = load_fragment_library(path)
            except FragmentLibraryCsvError:
                continue
            choices.append(
                (
                    str(path.resolve()),
                    f"{path.name} · {len(library.fragments)} rows",
                    library,
                )
            )
        return tuple(choices)

    def _refresh_fragment_library_choices(self) -> None:
        choices = self._fragment_library_choices()
        for drafts in self._planning_drafts.values():
            for _, draft_editor in drafts:
                if isinstance(draft_editor, FragmentScreeningEditor):
                    draft_editor.set_library_choices(choices)
        self.status_message_label.show_message(
            f"Found {len(choices)} libraries in "
            f"{self.settings.fragment_library_directory}",
            4000,
        )

    def _mxlive_reader(self) -> LegacyMxLiveReadClient:
        account = self.mxlive_account
        return LegacyMxLiveReadClient(
            account.base_url, account.beamline, account.username,
            account.key_path, ca_bundle=account.ca_bundle,
            timeout_seconds=self.settings.mxlive_timeout_seconds,
        )

    def _check_new_experiment_id_against_mxlive(self) -> bool:
        """Load IDs other users already uploaded; False cancels finalization."""
        account = self.mxlive_account
        if account is None or not account.upload_ready:
            return True
        try:
            reader = self._mxlive_reader()
            remote = self._run_in_background(
                "Checking experiment IDs on MxLive…",
                lambda: reader.experiment_ids(datetime.now().year),
            )
        except (MxLiveReadError, ValueError) as error:
            answer = QMessageBox.question(
                self,
                "Experiment ID not checked",
                f"Existing experiment IDs could not be read from MxLive:\n{error}\n\n"
                "Finalize with an ID checked only against this computer? It may "
                "match another researcher's experiment.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return answer == QMessageBox.Yes
        self._mxlive_experiment_ids.update(remote)
        return True

    def _save_plan_worksheets(self, editor) -> None:
        raw_crystal = editor.plan_type is PlanType.RAW_CRYSTAL
        if editor.current_plan is None:
            QMessageBox.warning(
                self, "Cannot save worksheets",
                f"The {'raw crystal' if raw_crystal else 'fragment'} plan is not valid.",
            )
            return
        assignment_order = self._choose_worksheet_assignment_order()
        if assignment_order is None:
            return
        order_index = editor.order_input.findData(assignment_order)
        if order_index != editor.order_input.currentIndex():
            editor.order_input.setCurrentIndex(order_index)
        plan = editor.current_plan
        if plan is None:
            QMessageBox.warning(
                self, "Cannot save worksheets", "The reordered plan is not valid."
            )
            return
        revision = self._finalize_plan(editor)
        if revision is None:
            return
        service = self._worksheet_export_service()
        try:
            result = self._run_in_background(
                "Saving worksheets to "
                + ("SHIFTER folders…" if raw_crystal else "instrument folders…"),
                lambda: service.deliver(plan, revision.experiment_id),
            )
        except WorksheetDestinationUnavailable as error:
            result = self._deliver_worksheets_to_alternate_root(
                service, revision, plan, error, raw_crystal
            )
            if result is None:
                return
        self._record_worksheet_export(service, revision, WORKSHEETS_SUCCEEDED, result=result)
        editor.experiment_id_label.setText(
            f"Experiment ID: {result.experiment_id} · Saved as {result.file_stem}"
        )
        shifter_paths = (
            f"SHIFTER 1:\n{result.shifter1_path}\n\nSHIFTER 2:\n{result.shifter2_path}"
        )
        if raw_crystal:
            QMessageBox.information(self, "SHIFTER worksheets saved", shifter_paths)
        else:
            QMessageBox.information(
                self, "Worksheets saved",
                f"ECHO:\n{result.echo_path}\n\n{shifter_paths}",
            )

    def _deliver_worksheets_to_alternate_root(
        self, service: WorksheetExportService, revision: PlanRevision, plan,
        error: WorksheetDestinationUnavailable, raw_crystal: bool,
    ):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Critical)
        dialog.setWindowTitle(
            "SHIFTER destination unavailable" if raw_crystal
            else "Worksheet destination unavailable"
        )
        dialog.setText(str(error))
        dialog.setInformativeText(
            "No ECHO worksheet is required for a Raw Crystal Plan. "
            "Choose an alternate root for the two SHIFTER worksheets?"
            if raw_crystal else
            "The worksheets were not delivered to the instrument folders. "
            "Choose an alternate output root?"
        )
        choose_button = dialog.addButton(
            "Choose Alternate Location…", QMessageBox.ActionRole
        )
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec_()
        if dialog.clickedButton() is not choose_button:
            self._record_worksheet_export(
                service, revision, WORKSHEETS_FAILED, error=str(error)
            )
            return None
        selected = QFileDialog.getExistingDirectory(
            self, "Choose alternate worksheet output root", str(Path.home())
        )
        if not selected:
            self._record_worksheet_export(
                service, revision, WORKSHEETS_CANCELLED, error=str(error)
            )
            return None
        try:
            return self._run_in_background(
                "Saving worksheets…",
                lambda: service.deliver(plan, revision.experiment_id, Path(selected)),
            )
        except WorksheetDestinationUnavailable as fallback_error:
            self._record_worksheet_export(
                service, revision, WORKSHEETS_FAILED, error=str(fallback_error)
            )
            QMessageBox.critical(self, "Could not save worksheets", str(fallback_error))
            return None

    def _worksheet_export_service(self) -> WorksheetExportService:
        return WorksheetExportService(
            WorksheetExporter(self.settings, getpass.getuser()),
            self.review_store,
            getpass.getuser(),
        )

    def _record_worksheet_export(
        self, service: WorksheetExportService, revision: PlanRevision, status: str,
        *, result=None, error: str | None = None,
    ) -> None:
        try:
            service.record(revision, status, result=result, error=error)
        except ReviewPersistenceError as persistence_error:
            self._show_persistence_error(persistence_error)

    def _choose_worksheet_assignment_order(self) -> AssignmentOrder | None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("Worksheet assignment order")
        dialog.setText("How should fragments be assigned in the worksheets?")
        dialog.setInformativeText(
            "Changing the order reassigns fragments and updates all three previews."
        )
        selection_button = dialog.addButton(
            "Selection Order", QMessageBox.AcceptRole
        )
        plate_button = dialog.addButton(
            "Plate / Well Order", QMessageBox.ActionRole
        )
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec_()
        clicked = dialog.clickedButton()
        if clicked is selection_button:
            return AssignmentOrder.SELECTION
        if clicked is plate_button:
            return AssignmentOrder.PLATE_WELL
        return None

    def _main_tab_changed(self, index: int) -> None:
        if index != self.planning_tab_index:
            return
        editor = self.plan_stack.currentWidget()
        if not isinstance(editor, (FragmentScreeningEditor, RawCrystalEditor)):
            return
        if getattr(editor, "selection_snapshot_owned", False):
            return
        try:
            crystals = self.project_controller.selected_crystals_for_plan()
            if not crystals:
                raise ValueError("select at least one crystal target first")
        except IMAGE_SOURCE_ERRORS as error:
            editor.current_plan = None
            editor.error_label.setText(
                f"Targets changed: {error}. Review warnings in Image Review."
            )
            if isinstance(editor, FragmentScreeningEditor):
                editor.table.setRowCount(0)
                editor.echo_table.setRowCount(0)
                editor.shifter_table.setRowCount(0)
            else:
                editor.summary_table.setRowCount(0)
                editor.shifter_table.setRowCount(0)
            return
        editor.set_crystals(crystals)

    def _switch_planning_project(self, project_id: str | None) -> None:
        if self._planning_project_id == project_id:
            return
        while self.plan_stack.count() > 1:
            widget = self.plan_stack.widget(1)
            self.plan_stack.removeWidget(widget)
            widget.setParent(None)
        self.plan_list.clear()
        self.plan_list_empty_label.show()
        self._planning_project_id = project_id
        if project_id is None:
            return
        if project_id not in self._planning_drafts and self.review_store is not None:
            self._planning_drafts[project_id] = []
            try:
                crystals = self.project_controller.selected_crystals_for_plan()
            except IMAGE_SOURCE_ERRORS:
                crystals = ()
            unrestored: list[str] = []
            try:
                drafts = self.review_store.load_planning_drafts(project_id)
            except ReviewPersistenceError as error:
                drafts = ()
                unrestored.append(str(error))
            for draft in drafts:
                try:
                    if draft.plan_type == PlanType.FRAGMENT_SCREENING.value:
                        self._add_fragment_plan(None, crystals, draft)
                    elif draft.plan_type == PlanType.RAW_CRYSTAL.value:
                        self._add_raw_crystal_plan(crystals, draft)
                except (ValueError, ReviewPersistenceError) as error:
                    # One damaged plan must not keep the others, or the window,
                    # from opening.
                    unrestored.append(f"{draft.name}: {error}")
            if unrestored:
                self.status_message_label.show_message(
                    f"{len(unrestored)} saved plan(s) could not be restored · "
                    + " · ".join(unrestored)
                )
            return
        for name, editor in self._planning_drafts.get(project_id, []):
            editor.setParent(self.plan_stack)
            self.plan_stack.addWidget(editor)
            status = getattr(editor, "plan_list_status", "")
            self.plan_list.addItem(f"{name} · {status}" if status else name)
        if self.plan_list.count():
            self.plan_list_empty_label.hide()
            self.plan_list.setCurrentRow(0)

    def _planning_calibration_action(
        self, acceptable_wells: int, warning_targets: int
    ) -> str:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Target calibration needs review")
        dialog.setText(f"{warning_targets} target(s) are not ready for planning.")
        if acceptable_wells:
            dialog.setInformativeText(
                f"{acceptable_wells} valid automatically detected well(s) can be "
                "confirmed now. Missing calibrations and targets outside a well "
                "will remain blocked and will not be omitted from the plan."
            )
            accept_button = dialog.addButton(
                f"Accept Valid ({acceptable_wells}) & Continue",
                QMessageBox.AcceptRole,
            )
        else:
            dialog.setInformativeText(
                "Missing calibrations and targets outside a well are not omitted. "
                "Review and resolve every warning before creating a plan."
            )
            accept_button = None
        review_button = dialog.addButton("Review Warnings", QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec_()
        clicked = dialog.clickedButton()
        if clicked is accept_button:
            return "accept"
        if clicked is review_button:
            return "review"
        return "cancel"

    def _review_target_warnings(self) -> None:
        self.main_tabs.setCurrentIndex(self.image_review_tab_index)
        warning_index = self.target_summary_filter.findData("warnings")
        self.target_summary_filter.setCurrentIndex(warning_index)
        self.target_summary_dock.show()
        self.target_summary_dock.raise_()
        self._refresh_target_summary()
        if self.target_summary_table.rowCount():
            self.target_summary_table.setCurrentCell(0, 0)
            self.target_summary_table.setFocus(Qt.OtherFocusReason)

    def _initialize_projects(self) -> None:
        unavailable: Exception | None = None
        if self.project_controller.projects:
            project_ids = {project.id for project in self.project_controller.projects}
            project_id = self.project_controller.last_open_project_id
            if project_id not in project_ids:
                project_id = self.project_controller.projects[0].id
            try:
                self.project_controller.open_project(project_id)
            except IMAGE_SOURCE_ERRORS as error:
                # Start without images so the user can retry or switch workspace.
                unavailable = error
        else:
            self.project_controller.create_project("Untitled Project")
        self._adopt_active_review()
        self._sync_project_widgets()
        if unavailable is not None:
            self._show_images_unavailable(unavailable)

    def _show_images_unavailable(self, error: Exception) -> None:
        self.navigation_label.setText("Images unavailable")
        self.review_summary_label.setText(
            f"Images unavailable: {error} · select the plate again to retry"
        )
        self.status_message_label.show_message(f"Images unavailable: {error}")

    def _target_summary_visibility_changed(self, visible: bool) -> None:
        if visible:
            self._refresh_target_summary()
        if (
            not self.isVisible()
            or self.isMaximized()
            or self.isFullScreen()
            or self.target_summary_dock.isFloating()
        ):
            return
        if visible and self._target_summary_window_expansion == 0:
            self._target_summary_window_expansion = max(
                self.target_summary_dock.width(),
                self.target_summary_dock.sizeHint().width(),
            )
        elif not visible and self._target_summary_window_expansion:
            contraction = self._target_summary_window_expansion
            self._target_summary_window_expansion = 0
            QTimer.singleShot(
                0, lambda amount=contraction: self._shrink_after_summary_close(amount)
            )

    def _shrink_after_summary_close(self, contraction: int) -> None:
        if (
            not self.isVisible()
            or self.target_summary_dock.isVisible()
            or self.isMaximized()
            or self.isFullScreen()
        ):
            return
        target_width = max(
            self.minimumWidth(),
            self.width() - contraction,
        )
        self.resize(target_width, self.height())

    def create_project_interactively(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "New workspace", "Workspace name:"
        )
        if not accepted:
            return
        try:
            if self.controller is not None:
                self.controller.checkpoint_current()
            self.project_controller.create_project(name)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot create workspace", str(error))

    def rename_project_interactively(self) -> None:
        project = self.project_controller.active_project
        if project is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename workspace", "Workspace name:", text=project.name
        )
        if not accepted:
            return
        try:
            self.project_controller.rename_active_project(name)
            self._sync_project_widgets()
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot rename workspace", str(error))

    def _project_selected(self, index: int) -> None:
        project_id = self.project_selector.itemData(index)
        if not project_id:
            return
        active = self.project_controller.active_project
        if active is not None and active.id == project_id:
            return
        try:
            self.project_controller.open_project(project_id)
        except IMAGE_SOURCE_ERRORS as error:
            # The workspace may already be open without its image set; show the
            # controller's actual state instead of the previous plate.
            self._adopt_active_review()
            self._sync_project_widgets()
            self._show_images_unavailable(error)
            QMessageBox.warning(self, "Cannot open workspace images", str(error))
            return
        self._adopt_active_review()
        self._sync_project_widgets()

    def _image_set_selected(self, index) -> None:
        image_set_id = index.data(ProjectImageSetListModel.ImageSetIdRole)
        if not image_set_id:
            return
        try:
            self.project_controller.activate_image_set(image_set_id)
            self._adopt_active_review()
            self._sync_project_widgets()
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
            self._adopt_active_review()
            self._sync_project_widgets()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot open image set", str(error))

    def _move_selected_image_set(self, offset: int) -> None:
        image_set_id = self._selected_image_set_id()
        if image_set_id is None:
            return
        try:
            self.project_controller.move_image_set(image_set_id, offset)
            self._sync_project_widgets()
        except (ValueError, ReviewPersistenceError) as error:
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
            self._adopt_active_review()
            self._sync_project_widgets()
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
            self._adopt_active_review()
            self._sync_project_widgets()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot restore image set", str(error))

    def _adopt_active_review(self) -> None:
        self.controller = self.project_controller.review_controller
        self.plate = self.controller.plate if self.controller is not None else None
        if self.controller is None:
            self.calibration_service = None
            self.current_calibration = None
            self._manual_calibration_points = None
            self.image_canvas.clear_image()
            self.well_input.clear()
            self._well_destinations.clear()
            self.well_completion_model.setStringList([])
            self.navigation_label.setText("No image set loaded")
            self.review_summary_label.setText("Add a plate to the active workspace")
            self.project_progress_label.setText("Workspace: no images")
            self.save_status_label.setText("Not loaded")
            self.image_path_status.set_image_path(None)
            self.status_message_label.clear()
            self.accept_calibration_button.setEnabled(False)
            self.auto_confirm_plate_checkbox.setEnabled(False)
            self.auto_confirm_plate_checkbox.setChecked(False)
            self._update_navigation()
            return
        self.calibration_service = None
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

    def _sync_project_widgets(self) -> None:
        active = self.project_controller.active_project
        self._switch_planning_project(active.id if active is not None else None)
        self.project_selector.blockSignals(True)
        self.project_selector.clear()
        for project in self.project_controller.projects:
            self.project_selector.addItem(project.name, project.id)
        if active is not None:
            active_index = self.project_selector.findData(active.id)
            self.project_selector.setCurrentIndex(active_index)
        self.project_selector.blockSignals(False)
        self.image_set_model.set_project(active)
        if active is not None and active.active_image_set_id is not None:
            for row, image_set in enumerate(self.image_set_model.image_sets):
                if image_set.id == active.active_image_set_id:
                    self.image_set_list.setCurrentIndex(self.image_set_model.index(row, 0))
                    plate_format = plate_format_by_id(image_set.plate_format_id)
                    format_index = self.plate_format_input.findData(plate_format)
                    self.plate_format_input.setCurrentIndex(max(format_index, 0))
                    break
        self._refresh_target_summary()

    def _target_count_for_image_set(self, image_set_id: str) -> int:
        if (
            self.project_controller.active_project is not None
            and self.project_controller.active_project.active_image_set_id == image_set_id
            and self.controller is not None
        ):
            return self.controller.session.target_count
        if self.review_store is not None:
            return self.review_store.target_count_for_image_set(image_set_id)
        return 0

    def load_entered_plate(self) -> None:
        try:
            plate_format = self._selected_plate_format()
            plate_codes = tuple(
                dict.fromkeys(
                    plate_code.strip()
                    for plate_code in self.plate_input.text().split(",")
                    if plate_code.strip()
                )
            )
            if not plate_codes:
                raise ValueError("enter at least one plate code")
            load_latest_for_all = False
            for plate_code in plate_codes:
                if load_latest_for_all:
                    self._add_latest_plate(plate_code, plate_format)
                    continue
                accepted, load_latest_for_all = self._choose_and_add_plate(
                    plate_code, plate_format
                )
                if not accepted:
                    return
            self._adopt_active_review()
            self._sync_project_widgets()
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self, "Cannot load plate", str(error))

    def _choose_and_add_plate(
        self, plate_code: str, plate_format: PlateFormat
    ) -> tuple[bool, bool]:
        dialog = PlateSourceDialog(self.repository, plate_code, self)
        if dialog.exec_() != QDialog.Accepted:
            return False, False
        self.project_controller.add_pinned_image_set(
            plate_code, dialog.batch_id, dialog.profile, plate_format
        )
        return True, dialog.load_latest_for_all.isChecked()

    def _add_latest_plate(
        self, plate_code: str, plate_format: PlateFormat
    ) -> None:
        batch_id, profile = latest_image_source(self.repository, plate_code)
        self.project_controller.add_pinned_image_set(
            plate_code, batch_id, profile, plate_format
        )

    def load_plate(
        self, plate_code: str, plate_format: PlateFormat, profile: str = "profileID_1"
    ) -> None:
        self.project_controller.add_latest_image_set(plate_code, plate_format, profile)
        self._adopt_active_review()
        self._sync_project_widgets()

    def _selected_plate_format(self) -> PlateFormat:
        plate_format = self.plate_format_input.currentData()
        if plate_format is None:
            raise ValueError("select the plate format before adding a plate")
        return plate_format

    def _active_plate_format(self) -> PlateFormat | None:
        image_set = self.project_controller.active_image_set
        if image_set is None:
            return None
        plate_format = plate_format_by_id(image_set.plate_format_id)
        if (
            plate_format is None
            or image_set.plate_format_version != plate_format.version
        ):
            return None
        return plate_format

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
            self._adopt_active_review()
            self._sync_project_widgets()
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
        self.target_summary_status_label.setText(
            f"Ready {ready_count} · Warnings {warning_count}"
        )
        try:
            acceptable_count = (
                self.project_controller
                .valid_unconfirmed_automatic_calibration_count()
            )
        except IMAGE_SOURCE_ERRORS:
            acceptable_count = 0
        self.accept_valid_auto_wells_button.setText(
            f"Accept Valid Auto Wells ({acceptable_count})"
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
                plate_format = plate_format_by_id(image_set.plate_format_id)
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
                    calibration_status = "Missing"
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
                    TargetValidationIssue.CALIBRATION_MISSING: "Calibration missing",
                    TargetValidationIssue.CALIBRATION_UNCONFIRMED: "Unconfirmed calibration",
                    TargetValidationIssue.OUTSIDE_WELL: "Outside well",
                }
                validation_status = (
                    " · ".join(
                        issue_labels[issue]
                        for issue in summary.validation_issues
                    )
                    or "Ready"
                )
                values = (
                    summary.image.plate_code,
                    well,
                    str(summary.target_number),
                    x_mm,
                    y_mm,
                    calibration_status,
                    validation_status,
                )
                tooltip = (
                    f"Pixel: ({summary.target.x_px:.1f}, {summary.target.y_px:.1f})\n"
                    f"{summary.image.path.resolve()}"
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(tooltip)
                    if column == 6:
                        item.setForeground(
                            QColor("#2e7d32" if summary.is_ready else "#c62828")
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
            self._adopt_active_review()
            self._sync_project_widgets()
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
        remove_action = menu.addAction("Remove selected targets")
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
        except (ValueError, ReviewPersistenceError) as error:
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
        self._adopt_active_review()
        self._sync_project_widgets()
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
            self.calibration_label.setText("Well calibration unavailable: unsupported format")
            self.accept_calibration_button.setEnabled(False)
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
            image_label = (
                f"Plate {image.plate_code} · Well {address} "
                f"(RM {image.well_number}/d{image.drop_number})"
            )
        if plate_format is not None:
            self._current_well_address = str(
                plate_format.address_for(image.well_number, image.drop_number)
            )
            self.well_input.setText(self._current_well_address)
        self.navigation_label.setText(
            f"{image_label} · Batch {image.batch_id} · "
            f"{self.controller.image_index + 1}/{len(self.controller.plate.images)}"
        )
        self._update_review_summary()
        self._update_navigation()

    def _handle_image_click(self, x_px: float, y_px: float, button: int) -> None:
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
            self.calibration_label.setText(f"Well calibration unavailable: {error}")
            self.accept_calibration_button.setEnabled(False)
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
        scale_um = calibration.physical_diameter_mm * 1000 / (
            calibration.radius_x_px + calibration.radius_y_px
        )
        method = (
            "Manual"
            if calibration.method is CalibrationMethod.MANUAL_THREE_POINT
            else "Auto"
        )
        confirmation = "Confirmed" if calibration.confirmed else "Unconfirmed"
        self.calibration_label.setText(
            f"Well: {method} · {confirmation} · confidence {calibration.confidence:.0%} · "
            f"center ({calibration.center_x_px:.1f}, {calibration.center_y_px:.1f}) px · "
            f"{scale_um:.3f} µm/px"
        )
        self.accept_calibration_button.setEnabled(not calibration.confirmed)
        self._refresh_target_summary()

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
        self.image_canvas.set_calibration_points(())
        self.calibration_label.setText(
            "Click three separated points on the outer well boundary · right-click cancels"
        )
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
        except (ValueError, ReviewPersistenceError) as error:
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
        except (ValueError, ReviewPersistenceError) as error:
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
        self.well_input.setStyleSheet("border: 1px solid #c62828;")
        self.status_message_label.show_message("Invalid or unavailable subwell", 2000)
        QTimer.singleShot(800, lambda: self.well_input.setStyleSheet(""))

    def _restore_current_well_address(self) -> None:
        self.well_input.setText(self._current_well_address)
        self.well_input.setStyleSheet("")

    def _update_review_summary(self) -> None:
        if self.controller is None:
            return
        image_count = len(self.controller.current_targets)
        auto_advance_count = self.controller.preferences.auto_advance_target_count
        reviewed = self.controller.session.reviewed_count
        total = len(self.controller.plate.images)
        filtered = len(self.controller.filtered_indices)
        review_state = (
            "Reviewed"
            if self.controller.session.is_reviewed(self.controller.current_image)
            else "Unreviewed"
        )
        self.review_summary_label.setText(
            f"Selected: {image_count} · Targets/img: {auto_advance_count} · Session total: "
            f"{self.controller.session.target_count} · {review_state} · Reviewed: {reviewed}/{total} "
            f"· Plate matches: {filtered}"
        )
        try:
            statistics = self.project_controller.project_review_statistics()
            self.project_progress_label.setText(
                f"Reviewed {statistics.reviewed_images}/{statistics.total_images} · "
                f"Target images {statistics.target_images} · "
                f"No-target {statistics.reviewed_without_targets} · "
                f"Pending {statistics.unreviewed_images} · Points {statistics.target_points}"
            )
        except IMAGE_SOURCE_ERRORS:
            self.project_progress_label.setText("Project totals unavailable")
        self._update_navigation()

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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        for drafts in self._planning_drafts.values():
            for _, editor in drafts:
                if hasattr(editor, "autosave_timer"):
                    editor.autosave_timer.stop()
                if isinstance(editor, (FragmentScreeningEditor, RawCrystalEditor)):
                    self._persist_draft(editor)
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
            "saved": ("Saved", "#2e7d32"),
            "unsaved": ("Unsaved changes", "#ad6800"),
            "failed": ("Save failed", "#c62828"),
        }
        text, color = styles[state]
        self.save_status_label.setText(text)
        self.save_status_label.setStyleSheet(f"font-weight: 600; color: {color};")

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
        default=DEFAULT_SETTINGS.echo_output_directory,
        help="ECHO worksheet output directory",
    )
    parser.add_argument(
        "--shifter1-dir",
        type=Path,
        default=DEFAULT_SETTINGS.shifter1_output_directory,
        help="SHIFTER 1 worksheet output directory",
    )
    parser.add_argument(
        "--shifter2-dir",
        type=Path,
        default=DEFAULT_SETTINGS.shifter2_output_directory,
        help="SHIFTER 2 worksheet output directory",
    )
    parser.add_argument(
        "--allow-local-instrument-dirs",
        action="store_true",
        help="Allow ECHO/SHIFTER directories that are not mounted network shares "
        "(testing only; instruments will not see these files)",
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
        help="external TOML file containing OS-user to MxLive account mappings",
    )
    return parser


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
    settings = replace(
        DEFAULT_SETTINGS,
        rmserver_root=args.root,
        fragment_library_directory=args.library_dir,
        worksheet_staging_directory=args.worksheet_dir,
        echo_output_directory=args.echo_dir,
        shifter1_output_directory=args.shifter1_dir,
        shifter2_output_directory=args.shifter2_dir,
        mxlive_base_url=args.mxlive_url,
        mxlive_key_path=args.mxlive_key,
        mxlive_ca_bundle=args.mxlive_ca,
        mxlive_config_path=args.mxlive_config,
    )
    settings = with_instrument_output_policy(
        settings, args.allow_local_instrument_dirs
    )
    try:
        review_store = SQLiteReviewStore(database_path)
    except ReviewPersistenceError as error:
        print(f"xtalflow-viewer: {error}", file=sys.stderr)
        return 2
    window = ViewerWindow(
        RockMakerImageRepository(settings.rmserver_root),
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
        window.plate_input.setText(args.plate)
        window.plate_format_input.setCurrentIndex(
            window.plate_format_input.findData(plate_format)
        )
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
