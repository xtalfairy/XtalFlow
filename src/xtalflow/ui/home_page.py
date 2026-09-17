"""Start screen: the experiments of the workspace chosen in the panel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.domain import PlanType
from xtalflow.ui import icons, theme


# Scoped to the landing page: other workflow screens keep their existing style.
HOME_STYLE = f"""
QWidget#ExperimentHome {{ background: {theme.BACKGROUND}; color: {theme.TEXT}; }}
QLabel#HomeTitle {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeSection {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeMuted {{ color: {theme.TEXT_MUTED}; }}
QPushButton#HomePrimary {{ background: {theme.PRIMARY}; color: white;
    border: 1px solid {theme.PRIMARY}; border-radius: 16px; padding: 5px 20px; }}
QPushButton#HomePrimary:hover {{ background: {theme.PRIMARY_HOVER}; }}
QPushButton#HomePrimary:disabled {{ background: transparent; color: {theme.TEXT_MUTED};
    border-color: {theme.BORDER}; }}
QTableWidget#HomeRecent {{ background: {theme.BACKGROUND}; border: none;
    selection-background-color: {theme.SELECTED}; selection-color: {theme.TEXT}; }}
QTableWidget#HomeRecent::item {{ padding: 0 12px; border-bottom: 1px solid {theme.BORDER}; }}
QTableWidget#HomeRecent::item:hover {{ background: {theme.SUBTLE}; }}
QTableWidget#HomeRecent:focus {{ border: 1px solid {theme.FOCUS}; }}
QTableWidget#HomeRecent QHeaderView::section {{ background: {theme.BACKGROUND};
    color: {theme.TEXT_MUTED}; border: none; padding: 10px 12px; font-weight: 400; }}
QLabel#HomeEmpty {{ color: {theme.TEXT_MUTED}; background: transparent;
    border: none; padding: 32px; }}
