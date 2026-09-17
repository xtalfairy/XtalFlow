"""Start screen: recent experiments first, and the types XtalFlow can prepare."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
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
from xtalflow.ui import icons, theme


# Scoped to the landing page: other workflow screens keep their existing style.
HOME_STYLE = f"""
QWidget#ExperimentHome {{ background: {theme.BACKGROUND}; color: {theme.TEXT}; }}
QLabel#HomeTitle {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeSection {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeMuted {{ color: {theme.TEXT_MUTED}; }}
QPushButton#HomeCard {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
    min-height: 132px; max-height: 132px;
    border-radius: 16px; padding: 0; text-align: left; }}
QPushButton#HomeCard:hover {{ background: {theme.SUBTLE}; border-color: {theme.BORDER}; }}
QPushButton#HomeCard:pressed {{ background: {theme.SELECTED}; }}
QPushButton#HomeCard:focus {{ border: 2px solid {theme.FOCUS}; }}
QPushButton#HomeCard QLabel {{ background: transparent; border: none; }}
QLabel#HomeCardTitle {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HomeCardArrow {{ color: {theme.TEXT_MUTED}; }}
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
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(HOME_STYLE)
        title = QLabel("Experiments")
        title.setObjectName("HomeTitle")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.8)
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
        choices = QHBoxLayout()
        choices.setSpacing(theme.SPACING_L)
        for choice in EXPERIMENT_CHOICES:
            card = self._choice_card(choice)
            self.start_buttons[choice.plan_type] = card
            choices.addWidget(card, 1)

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
        content.setContentsMargins(40, 32, 40, 24)
        content.setSpacing(theme.SPACING_M)
        content.addLayout(heading)
        content.addSpacing(28)
        content.addWidget(new_title)
        content.addLayout(choices)
        content.addSpacing(28)
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

    def _choice_card(self, choice: ExperimentChoice) -> QPushButton:
        """The whole card starts the experiment; nothing inside it is a second target."""
        card = QPushButton()
        card.setObjectName("HomeCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setAccessibleName(f"New {choice.title} experiment")
        card.setAccessibleDescription(f"{choice.description} {choice.outputs}.")
        card.setFocusPolicy(Qt.TabFocus)
        card.setMinimumHeight(132)
        card.setMinimumWidth(320)
        icon = QLabel()
        icon.setObjectName("HomeCardIcon")
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(icons.svg_pixmap(choice.icon, 26, choice.tint))
        icon.setStyleSheet(f"background: {choice.tint_soft}; border-radius: 12px;")
        title = QLabel(choice.title)
        title.setObjectName("HomeCardTitle")
        description = QLabel(choice.description)
        description.setWordWrap(True)
        description.setObjectName("HomeMuted")
        outputs = QLabel(choice.outputs)
        outputs.setObjectName("HomeMuted")
        arrow = QLabel("›")
        arrow.setObjectName("HomeCardArrow")
        font = arrow.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        arrow.setFont(font)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(title)
        text.addWidget(description)
        text.addWidget(outputs)
        layout = QHBoxLayout()
        layout.setContentsMargins(theme.SPACING_XL, theme.SPACING_M, theme.SPACING_XL, theme.SPACING_M)
        layout.setSpacing(theme.SPACING_XL)
        layout.addWidget(icon, 0, Qt.AlignVCenter)
        layout.addLayout(text, 1)
        layout.addWidget(arrow, 0, Qt.AlignVCenter)
        card.setLayout(layout)
        for label in (icon, title, description, outputs, arrow):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        card.clicked.connect(
            lambda _=False, selected=choice.plan_type: self.start_requested.emit(selected)
        )
        return card

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
