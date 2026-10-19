"""Window-wide left panel: new experiments and workspaces as folders."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.ui import icons, theme
from xtalflow.ui.home_page import (
    ALL_EXPERIMENTS,
    EXPERIMENT_CHOICES,
    ExperimentChoice,
    WorkspaceEntry,
    panel_toggle_button,
)

SIDEBAR_STYLE = f"""
QWidget#WorkspaceSidebar {{ background: {theme.SIDEBAR}; border-right: 1px solid {theme.BORDER}; }}
QLabel#SidebarTitle {{ color: {theme.TEXT_MUTED}; font-weight: 600; }}
QPushButton#NewExperiment {{ background: transparent; border: none; border-radius: 8px;
    text-align: left; padding: 7px 10px; color: {theme.TEXT}; font-weight: 500; }}
QPushButton#NewExperiment:hover {{ background: {theme.SUBTLE}; }}
QPushButton#NewExperiment:pressed {{ background: {theme.SELECTED}; }}
QPushButton#NewExperiment:focus {{ background: {theme.SUBTLE}; }}
QFrame#SidebarDivider {{ border: none; border-top: 1px solid {theme.BORDER}; }}
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
QPushButton#ShareButton {{ background: transparent; border: none; border-radius: 6px;
    padding: 3px 6px; text-align: left; font-size: 12px; min-height: 24px;
    color: {theme.TEXT}; }}
