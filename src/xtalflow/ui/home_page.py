"""Start screen: workspaces as folders, their experiments, and new experiment types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
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
QPushButton#NewExperiment {{ background: transparent; border: none; border-radius: 8px;
    text-align: left; padding: 7px 10px; color: {theme.TEXT}; font-weight: 500; }}
QPushButton#NewExperiment:hover {{ background: {theme.SUBTLE}; }}
QPushButton#NewExperiment:pressed {{ background: {theme.SELECTED}; }}
QPushButton#NewExperiment:focus {{ background: {theme.SUBTLE}; }}
QFrame#SidebarDivider {{ border: none; border-top: 1px solid {theme.BORDER}; }}
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
QWidget#HomeSidebar {{ background: {theme.SIDEBAR}; border-right: 1px solid {theme.BORDER}; }}
QLabel#SidebarTitle {{ color: {theme.TEXT_MUTED}; font-weight: 600; }}
QListWidget#WorkspaceList, QListWidget#HiddenWorkspaceList {{
    background: transparent; border: none; outline: none; }}
QListWidget#WorkspaceList::item, QListWidget#HiddenWorkspaceList::item {{
    color: {theme.TEXT}; padding: 6px 36px 6px 10px; border-radius: 8px; }}
QListWidget#HiddenWorkspaceList::item {{ color: {theme.TEXT_MUTED}; }}
QListWidget#WorkspaceList::item:hover, QListWidget#HiddenWorkspaceList::item:hover {{
    background: {theme.SUBTLE}; }}
QListWidget#WorkspaceList::item:selected, QListWidget#HiddenWorkspaceList::item:selected {{
    background: {theme.SELECTED}; color: {theme.TEXT}; }}
QPushButton#HiddenToggle {{ border: none; background: transparent; color: {theme.TEXT_MUTED};
    text-align: left; padding: 4px 10px; }}
QPushButton#HiddenToggle:hover {{ color: {theme.TEXT}; }}
"""

ALL_EXPERIMENTS = "All experiments"


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


class _CountDelegate(QStyledItemDelegate):
    """Draws each folder's experiment count at the right edge of its row."""

    CountRole = Qt.UserRole + 1

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        count = index.data(self.CountRole)
        if count is None:
            return
        painter.save()
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            option.rect.adjusted(0, 0, -12, 0), Qt.AlignRight | Qt.AlignVCenter, str(count)
        )
        painter.restore()