"""

ALL_EXPERIMENTS = "All experiments"


def panel_toggle_button(tooltip: str) -> QToolButton:
    """The same icon hides the panel from inside it and shows it again from a page."""
    button = QToolButton()
    button.setIcon(QIcon(icons.svg_pixmap(icons.SIDEBAR, 18, theme.TEXT_MUTED)))
    button.setIconSize(QSize(18, 18))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


@dataclass(frozen=True)
class ExperimentChoice:
    plan_type: PlanType
    title: str
    description: str
    outputs: str
    icon: str
    # Icon colour and its tile: a quiet way to tell the types apart at a glance.
    tint: str
    tint_soft: str


EXPERIMENT_CHOICES = (
    ExperimentChoice(
        PlanType.FRAGMENT_SCREENING,
        "Fragment Screening",
        "One library fragment soaked into each well.",
        "ECHO + SHIFTER worksheets",
        icons.FRAGMENT_SCREENING,
        theme.FOCUS,
        theme.FOCUS_SOFT,
    ),
    ExperimentChoice(
        PlanType.RAW_CRYSTAL,
        "Raw Crystal",
        "Harvest the selected wells as they are.",
        "SHIFTER worksheets",
        icons.RAW_CRYSTAL,
        theme.SUCCESS,
        theme.SUCCESS_SOFT,
    ),
)


@dataclass(frozen=True)
class WorkspaceEntry:
    id: str
    name: str
    hidden: bool = False


@dataclass(frozen=True)
class RecentExperiment:
    workspace_id: str
    workspace_name: str
    plan_id: str
    name: str
    plan_type: PlanType
    status: str
    updated_at: datetime


class HomePage(QWidget):
    start_requested = pyqtSignal(object)
    resume_requested = pyqtSignal(str, str)
    delete_requested = pyqtSignal(str, str)
    rename_workspace_requested = pyqtSignal(str)
    hide_workspace_requested = pyqtSignal(str)
    show_panel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExperimentHome")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(HOME_STYLE)
        self._workspaces: tuple[WorkspaceEntry, ...] = ()
        self._selected_workspace_id: str | None = None
        self._all_recent: tuple[RecentExperiment, ...] = ()
        self._recent: tuple[RecentExperiment, ...] = ()

        # Shown only while the workspace panel is hidden.
        self.panel_button = panel_toggle_button("Show panel (Ctrl+Shift+S)")
        self.panel_button.hide()

        self.title_label = QLabel(ALL_EXPERIMENTS)
        self.title_label.setObjectName("HomeTitle")
        font = self.title_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.8)
        self.title_label.setFont(font)
        self.workspace_actions_button = QToolButton()
        self.workspace_actions_button.setText("⋯")
        self.workspace_actions_button.setToolTip("Workspace actions")
        self.workspace_actions_button.setAccessibleName("Workspace actions")
        self.workspace_actions_button.setPopupMode(QToolButton.InstantPopup)
        workspace_menu = QMenu(self.workspace_actions_button)
        self.rename_workspace_action = workspace_menu.addAction("Rename…")
        self.hide_workspace_action = workspace_menu.addAction("Hide from list…")
        self.workspace_actions_button.setMenu(workspace_menu)
        heading = QHBoxLayout()
        heading.setSpacing(theme.SPACING_S)
        heading.addWidget(self.panel_button, 0, Qt.AlignVCenter)
        heading.addWidget(self.title_label)
        heading.addWidget(self.workspace_actions_button, 0, Qt.AlignVCenter)
        heading.addStretch()

        recent_title = QLabel("Experiments")
        recent_title.setObjectName("HomeSection")
        self.recent_count = QLabel("0 experiments")
        self.recent_count.setObjectName("HomeMuted")
        self.recent_table = QTableWidget(0, 5)
        self.recent_table.setObjectName("HomeRecent")
        self.recent_table.setHorizontalHeaderLabels(
            ("Experiment", "Type", "Status", "Workspace", "Edited")
        )
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.verticalHeader().setDefaultSectionSize(48)
        self.recent_table.setShowGrid(False)
        self.recent_table.setWordWrap(False)
        self.recent_table.installEventFilter(self)
        self.recent_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recent_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recent_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.recent_table.setContextMenuPolicy(Qt.CustomContextMenu)
        header = self.recent_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.delete_button = QPushButton("Delete…")
        self.delete_button.setToolTip("Delete the selected experiment (Delete)")
        self.delete_button.setEnabled(False)
        self.resume_button = QPushButton("Open")
        self.resume_button.setObjectName("HomePrimary")
        self.resume_button.setEnabled(False)
        self.recent_empty_label = QLabel("No experiments yet.")
        self.recent_empty_label.setObjectName("HomeEmpty")
        self.recent_empty_label.setAlignment(Qt.AlignCenter)
        recent_header = QHBoxLayout()
        recent_header.addWidget(recent_title)
        recent_header.addWidget(self.recent_count)
        recent_header.addStretch()
        recent_header.addWidget(self.delete_button)
        recent_header.addWidget(self.resume_button)

        content = QVBoxLayout()
        content.setContentsMargins(32, 24, 32, 20)
        content.setSpacing(theme.SPACING_M)
        content.addLayout(heading)
        content.addSpacing(20)
        content.addLayout(recent_header)
        content.addWidget(self.recent_empty_label)
        content.addWidget(self.recent_table, 1)
        # Takes the free space while the recent table is hidden.
        self._bottom_stretch = QWidget()
        content.addWidget(self._bottom_stretch, 1)
        self.setLayout(content)

        self.recent_table.itemSelectionChanged.connect(self._selection_changed)
        self.delete_button.clicked.connect(
            lambda: self._delete_row(self.recent_table.currentRow())
        )
        self.recent_table.cellDoubleClicked.connect(lambda row, _: self._resume_row(row))
        self.resume_button.clicked.connect(
            lambda: self._resume_row(self.recent_table.currentRow())
        )
        self.recent_table.customContextMenuRequested.connect(self._show_recent_menu)
        self.panel_button.clicked.connect(self.show_panel_requested.emit)
        self.rename_workspace_action.triggered.connect(
            lambda: self._selected_workspace_id
            and self.rename_workspace_requested.emit(self._selected_workspace_id)
        )
        self.hide_workspace_action.triggered.connect(
            lambda: self._selected_workspace_id
            and self.hide_workspace_requested.emit(self._selected_workspace_id)
        )
        self.show_workspaces((), None)

    def show_workspaces(
        self, workspaces: tuple[WorkspaceEntry, ...], selected_id: str | None
    ) -> None:
        """Visible and hidden folders, and the one being viewed (None for all)."""
        self._workspaces = workspaces
        self._selected_workspace_id = selected_id
        self._refresh()

    def show_recent_work(self, experiments: tuple[RecentExperiment, ...]) -> None:
        self._all_recent = experiments
        self._refresh()

    def _refresh(self) -> None:
        visible = tuple(item for item in self._workspaces if not item.hidden)
        known = {item.id for item in visible}
        if self._selected_workspace_id not in known:
            self._selected_workspace_id = None

        selected = next(
            (item for item in visible if item.id == self._selected_workspace_id), None
        )
        self.title_label.setText(selected.name if selected else ALL_EXPERIMENTS)
        self.workspace_actions_button.setVisible(selected is not None)
        self.hide_workspace_action.setEnabled(len(visible) > 1)

        experiments = tuple(
            experiment for experiment in self._all_recent
            if selected is None or experiment.workspace_id == selected.id
        )
        self._recent = experiments
        self.recent_count.setText(
            f"{len(experiments)} experiment" + ("s" if len(experiments) != 1 else "")
        )
        self.recent_table.setColumnHidden(3, selected is not None)
        self.recent_table.setRowCount(len(experiments))
        labels = {choice.plan_type: choice.title for choice in EXPERIMENT_CHOICES}
        for row, experiment in enumerate(experiments):
            values = (
                experiment.name,
                labels.get(experiment.plan_type, experiment.plan_type.value),
                experiment.status,
                experiment.workspace_name,
                experiment.updated_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.recent_table.setItem(row, column, item)
        self.recent_table.setVisible(bool(experiments))
        if self._all_recent:
            self.recent_empty_label.setText("No experiments in this workspace yet.")
        else:
            # The only moment with room to spare, so the types are explained here.
            self.recent_empty_label.setText(
                "No experiments yet. Start one from the left:\n\n"
                + "\n".join(
                    f"{choice.title} — {choice.description} {choice.outputs}."
                    for choice in EXPERIMENT_CHOICES
                )
            )
        self.recent_empty_label.setVisible(not experiments)
        self._bottom_stretch.setVisible(not experiments)
        self.delete_button.setVisible(bool(experiments))
        self.resume_button.setVisible(bool(experiments))
        self._selection_changed()

    def _selection_changed(self) -> None:
        selected = bool(self.recent_table.selectedItems())
        self.resume_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def _delete_row(self, row: int) -> None:
        if 0 <= row < len(self._recent):
            experiment = self._recent[row]
            self.delete_requested.emit(experiment.workspace_id, experiment.plan_id)

    def _resume_row(self, row: int) -> None:
        if 0 <= row < len(self._recent):
            experiment = self._recent[row]
            self.resume_requested.emit(experiment.workspace_id, experiment.plan_id)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if (
            watched is self.recent_table
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and event.modifiers() == Qt.NoModifier
        ):
            self._resume_row(self.recent_table.currentRow())
            return True
        if (
            watched is self.recent_table
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Delete, Qt.Key_Backspace)
            and self.recent_table.selectedItems()
        ):
            self._delete_row(self.recent_table.currentRow())
            return True
        return super().eventFilter(watched, event)

    def _show_recent_menu(self, position) -> None:
        row = self.recent_table.indexAt(position).row()
        if not 0 <= row < len(self._recent):
            return
        experiment = self._recent[row]
        menu = QMenu(self.recent_table)
        menu.addAction("Open").triggered.connect(lambda: self._resume_row(row))
        menu.addAction("Delete Experiment…").triggered.connect(
            lambda: self.delete_requested.emit(experiment.workspace_id, experiment.plan_id)
        )
        menu.exec_(self.recent_table.viewport().mapToGlobal(position))