QPushButton#ShareButton:hover {{ background: {theme.SUBTLE}; }}
QPushButton#ShareButton:pressed {{ background: {theme.SELECTED}; }}
QPushButton#ShareButton:focus {{ background: {theme.SUBTLE}; }}
QLabel#SharesStatus {{ color: {theme.TEXT_MUTED}; font-size: 11px; }}
QLabel#SharesLooking {{ color: {theme.TEXT_TERTIARY}; font-size: 11px; }}
"""

# The name stays plain and only the lamp is coloured, the way a panel is
# engraved and only its lights are lit. Lit is a filled disc with a tick in
# it, unlit an open ring with a mark: the difference is how much ink is on
# the screen, so it survives being unable to tell the two colours apart.
SHARE_LAMPS = {"ok": (True, theme.OK), "error": (False, theme.ERROR)}
LAMP_SIZE = 14


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


class WorkspaceSidebar(QWidget):
    start_requested = pyqtSignal(object)
    # A workspace id, or None for every experiment.
    workspace_selected = pyqtSignal(object)
    history_requested = pyqtSignal()
    create_workspace_requested = pyqtSignal()
    rename_workspace_requested = pyqtSignal(str, str)
    hide_workspace_requested = pyqtSignal(str)
    restore_workspace_requested = pyqtSignal(str)
    collapse_requested = pyqtSignal()
    # A share's own button was pressed: look at that one again, now.
    share_check_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkspaceSidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(SIDEBAR_STYLE)
        self.setFixedWidth(250)
        self._workspaces: tuple[WorkspaceEntry, ...] = ()
        self._selected_workspace_id: str | None = None

        app_name = QLabel("XtalFlow")
        app_name.setObjectName("SidebarTitle")
        self.collapse_button = panel_toggle_button("Hide panel (Ctrl+Shift+S)")
        top = QHBoxLayout()
        top.setContentsMargins(10, 0, 0, 0)
        top.addWidget(app_name)
        top.addStretch()
        top.addWidget(self.collapse_button)

        # Which shares are up decides what can be done at all, so it is the
        # first thing in the panel rather than something to go looking for.
        self.share_buttons: dict[str, QPushButton] = {}
        self._share_states: dict[str, object] = {}
        shares_title = QLabel("Shares")
        shares_title.setObjectName("SidebarTitle")
        # Beside the title, not under the lamps: a line that comes and goes
        # under them would shove the whole panel down on every reading.
        self.shares_looking = QLabel()
        self.shares_looking.setObjectName("SharesLooking")
        shares_header = QHBoxLayout()
        shares_header.setContentsMargins(10, 0, 0, theme.SPACING_S)
        shares_header.addWidget(shares_title)
        shares_header.addStretch()
        shares_header.addWidget(self.shares_looking)
        self.shares_grid = QGridLayout()
        self.shares_grid.setContentsMargins(4, 0, 0, 0)
        self.shares_grid.setHorizontalSpacing(theme.SPACING_S)
        self.shares_grid.setVerticalSpacing(0)
        self.shares_grid.setColumnStretch(0, 1)
        self.shares_grid.setColumnStretch(1, 1)
        shares = QVBoxLayout()
        shares.setSpacing(2)
        shares.addLayout(shares_header)
        shares.addLayout(self.shares_grid)
        self.shares_status = QLabel()
        self.shares_status.setObjectName("SharesStatus")
        self.shares_status.setContentsMargins(10, theme.SPACING_S, 0, 0)
        self.shares_status.hide()
        shares.addWidget(self.shares_status)
        shares_divider = QFrame()
        shares_divider.setObjectName("SidebarDivider")
        shares_divider.setFixedHeight(1)

        # New experiments sit at the top of the panel, like a chat app's New chat.
        # Keyed by choice: a plan type for the single-template types, else a name.
        self.start_buttons: dict[str, QPushButton] = {}
        new_title = QLabel("New experiment")
        new_title.setObjectName("SidebarTitle")
        new_title.setContentsMargins(10, 0, 0, theme.SPACING_S)
        new_buttons = QVBoxLayout()
        new_buttons.setSpacing(2)
        new_buttons.addWidget(new_title)
        for choice in EXPERIMENT_CHOICES:
            button = self._new_experiment_button(choice)
            self.start_buttons[choice.key] = button
            new_buttons.addWidget(button)
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)

        workspaces_title = QLabel("Workspaces")
        workspaces_title.setObjectName("SidebarTitle")
        self.new_workspace_button = QToolButton()
        self.new_workspace_button.setText("+")
        self.new_workspace_button.setToolTip("New workspace")
        self.new_workspace_button.setAccessibleName("New workspace")
        workspaces_header = QHBoxLayout()
        workspaces_header.setContentsMargins(10, 0, 0, 0)
        workspaces_header.addWidget(workspaces_title)
        workspaces_header.addStretch()
        workspaces_header.addWidget(self.new_workspace_button)
        self.workspace_list = QListWidget()
        self.workspace_list.setObjectName("WorkspaceList")
        self.workspace_list.setItemDelegate(_CountDelegate(self.workspace_list))
        self.workspace_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.workspace_list.setEditTriggers(
            QAbstractItemView.EditKeyPressed | QAbstractItemView.DoubleClicked
        )
        self.hidden_toggle = QPushButton()
        self.hidden_toggle.setObjectName("HiddenToggle")
        self.hidden_toggle.setCheckable(True)
        self.hidden_list = QListWidget()
        self.hidden_list.setObjectName("HiddenWorkspaceList")
        self.hidden_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.hidden_list.setToolTip("Double-click to show it in the list again")
        self.hidden_list.hide()

        # The third place to stand, beside new experiments and workspaces: what
        # MxLive holds, which is read-only and belongs to the account, not to a
        # folder on this computer.
        mxlive_title = QLabel("MxLive")
        mxlive_title.setObjectName("SidebarTitle")
        mxlive_title.setContentsMargins(10, 0, 0, theme.SPACING_S)
        self.history_button = QPushButton("Experiment history")
        self.history_button.setObjectName("NewExperiment")
        self.history_button.setCursor(Qt.PointingHandCursor)
        self.history_button.setIcon(
            QIcon(icons.svg_pixmap(icons.HISTORY, 18, theme.TEXT_MUTED))
        )
        self.history_button.setIconSize(QSize(18, 18))
        self.history_button.setToolTip(
            "Experiments MxLive holds for your account\n"
            "Read-only, including ones made on another computer"
        )
        self.history_button.setAccessibleName("MxLive experiment history")
        mxlive_divider = QFrame()
        mxlive_divider.setObjectName("SidebarDivider")
        mxlive_divider.setFixedHeight(1)

        layout = QVBoxLayout()
        layout.setContentsMargins(theme.SPACING_M, theme.SPACING_L, theme.SPACING_M, theme.SPACING_L)
        layout.setSpacing(theme.SPACING_S)
        layout.addLayout(top)
        layout.addSpacing(theme.SPACING_L)
        layout.addLayout(shares)
        layout.addSpacing(theme.SPACING_L)
        layout.addWidget(shares_divider)
        layout.addSpacing(theme.SPACING_L)
        layout.addLayout(new_buttons)
        layout.addSpacing(theme.SPACING_L)
        layout.addWidget(divider)
        layout.addSpacing(theme.SPACING_L)
        layout.addLayout(workspaces_header)
        layout.addWidget(self.workspace_list, 1)
        layout.addWidget(self.hidden_toggle)
        layout.addWidget(self.hidden_list)
        layout.addSpacing(theme.SPACING_L)
        layout.addWidget(mxlive_divider)
        layout.addSpacing(theme.SPACING_L)
        layout.addWidget(mxlive_title)
        layout.addWidget(self.history_button)
        self.setLayout(layout)

        self.collapse_button.clicked.connect(self.collapse_requested.emit)
        self.history_button.clicked.connect(self.history_requested.emit)
        self.new_workspace_button.clicked.connect(self.create_workspace_requested.emit)
        self.workspace_list.currentItemChanged.connect(self._workspace_item_changed)
        self.workspace_list.itemClicked.connect(self._workspace_item_clicked)
        self.workspace_list.itemChanged.connect(self._workspace_item_edited)
        self.workspace_list.customContextMenuRequested.connect(self._show_workspace_menu)
        self.hidden_toggle.toggled.connect(lambda _checked: self._refresh({}))
        self.hidden_list.itemDoubleClicked.connect(
            lambda item: self.restore_workspace_requested.emit(item.data(Qt.UserRole))
        )
        self.hidden_list.customContextMenuRequested.connect(self._show_hidden_menu)
        self._counts: dict[str | None, int] = {}
        self.show_workspaces((), None, {})

    def show_share_states(self, states: tuple, checking: bool = False) -> None:
        """Set each share's mark, colour and tooltip from a fresh reading.

        The buttons are made on the first reading, because which shares exist
        comes from the site's settings rather than from this file.
        """
        for index, state in enumerate(states):
            button = self.share_buttons.get(state.key)
            if button is None:
                button = QPushButton()
                button.setObjectName("ShareButton")
                button.setCursor(Qt.PointingHandCursor)
                button.setIconSize(QSize(LAMP_SIZE, LAMP_SIZE))
                button.setAccessibleDescription("Click to look again")
                button.clicked.connect(
                    lambda _checked=False, key=state.key: self.share_check_requested.emit(key)
                )
                self.share_buttons[state.key] = button
                self.shares_grid.addWidget(button, index // 2, index % 2)
            self._share_states[state.key] = state
            lit, colour = SHARE_LAMPS[state.state]
            button.setIcon(QIcon(icons.lamp_pixmap(lit, LAMP_SIZE, colour)))
            button.setText(state.label)
            # Only the share that is down is spelled out; the rest read as a
            # row of identical lamps, which is the point of a row of lamps.
            button.setStyleSheet(
                "" if lit else f"color: {theme.ERROR}; font-weight: 600;"
            )
            button.setToolTip(
                f"{state.label} · {state.detail}\n{state.path}\n\n"
                "Click to look again"
            )
            button.setAccessibleName(f"{state.label}: {state.detail}")
        # The buttons carry the names, so the line under them only counts:
        # it is there to be caught by the eye, not read twice.
        away = sum(1 for state in states if state.state != "ok")
        summary = f"{away} share{'' if away == 1 else 's'} not mounted" if away else ""
        self.shares_looking.setText("Looking…" if checking else "")
        self.shares_status.setText(summary)
        self.shares_status.setVisible(bool(summary))

    def share_state_detail(self, key: str) -> str:
        """What the last reading said about one share, for the status bar."""
        state = self._share_states.get(key)
        return f"{state.label} · {state.detail} · {state.path}" if state else ""

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
            lambda _=False, selected=choice.key: self.start_requested.emit(selected)
        )
        return button

    def show_workspaces(
        self,
        workspaces: tuple[WorkspaceEntry, ...],
        selected_id: str | None,
        counts: dict[str | None, int],
    ) -> None:
        """Folders, the highlighted one (None for all), and experiment counts.

        ``counts`` maps workspace ids to experiment counts; the None key is the total.
        """
        self._workspaces = workspaces
        self._selected_workspace_id = selected_id
        self._refresh(counts)

    def edit_workspace_name(self, workspace_id: str | None) -> None:
        for row in range(self.workspace_list.count()):
            item = self.workspace_list.item(row)
            if workspace_id is not None and item.data(Qt.UserRole) == workspace_id:
                self.workspace_list.setCurrentItem(item)
                self.workspace_list.editItem(item)
                return

    def _refresh(self, counts: dict[str | None, int]) -> None:
        if counts:
            self._counts = counts
        visible = tuple(item for item in self._workspaces if not item.hidden)
        hidden = tuple(item for item in self._workspaces if item.hidden)
        if self._selected_workspace_id not in {item.id for item in visible}:
            self._selected_workspace_id = None

        self.workspace_list.blockSignals(True)
        self.workspace_list.clear()
        everything = QListWidgetItem(ALL_EXPERIMENTS)
        everything.setData(Qt.UserRole, None)
        everything.setData(_CountDelegate.CountRole, self._counts.get(None, 0))
        self.workspace_list.addItem(everything)
        current = everything
        for workspace in visible:
            item = QListWidgetItem(workspace.name)
            item.setData(Qt.UserRole, workspace.id)
            item.setData(_CountDelegate.CountRole, self._counts.get(workspace.id, 0))
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

    def _workspace_item_changed(self, current, _previous) -> None:
        if current is None:
            return
        workspace_id = current.data(Qt.UserRole)
        if workspace_id != self._selected_workspace_id:
            self._selected_workspace_id = workspace_id
            self.workspace_selected.emit(workspace_id)

    def _workspace_item_clicked(self, item) -> None:
        # Clicking the highlighted folder from inside an experiment still returns to its list.
        if item.data(Qt.UserRole) == self._selected_workspace_id:
            self.workspace_selected.emit(self._selected_workspace_id)

    def _workspace_item_edited(self, item) -> None:
        workspace_id = item.data(Qt.UserRole)
        if workspace_id is None:
            return
        previous = next(
            (entry.name for entry in self._workspaces if entry.id == workspace_id), ""
        )
        name = item.text().strip()
        if name != previous:
            self.rename_workspace_requested.emit(workspace_id, name)

    def _show_workspace_menu(self, position) -> None:
        item = self.workspace_list.itemAt(position)
        if item is None or item.data(Qt.UserRole) is None:
            return
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
