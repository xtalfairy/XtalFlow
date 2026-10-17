"""Past experiments as MxLive holds them: a reading, never a plan.

Nothing on this page can be opened, edited, deleted or uploaded.  MxLive has
no way to update or delete a labwork, so an experiment that is already there
must stay there, and an experiment ID that was uploaded once must never be
uploaded again.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application.beamline_history import PastExperiment
from xtalflow.ui import theme
from xtalflow.ui.home_page import PLAN_TYPE_TITLES, panel_toggle_button


HISTORY_STYLE = f"""
QWidget#MxLiveHistory {{ background: {theme.BACKGROUND}; color: {theme.TEXT}; }}
QLabel#HistoryTitle {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HistorySection {{ color: {theme.TEXT}; font-weight: 600; }}
QLabel#HistoryMuted {{ color: {theme.TEXT_MUTED}; }}
QLabel#HistoryAttention {{ color: {theme.ATTENTION}; }}
QTableWidget#HistoryTable, QTableWidget#CrystalTable {{ background: {theme.BACKGROUND};
    border: none; selection-background-color: {theme.SELECTED};
    selection-color: {theme.TEXT}; }}
QTableWidget#HistoryTable::item, QTableWidget#CrystalTable::item {{
    padding: 0 10px; border-bottom: 1px solid {theme.BORDER}; }}
QTableWidget#HistoryTable::item:hover {{ background: {theme.SUBTLE}; }}
QTableWidget#HistoryTable QHeaderView::section,
QTableWidget#CrystalTable QHeaderView::section {{ background: {theme.BACKGROUND};
    color: {theme.TEXT_MUTED}; border: none; padding: 8px 10px; font-weight: 400; }}
QLabel#HistoryEmpty {{ color: {theme.TEXT_MUTED}; background: transparent;
    padding: 28px; }}