class HomePage(QWidget):
    start_requested = pyqtSignal(object)
    resume_requested = pyqtSignal(str, str)
    delete_requested = pyqtSignal(str, str)
    # A workspace id, or None for every experiment.
    workspace_selected = pyqtSignal(object)
    create_workspace_requested = pyqtSignal()
    rename_workspace_requested = pyqtSignal(str, str)
    hide_workspace_requested = pyqtSignal(str)
    restore_workspace_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExperimentHome")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(HOME_STYLE)
        self._workspaces: tuple[WorkspaceEntry, ...] = ()
        self._selected_workspace_id: str | None = None
        self._target_workspace_name = ""
        self._all_recent: tuple[RecentExperiment, ...] = ()
        self._recent: tuple[RecentExperiment, ...] = ()

        # New experiments sit at the top of the panel, like a chat app's New chat.
        self.start_buttons: dict[PlanType, QPushButton] = {}
        new_title = QLabel("New experiment")
        new_title.setObjectName("SidebarTitle")
        new_title.setContentsMargins(10, 0, 0, theme.SPACING_S)
        new_buttons = QVBoxLayout()
        new_buttons.setSpacing(2)
        new_buttons.addWidget(new_title)
        for choice in EXPERIMENT_CHOICES:
            button = self._new_experiment_button(choice)
            self.start_buttons[choice.plan_type] = button
            new_buttons.addWidget(button)
        self.new_target_label = QLabel()
        self.new_target_label.setObjectName("HomeMuted")
        self.new_target_label.setContentsMargins(10, 0, 0, 0)
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)

        # Folder panel: where experiments live, and where a new one will be made.
        sidebar_title = QLabel("Workspaces")
        sidebar_title.setObjectName("SidebarTitle")
        self.new_workspace_button = QToolButton()
        self.new_workspace_button.setText("+")
        self.new_workspace_button.setToolTip("New workspace")
        self.new_workspace_button.setAccessibleName("New workspace")
        sidebar_header = QHBoxLayout()
        sidebar_header.setContentsMargins(10, 0, 0, 0)
        sidebar_header.addWidget(sidebar_title)
        sidebar_header.addStretch()
        sidebar_header.addWidget(self.new_workspace_button)
        self.workspace_list = QListWidget()
        self.workspace_list.setObjectName("WorkspaceList")
        self.workspace_list.setItemDelegate(_CountDelegate(self.workspace_list))
        self.workspace_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.workspace_list.setEditTriggers(
            QAbstractItemView.EditKeyPressed | QAbstractItemView.DoubleClicked
        )
        self.workspace_list.setToolTip("Double-click or press F2 to rename")
        self.hidden_toggle = QPushButton()
        self.hidden_toggle.setObjectName("HiddenToggle")
        self.hidden_toggle.setCheckable(True)
        self.hidden_list = QListWidget()
        self.hidden_list.setObjectName("HiddenWorkspaceList")
        self.hidden_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.hidden_list.setToolTip("Double-click to show it in the list again")
        self.hidden_list.hide()
        sidebar_layout = QVBoxLayout()
        sidebar_layout.setContentsMargins(theme.SPACING_M, 32, theme.SPACING_M, theme.SPACING_L)
        sidebar_layout.setSpacing(theme.SPACING_S)
        sidebar_layout.addLayout(new_buttons)
        sidebar_layout.addWidget(self.new_target_label)
        sidebar_layout.addSpacing(theme.SPACING_L)
        sidebar_layout.addWidget(divider)
        sidebar_layout.addSpacing(theme.SPACING_L)
        sidebar_layout.addLayout(sidebar_header)
        sidebar_layout.addWidget(self.workspace_list, 1)
        sidebar_layout.addWidget(self.hidden_toggle)
        sidebar_layout.addWidget(self.hidden_list)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("HomeSidebar")
        self.sidebar.setAttribute(Qt.WA_StyledBackground, True)
        self.sidebar.setFixedWidth(250)
        self.sidebar.setLayout(sidebar_layout)

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
        content.setContentsMargins(40, 32, 40, 24)
        content.setSpacing(theme.SPACING_M)
        content.addLayout(heading)
        content.addSpacing(20)
        content.addLayout(recent_header)
        content.addWidget(self.recent_empty_label)
        content.addWidget(self.recent_table, 1)
        # Takes the free space while the recent table is hidden.
        self._bottom_stretch = QWidget()
        content.addWidget(self._bottom_stretch, 1)
        main = QWidget()
        main.setLayout(content)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(main, 1)
        self.setLayout(layout)

        self.recent_table.itemSelectionChanged.connect(self._selection_changed)
        self.delete_button.clicked.connect(
            lambda: self._delete_row(self.recent_table.currentRow())
        )
        self.recent_table.cellDoubleClicked.connect(lambda row, _: self._resume_row(row))
        self.resume_button.clicked.connect(
            lambda: self._resume_row(self.recent_table.currentRow())
        )
        self.recent_table.customContextMenuRequested.connect(self._show_recent_menu)
        self.new_workspace_button.clicked.connect(self.create_workspace_requested.emit)
        self.workspace_list.currentItemChanged.connect(self._workspace_item_changed)
        self.workspace_list.itemChanged.connect(self._workspace_item_edited)
        self.workspace_list.customContextMenuRequested.connect(self._show_workspace_menu)
        self.rename_workspace_action.triggered.connect(
            lambda: self.edit_workspace_name(self._selected_workspace_id)
        )
        self.hide_workspace_action.triggered.connect(
            lambda: self._selected_workspace_id
            and self.hide_workspace_requested.emit(self._selected_workspace_id)
        )
        self.hidden_toggle.toggled.connect(self._toggle_hidden)
        self.hidden_list.itemDoubleClicked.connect(
            lambda item: self.restore_workspace_requested.emit(item.data(Qt.UserRole))
        )
        self.hidden_list.customContextMenuRequested.connect(self._show_hidden_menu)
        self.show_workspaces((), None, "")

    def _new_experiment_button(self, choice: ExperimentChoice) -> QPushButton:
        button = QPushButton(choice.title)
        button.setObjectName("NewExperiment")
        button.setCursor(Qt.PointingHandCursor)
        button.setIcon(QIcon(icons.svg_pixmap(choice.icon, 18, choice.tint)))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(f"New {choice.title} experiment\n{choice.description}\n{choice.outputs}")
        button.setAccessibleName(f"New {choice.title} experiment")
        button.setAccessibleDescription(f"{choice.description} {choice.outputs}.")
        button.clicked.connect(
            lambda _=False, selected=choice.plan_type: self.start_requested.emit(selected)
        )
        return button

    def show_workspaces(
        self,
        workspaces: tuple[WorkspaceEntry, ...],
        selected_id: str | None,
        target_name: str,
    ) -> None:
        """Folders to list, the one being viewed (None for all), and where new work goes."""
        self._workspaces = workspaces
        self._selected_workspace_id = selected_id
        self._target_workspace_name = target_name
        self._refresh()

    def show_recent_work(self, experiments: tuple[RecentExperiment, ...]) -> None:
        self._all_recent = experiments
        self._refresh()

    def edit_workspace_name(self, workspace_id: str | None) -> None:
        for row in range(self.workspace_list.count()):
            item = self.workspace_list.item(row)
            if workspace_id is not None and item.data(Qt.UserRole) == workspace_id:
                self.workspace_list.setCurrentItem(item)
                self.workspace_list.editItem(item)
                return

    def _refresh(self) -> None:
        visible = tuple(item for item in self._workspaces if not item.hidden)
        hidden = tuple(item for item in self._workspaces if item.hidden)
        known = {item.id for item in visible}
        if self._selected_workspace_id not in known:
            self._selected_workspace_id = None
        counts: dict[str, int] = {}
        for experiment in self._all_recent:
            counts[experiment.workspace_id] = counts.get(experiment.workspace_id, 0) + 1

        self.workspace_list.blockSignals(True)
        self.workspace_list.clear()
        everything = QListWidgetItem(ALL_EXPERIMENTS)
        everything.setData(Qt.UserRole, None)
        everything.setData(_CountDelegate.CountRole, len(self._all_recent))
        self.workspace_list.addItem(everything)
        current = everything
        for workspace in visible:
            item = QListWidgetItem(workspace.name)
            item.setData(Qt.UserRole, workspace.id)
            item.setData(_CountDelegate.CountRole, counts.get(workspace.id, 0))
            item.setToolTip(f"{workspace.name} · double-click or F2 to rename")
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.workspace_list.addItem(item)
            if workspace.id == self._selected_workspace_id:
                current = item
        self.workspace_list.setCurrentItem(current)
        self.workspace_list.blockSignals(False)

        self.hidden_list.clear()
        for workspace in hidden:
            item = QListWidgetItem(workspace.name)
            item.setData(Qt.UserRole, workspace.id)
            self.hidden_list.addItem(item)
        self.hidden_toggle.setText(
            f"{'▾' if self.hidden_toggle.isChecked() else '▸'} Hidden ({len(hidden)})"
        )
        self.hidden_toggle.setVisible(bool(hidden))
        self.hidden_list.setVisible(bool(hidden) and self.hidden_toggle.isChecked())

        selected = next(
            (item for item in visible if item.id == self._selected_workspace_id), None
        )
        self.title_label.setText(selected.name if selected else ALL_EXPERIMENTS)
        self.workspace_actions_button.setVisible(selected is not None)
        self.hide_workspace_action.setEnabled(len(visible) > 1)
        self.new_target_label.setText(
            f"in {self._target_workspace_name}" if self._target_workspace_name else ""
        )
        self.new_target_label.setToolTip(
            f"New experiments are created in {self._target_workspace_name}"
        )

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

    def _workspace_item_changed(self, current, _previous) -> None:
        if current is None:
            return
        workspace_id = current.data(Qt.UserRole)
        if workspace_id != self._selected_workspace_id:
            self._selected_workspace_id = workspace_id
            self.workspace_selected.emit(workspace_id)

    def _workspace_item_edited(self, item) -> None:
        workspace_id = item.data(Qt.UserRole)
        if workspace_id is None:
            return
        previous = next((entry.name for entry in self._workspaces if entry.id == workspace_id), "")
        name = item.text().strip()
        if name != previous:
            self.rename_workspace_requested.emit(workspace_id, name)

    def _show_workspace_menu(self, position) -> None:
        item = self.workspace_list.itemAt(position)
        if item is None or item.data(Qt.UserRole) is None:
            return
        self.workspace_list.setCurrentItem(item)
        workspace_id = item.data(Qt.UserRole)
        menu = QMenu(self.workspace_list)
        menu.addAction("Rename").triggered.connect(lambda: self.workspace_list.editItem(item))
        hide = menu.addAction("Hide from list…")
        hide.setEnabled(sum(not entry.hidden for entry in self._workspaces) > 1)
        hide.triggered.connect(lambda: self.hide_workspace_requested.emit(workspace_id))
        menu.exec_(self.workspace_list.viewport().mapToGlobal(position))

    def _show_hidden_menu(self, position) -> None:
        item = self.hidden_list.itemAt(position)
        if item is None:
            return
        menu = QMenu(self.hidden_list)
        menu.addAction("Show in list").triggered.connect(
            lambda: self.restore_workspace_requested.emit(item.data(Qt.UserRole))
        )
        menu.exec_(self.hidden_list.viewport().mapToGlobal(position))

    def _toggle_hidden(self, _checked: bool) -> None:
        self._refresh()

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
