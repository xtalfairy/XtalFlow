"""Start screen: what XtalFlow can prepare, and experiments to resume."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
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
from xtalflow.ui import theme


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
        "Soak one library fragment into each selected crystal, then harvest.",
        "ECHO + SHIFTER worksheets · MxLive records",
    ),
    ExperimentChoice(
        PlanType.RAW_CRYSTAL,
        "Raw Crystal",
        "Harvest selected crystals without soaking.",
        "SHIFTER worksheets · MxLive records",
    ),
)

UPCOMING_EXPERIMENTS = (
    ("Solvent / Duration Test", "Check how long crystals tolerate solvent concentrations."),
    ("Cryo Test", "Compare cryoprotectant conditions before data collection."),
    ("Custom Soaking", "Soak with a template of your own conditions."),
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
        title = QLabel("What would you like to prepare?")
        title.setObjectName("PrimaryHeading")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        title.setFont(font)
        subtitle = QLabel("Choose an experiment. XtalFlow guides you from crystal images to instrument worksheets.")
        subtitle.setObjectName("Muted")

        cards = QHBoxLayout()
        cards.setSpacing(theme.SPACING_L)
        self.start_buttons: dict[PlanType, QPushButton] = {}
        for choice in EXPERIMENT_CHOICES:
            cards.addWidget(self._experiment_card(choice))
        cards.addStretch()

        self.more_types_button = QToolButton()
        self.more_types_button.setText("More experiment types")
        self.more_types_button.setCheckable(True)
        self.more_types_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more_types_button.setArrowType(Qt.RightArrow)
        self.upcoming_panel = QLabel(
            "\n".join(f"{name} — Not available yet · {text}" for name, text in UPCOMING_EXPERIMENTS)
        )
        self.upcoming_panel.setObjectName("Muted")
        self.upcoming_panel.hide()
        # The window puts its workspace chooser here; most people never change it.
        self.workspace_row = QHBoxLayout()
        self.workspace_row.setSpacing(theme.SPACING_M)

        recent_title = QLabel("Recent work")
        recent_title.setObjectName("SectionTitle")
        self.recent_table = QTableWidget(0, 5)
        self.recent_table.setHorizontalHeaderLabels(
            ("Experiment", "Type", "Status", "Workspace", "Edited")
        )
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recent_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recent_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.recent_table.setContextMenuPolicy(Qt.CustomContextMenu)
        header = self.recent_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.resume_button = QPushButton("Resume")
        self.resume_button.setEnabled(False)
        self.recent_empty_label = QLabel("No experiments yet. Start one above.")
        self.recent_empty_label.setObjectName("Muted")
        recent_header = QHBoxLayout()
        recent_header.addWidget(recent_title)
        recent_header.addStretch()
        recent_header.addWidget(self.resume_button)

        content = QVBoxLayout()
        content.setContentsMargins(theme.SPACING_XL * 2, theme.SPACING_XL * 2, theme.SPACING_XL * 2, theme.SPACING_XL)
        content.setSpacing(theme.SPACING_L)
        content.addWidget(title)
        content.addWidget(subtitle)
        content.addSpacing(theme.SPACING_M)
        content.addLayout(cards)
        content.addWidget(self.more_types_button)
        content.addWidget(self.upcoming_panel)
        content.addLayout(self.workspace_row)
        content.addSpacing(theme.SPACING_L)
        content.addLayout(recent_header)
        content.addWidget(self.recent_empty_label)
        content.addWidget(self.recent_table, 1)
        # Takes the free space while the recent table is hidden.
        self._bottom_stretch = QWidget()
        content.addWidget(self._bottom_stretch, 1)
        self.setLayout(content)

        self._recent: tuple[RecentExperiment, ...] = ()
        self.more_types_button.toggled.connect(self._toggle_upcoming)
        self.recent_table.itemSelectionChanged.connect(
            lambda: self.resume_button.setEnabled(bool(self.recent_table.selectedItems()))
        )
        self.recent_table.cellDoubleClicked.connect(lambda row, _: self._resume_row(row))
        self.resume_button.clicked.connect(
            lambda: self._resume_row(self.recent_table.currentRow())
        )
        self.recent_table.customContextMenuRequested.connect(self._show_recent_menu)

    def _experiment_card(self, choice: ExperimentChoice) -> QFrame:
        card = QFrame()
        card.setObjectName("SelectionBar")
        card.setMinimumWidth(320)
        card.setMaximumWidth(420)
        heading = QLabel(choice.title)
        heading.setObjectName("PrimaryHeading")
        description = QLabel(choice.description)
        description.setWordWrap(True)
        outputs = QLabel(choice.outputs)
        outputs.setObjectName("Muted")
        start = QPushButton(f"Start {choice.title}")
        start.setObjectName("Primary")
        start.clicked.connect(lambda _=False, selected=choice.plan_type: self.start_requested.emit(selected))
        self.start_buttons[choice.plan_type] = start
        layout = QVBoxLayout()
        layout.setContentsMargins(theme.SPACING_XL, theme.SPACING_L, theme.SPACING_XL, theme.SPACING_L)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addWidget(outputs)
        layout.addSpacing(theme.SPACING_S)
        layout.addWidget(start, 0, Qt.AlignLeft)
        card.setLayout(layout)
        return card

    def show_recent_work(self, experiments: tuple[RecentExperiment, ...]) -> None:
        self._recent = experiments
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
                self.recent_table.setItem(row, column, QTableWidgetItem(value))
        self.recent_table.setVisible(bool(experiments))
        self._bottom_stretch.setVisible(not experiments)
        self.recent_empty_label.setVisible(not experiments)
        self.resume_button.setEnabled(False)

    def _resume_row(self, row: int) -> None:
        if 0 <= row < len(self._recent):
            experiment = self._recent[row]
            self.resume_requested.emit(experiment.workspace_id, experiment.plan_id)

    def _show_recent_menu(self, position) -> None:
        row = self.recent_table.indexAt(position).row()
        if not 0 <= row < len(self._recent):
            return
        experiment = self._recent[row]
        menu = QMenu(self.recent_table)
        menu.addAction("Resume").triggered.connect(lambda: self._resume_row(row))
        menu.addAction("Delete Experiment…").triggered.connect(
            lambda: self.delete_requested.emit(experiment.workspace_id, experiment.plan_id)
        )
        menu.exec_(self.recent_table.viewport().mapToGlobal(position))

    def _toggle_upcoming(self, visible: bool) -> None:
        self.upcoming_panel.setVisible(visible)
        self.more_types_button.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