"""

EXPERIMENT_COLUMNS = (
    "Experiment ID", "Type", "Protein", "Crystals", "Harvested", "Pucks",
    "Month", "Source",
)
CRYSTAL_COLUMNS = (
    "#", "Plate", "Well", "Soak", "SMILES", "Volume", "Harvested",
    "Puck (at harvest)", "Position", "Comment",
)


def _text(record, key: str) -> str:
    value = record.raw.get(key)
    if value is None or value in ("None", ""):
        return ""
    return str(value)


class MxLiveHistoryPage(QWidget):
    """Read-only: experiments MxLive holds for the signed-in account."""

    refresh_requested = pyqtSignal()
    earlier_years_requested = pyqtSignal()
    show_panel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MxLiveHistory")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(HISTORY_STYLE)
        self._experiments: tuple[PastExperiment, ...] = ()

        self.panel_button = panel_toggle_button("Show panel (Ctrl+Shift+S)")
        self.panel_button.hide()
        self.title_label = QLabel("Experiment history")
        self.title_label.setObjectName("HistoryTitle")
        font = self.title_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.8)
        self.title_label.setFont(font)
        heading = QHBoxLayout()
        heading.setSpacing(theme.SPACING_S)
        heading.addWidget(self.panel_button, 0, Qt.AlignVCenter)
        heading.addWidget(self.title_label)
        heading.addStretch()

        self.years_label = QLabel()
        self.years_label.setObjectName("HistoryMuted")
        self.earlier_button = QPushButton("Load earlier years")
        self.refresh_button = QPushButton("Refresh")
        self.status_label = QLabel()
        self.status_label.setObjectName("HistoryMuted")
        controls = QHBoxLayout()
        controls.setSpacing(theme.SPACING_M)
        controls.addWidget(self.years_label)
        controls.addWidget(self.status_label, 1)
        controls.addWidget(self.earlier_button)
        controls.addWidget(self.refresh_button)

        self.experiment_table = self._table("HistoryTable", EXPERIMENT_COLUMNS)
        self.experiment_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("HistoryEmpty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("HistorySection")
        self.detail_label.setWordWrap(True)
        self.crystal_table = self._table("CrystalTable", CRYSTAL_COLUMNS)
        self.note_label = QLabel(
            "Read from MxLive, which records harvesting but nothing after it. "
            "These experiments cannot be opened or changed here."
        )
        self.note_label.setObjectName("HistoryMuted")
        self.note_label.setWordWrap(True)

        content = QVBoxLayout()
        content.setContentsMargins(32, 24, 32, 20)
        content.setSpacing(theme.SPACING_M)
        content.addLayout(heading)
        content.addSpacing(12)
        content.addLayout(controls)
        content.addWidget(self.empty_label)
        content.addWidget(self.experiment_table, 3)
        content.addWidget(self.detail_label)
        content.addWidget(self.crystal_table, 2)
        content.addWidget(self.note_label)
        self.setLayout(content)

        self.experiment_table.itemSelectionChanged.connect(self._selection_changed)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.earlier_button.clicked.connect(self.earlier_years_requested.emit)
        self.panel_button.clicked.connect(self.show_panel_requested.emit)
        self.show_experiments(())

    @staticmethod
    def _table(name: str, columns: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setObjectName(name)
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        return table

    def show_account(self, username: str, beamline: str) -> None:
        """Say whose records these are: the key decides what MxLive returns."""
        who = f"{username}'s experiments" if username else "Experiments"
        self.title_label.setText(f"{who} on {beamline}" if beamline else who)

    def show_years(self, years: tuple[int, ...], earlier_available: bool = True) -> None:
        self.years_label.setText(
            " · ".join(str(year) for year in sorted(years, reverse=True))
            or "No years read yet"
        )
        self.earlier_button.setEnabled(earlier_available)

    def show_status(self, message: str, kind: str = "muted") -> None:
        self.status_label.setText(message)
        self.status_label.setObjectName(
            "HistoryAttention" if kind == "attention" else "HistoryMuted"
        )
        self.status_label.setStyleSheet(
            f"color: {theme.ATTENTION};" if kind == "attention"
            else f"color: {theme.TEXT_MUTED};"
        )

    def show_experiments(self, experiments: tuple[PastExperiment, ...]) -> None:
        self._experiments = experiments
        self.experiment_table.setRowCount(len(experiments))
        for row, experiment in enumerate(experiments):
            for column, value in enumerate(self._row(experiment)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.experiment_table.setItem(row, column, item)
        self.experiment_table.setVisible(bool(experiments))
        self.empty_label.setVisible(not experiments)
        if not experiments:
            self.empty_label.setText(
                "No experiments on MxLive for this account yet.\n\n"
                "What appears here is what was uploaded under your own MxLive "
                "key — your own experiments, from this computer or another."
            )
        self._selection_changed()

    @staticmethod
    def _row(experiment: PastExperiment) -> tuple[str, ...]:
        kind = (
            PLAN_TYPE_TITLES.get(experiment.plan_type, "")
            if experiment.plan_type is not None else experiment.type_name
        )
        return (
            experiment.experiment_id,
            kind or "Unknown",
            experiment.protein,
            str(experiment.crystals),
            f"{experiment.harvested} of {experiment.crystals}"
            if experiment.harvested else "not yet",
            ", ".join(experiment.pucks) if len(experiment.pucks) <= 3
            else f"{len(experiment.pucks)} pucks",
            experiment.month,
            experiment.source,
        )

    def _selection_changed(self) -> None:
        row = self.experiment_table.currentRow()
        if not 0 <= row < len(self._experiments):
            self.detail_label.setText("")
            self.crystal_table.setRowCount(0)
            self.crystal_table.hide()
            return
        experiment = self._experiments[row]
        self.detail_label.setText(
            f"{experiment.experiment_id} · {experiment.protein} · "
            f"{len(experiment.records)} crystals · uploaded from the account "
            f"{experiment.uploaded_by or 'unknown'}"
        )
        self.detail_label.setToolTip(
            "The login name on the computer that uploaded it, not a person."
        )
        records = sorted(
            experiment.records, key=lambda item: int(_text(item, "crystal_no") or 0)
        )
        self.crystal_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = (
                _text(record, "crystal_no"),
                record.plate_code or "",
                record.plate_well or "",
                _text(record, "soak_id"),
                _text(record, "soak_smile"),
                _text(record, "soak_vol"),
                record.harvest_time or "",
                record.puck_name or "",
                _text(record, "puck_nohole"),
                _text(record, "harvest_comment"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.crystal_table.setItem(row, column, item)
        self.crystal_table.show()
