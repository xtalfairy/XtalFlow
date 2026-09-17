"""Start screen: recent experiments first, and the types XtalFlow can prepare."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xtalflow.domain import PlanType
from xtalflow.ui import theme


# Scoped to the landing page: other workflow screens keep their existing style.
HOME_STYLE = f"""
QWidget#ExperimentHome {{ background: {theme.BACKGROUND}; }}
QLabel#HomeTitle {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeSection {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeMuted {{ color: {theme.TEXT_MUTED}; }}
QFrame#HomeChoices {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
    border-radius: 4px; }}
QFrame#HomeChoices QLabel {{ border: none; background: transparent; }}
QFrame#HomeDivider {{ border: none; border-top: 1px solid {theme.BORDER}; }}
QLabel#HomeChoiceTitle {{ color: {theme.TEXT}; font-weight: 600; }}
QPushButton#HomeCreate {{ background: {theme.SURFACE}; color: {theme.FOCUS};
    border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 3px 14px; }}
QPushButton#HomeCreate:hover {{ background: {theme.FOCUS_SOFT}; }}
QPushButton#HomeCreate:focus {{ border: 1px solid {theme.FOCUS}; }}
QPushButton#HomePrimary {{ background: {theme.FOCUS}; color: white;
    border: 1px solid {theme.FOCUS}; border-radius: 4px; padding: 3px 16px; }}
QPushButton#HomePrimary:disabled {{ background: transparent; color: {theme.TEXT_MUTED};
    border-color: {theme.BORDER}; }}
QTableWidget#HomeRecent {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
    border-radius: 4px; selection-background-color: {theme.FOCUS_SOFT};
    selection-color: {theme.TEXT}; }}
QTableWidget#HomeRecent::item {{ padding: 0 8px; border-bottom: 1px solid {theme.BORDER}; }}
QTableWidget#HomeRecent:focus {{ border: 1px solid {theme.FOCUS}; }}
QLabel#HomeEmpty {{ color: {theme.TEXT_MUTED}; background: {theme.SURFACE};
    border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 24px; }}
"""


@dataclass(frozen=True)
class ExperimentChoice:
    plan_type: PlanType
    title: str
    description: str
    outputs: str


EXPERIMENT_CHOICES = (
    ExperimentChoice(
        PlanType.FRAGMENT_SCREENING,
        "Fragment Screening",
        "One library fragment per selected well.",
        "ECHO + SHIFTER worksheets",
    ),
    ExperimentChoice(
        PlanType.RAW_CRYSTAL,
        "Raw Crystal",
        "Harvest selected wells without soaking.",
        "SHIFTER worksheets",
    ),
)


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExperimentHome")
        self.setStyleSheet(HOME_STYLE)
        title = QLabel("Experiments")
        title.setObjectName("HomeTitle")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.35)
        title.setFont(font)
        # The window puts its workspace chooser here; most people never change it.
        self.workspace_row = QHBoxLayout()
        self.workspace_row.setSpacing(theme.SPACING_M)
        heading = QHBoxLayout()
        heading.addWidget(title)
        heading.addStretch()
        heading.addLayout(self.workspace_row)

        new_title = QLabel("New experiment")
        new_title.setObjectName("HomeSection")
        self.start_buttons: dict[PlanType, QPushButton] = {}
        choices = QFrame()
        choices.setObjectName("HomeChoices")
        grid = QGridLayout()
        grid.setContentsMargins(theme.SPACING_L, theme.SPACING_S, theme.SPACING_M, theme.SPACING_S)
        grid.setHorizontalSpacing(theme.SPACING_XL)
        grid.setVerticalSpacing(theme.SPACING_S)
        for index, choice in enumerate(EXPERIMENT_CHOICES):
            row = index * 2
            if index:
                divider = QFrame()
                divider.setObjectName("HomeDivider")
                divider.setFixedHeight(1)
                grid.addWidget(divider, row - 1, 0, 1, 4)
            self._add_choice(grid, row, choice)
        grid.setColumnStretch(1, 1)
        choices.setLayout(grid)

        recent_title = QLabel("Recent")
        recent_title.setObjectName("HomeSection")
        self.recent_count = QLabel("0 experiments")
        self.recent_count.setObjectName("HomeMuted")
        self.recent_table = QTableWidget(0, 5)
        self.recent_table.setObjectName("HomeRecent")
        self.recent_table.setHorizontalHeaderLabels(
            ("Experiment", "Type", "Status", "Workspace", "Edited")
        )
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.verticalHeader().setDefaultSectionSize(30)
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
        recent_header.addWidget(self.resume_button)

        content = QVBoxLayout()
        content.setContentsMargins(theme.SPACING_XL * 2, 20, theme.SPACING_XL * 2, theme.SPACING_XL)
        content.setSpacing(theme.SPACING_M)
        content.addLayout(heading)
        content.addSpacing(theme.SPACING_L)
        content.addWidget(new_title)
        content.addWidget(choices)
        content.addSpacing(theme.SPACING_XL)
        content.addLayout(recent_header)
        content.addWidget(self.recent_empty_label)
        content.addWidget(self.recent_table, 1)
        # Takes the free space while the recent table is hidden.
        self._bottom_stretch = QWidget()
        content.addWidget(self._bottom_stretch, 1)
        self.setLayout(content)

        self._recent: tuple[RecentExperiment, ...] = ()
        self.recent_table.itemSelectionChanged.connect(
            lambda: self.resume_button.setEnabled(bool(self.recent_table.selectedItems()))
        )
        self.recent_table.cellDoubleClicked.connect(lambda row, _: self._resume_row(row))
        self.resume_button.clicked.connect(
            lambda: self._resume_row(self.recent_table.currentRow())
        )
        self.recent_table.customContextMenuRequested.connect(self._show_recent_menu)
        self.show_recent_work(())

    def _add_choice(self, grid: QGridLayout, row: int, choice: ExperimentChoice) -> None:
        heading = QLabel(choice.title)
        heading.setObjectName("HomeChoiceTitle")
        heading.setMinimumWidth(150)
        description = QLabel(choice.description)
        outputs = QLabel(choice.outputs)
        outputs.setObjectName("HomeMuted")
        start = QPushButton("Create")
        start.setObjectName("HomeCreate")
        start.setAccessibleName(f"Create {choice.title} experiment")
        start.clicked.connect(
            lambda _=False, selected=choice.plan_type: self.start_requested.emit(selected)
        )
        self.start_buttons[choice.plan_type] = start
        grid.addWidget(heading, row, 0)
        grid.addWidget(description, row, 1)
        grid.addWidget(outputs, row, 2)
        grid.addWidget(start, row, 3)

    def show_recent_work(self, experiments: tuple[RecentExperiment, ...]) -> None:
        self._recent = experiments
        self.recent_count.setText(
            f"{len(experiments)} experiment" + ("s" if len(experiments) != 1 else "")
        )
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
        self.recent_empty_label.setVisible(not experiments)
        self._bottom_stretch.setVisible(not experiments)
        self.resume_button.setEnabled(False)

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
