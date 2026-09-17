"""Step bodies of an experiment that are not the image review itself."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.ui import theme


class SetupStep(QWidget):
    """What the experiment is: its type, protein, and name."""

    def __init__(self, editor, plan_type_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.type_label = QLabel(plan_type_label)
        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("Muted")
        protein_hint = QLabel(
            "Used for the experiment ID and MxLive records, e.g. BRD4 → FBS-BRD4-2026-001."
        )
        protein_hint.setObjectName("Muted")
        protein_hint.setWordWrap(True)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(theme.SPACING_M)
        form.addRow("Experiment type", self.type_label)
        form.addRow("Protein *", editor.protein_input)
        form.addRow("", protein_hint)
        form.addRow("Experiment name", editor.name_input)
        form.addRow("Workspace", self.workspace_label)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_M, 0, 0)
        layout.addLayout(form)
        layout.addStretch()
        self.setLayout(layout)


INSTRUMENT_COLUMNS = ("Instrument", "Worksheet", "Rows", "Saved to")


class WorksheetsStep(QWidget):
    """Every configured instrument that receives a worksheet, and the last save."""

    def __init__(self, mxlive_widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.instrument_table = QTableWidget(0, len(INSTRUMENT_COLUMNS))
        self.instrument_table.setHorizontalHeaderLabels(INSTRUMENT_COLUMNS)
        self.instrument_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.instrument_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.instrument_table.verticalHeader().setVisible(False)
        header = self.instrument_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.atomic_hint = QLabel(
            "All worksheets are saved together. If any folder cannot be written, "
            "none are saved."
        )
        self.atomic_hint.setObjectName("Muted")
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.hide()
        self.retry_button = QPushButton("Try again")
        self.choose_location_button = QPushButton("Save to another folder…")
        self.copy_paths_button = QPushButton("Copy paths")
        self._copy_text = ""
        result_actions = QHBoxLayout()
        result_actions.setContentsMargins(0, 0, 0, 0)
        for button in (self.retry_button, self.choose_location_button, self.copy_paths_button):
            button.hide()
            result_actions.addWidget(button)
        result_actions.addStretch()
        self.copy_paths_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self._copy_text)
        )

        self.mxlive_toggle = QToolButton()
        self.mxlive_toggle.setText("Send records to MxLive (optional)")
        self.mxlive_toggle.setCheckable(True)
        self.mxlive_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mxlive_toggle.setArrowType(Qt.RightArrow)
        self.mxlive_panel = mxlive_widget
        self.mxlive_panel.hide()
        self.mxlive_toggle.toggled.connect(self._toggle_mxlive)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_M, 0, 0)
        layout.setSpacing(theme.SPACING_M)
        layout.addWidget(self.instrument_table)
        layout.addWidget(self.atomic_hint)
        layout.addWidget(self.result_label)
        layout.addLayout(result_actions)
        layout.addWidget(self.mxlive_toggle)
        layout.addWidget(self.mxlive_panel, 1)
        layout.addStretch()
        self.setLayout(layout)

    def show_instruments(self, rows: tuple[tuple[str, str, str, str], ...]) -> None:
        self.instrument_table.setRowCount(len(rows))
        self.instrument_table.setFixedHeight(
            self.instrument_table.horizontalHeader().sizeHint().height()
            + sum(self.instrument_table.rowHeight(row) for row in range(len(rows)))
            + 2 * self.instrument_table.frameWidth()
        )
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.instrument_table.setItem(row, column, item)

    def show_result(
        self, text: str, kind: str, *, can_retry: bool = False, copy_text: str = ""
    ) -> None:
        self.result_label.setText(text)
        self.result_label.setStyleSheet(theme.status_style(kind))
        self.result_label.setVisible(bool(text))
        self.retry_button.setVisible(can_retry)
        self.choose_location_button.setVisible(can_retry)
        self._copy_text = copy_text
        self.copy_paths_button.setVisible(bool(copy_text))

    def _toggle_mxlive(self, visible: bool) -> None:
        self.mxlive_panel.setVisible(visible)
        self.mxlive_toggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
