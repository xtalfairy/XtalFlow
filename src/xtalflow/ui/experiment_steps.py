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
    QVBoxLayout,
    QWidget,
)

from xtalflow.ui import theme


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PrimaryHeading")
    return label


class SetupStep(QWidget):
    """What the experiment is: its protein, name, type, and workspace."""

    def __init__(self, editor, plan_type_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.type_label = QLabel(plan_type_label)
        self.workspace_label = QLabel()
        # The ID the protein name produces, or why it cannot produce one, beside the field.
        self.experiment_id_label = QLabel()
        self.experiment_id_label.setObjectName("Muted")
        self.experiment_id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.protein_error_label = QLabel()
        self.protein_error_label.setStyleSheet(theme.status_style("attention"))
        self.protein_error_label.hide()
        editor.protein_input.setMaximumWidth(280)
        editor.name_input.setMaximumWidth(420)
        protein_row = QHBoxLayout()
        protein_row.setSpacing(theme.SPACING_L)
        protein_row.addWidget(editor.protein_input)
        protein_row.addWidget(self.experiment_id_label)
        protein_row.addStretch()
        protein_field = QVBoxLayout()
        protein_field.setSpacing(2)
        protein_field.addLayout(protein_row)
        protein_field.addWidget(self.protein_error_label)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setVerticalSpacing(theme.SPACING_L)
        form.setHorizontalSpacing(theme.SPACING_XL)
        form.addRow("Protein", protein_field)
        form.addRow("Name", editor.name_input)
        form.addRow("Type", self.type_label)
        form.addRow("Workspace", self.workspace_label)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_L, 0, 0)
        layout.addLayout(form)
        layout.addStretch()
        self.setLayout(layout)
        editor.protein_input.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        editor = self.editor
        protein = editor.protein_input.text().strip()
        if editor.assigned_experiment_id:
            self.experiment_id_label.setText(f"ID {editor.assigned_experiment_id} · fixed")
            error = ""
        elif editor.current_experiment_id:
            self.experiment_id_label.setText(f"ID when finalized: {editor.current_experiment_id}")
            error = ""
        else:
            self.experiment_id_label.setText("")
            # An empty name is not a mistake yet; the footer asks for it.
            message = editor.experiment_id_label.text()
            prefix = "Experiment ID: "
            error = message[len(prefix):] if protein and message.startswith(prefix) else ""
        self.protein_error_label.setText(error[:1].upper() + error[1:])
        self.protein_error_label.setVisible(bool(error))


INSTRUMENT_COLUMNS = ("Instrument", "File", "Rows", "Destination")


class WorksheetsStep(QWidget):
    """Instrument files for the finalized revision, then optional MxLive records."""

    def __init__(self, editor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.instrument_table = QTableWidget(0, len(INSTRUMENT_COLUMNS))
        self.instrument_table.setHorizontalHeaderLabels(INSTRUMENT_COLUMNS)
        self.instrument_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.instrument_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.instrument_table.verticalHeader().setVisible(False)
        self.instrument_table.setShowGrid(False)
        header = self.instrument_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.atomic_hint = QLabel(
            "Saved together: if any folder cannot be written, nothing is saved."
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
        result_row = QHBoxLayout()
        result_row.setSpacing(theme.SPACING_M)
        result_row.addWidget(self.result_label, 1)
        for button in (self.retry_button, self.choose_location_button, self.copy_paths_button):
            button.hide()
            result_row.addWidget(button)
        self.copy_paths_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self._copy_text)
        )

        # MxLive is a separate delivery with its own state; it never implies the files ran.
        self.mxlive_status_label = editor.webdb_status_label
        self.mxlive_table = editor.webdb_table
        self.mxlive_table.hide()
        self.upload_button = editor.webdb_upload_button
        self.mxlive_toggle = QPushButton("Show records")
        self.mxlive_toggle.setCheckable(True)
        self.mxlive_toggle.toggled.connect(self._toggle_mxlive)
        mxlive_header = QHBoxLayout()
        mxlive_header.setSpacing(theme.SPACING_M)
        mxlive_header.addWidget(_section_title("MxLive records"))
        mxlive_header.addWidget(self.mxlive_status_label, 1)
        mxlive_header.addWidget(self.mxlive_toggle)
        mxlive_header.addWidget(self.upload_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_M, 0, 0)
        layout.setSpacing(theme.SPACING_M)
        layout.addWidget(_section_title("Instrument worksheets"))
        layout.addWidget(self.instrument_table)
        layout.addWidget(self.atomic_hint)
        layout.addLayout(result_row)
        layout.addSpacing(theme.SPACING_XL)
        layout.addLayout(mxlive_header)
        layout.addWidget(self.mxlive_table, 1)
        self._stretch = QWidget()
        layout.addWidget(self._stretch, 1)
        self.setLayout(layout)

    def show_instruments(self, rows: tuple[tuple[str, str, str, str], ...]) -> None:
        self.instrument_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.instrument_table.setItem(row, column, item)
        self.instrument_table.setFixedHeight(
            self.instrument_table.horizontalHeader().sizeHint().height()
            + sum(self.instrument_table.rowHeight(row) for row in range(len(rows)))
            + 2 * self.instrument_table.frameWidth()
        )

    def show_result(
        self, text: str, kind: str, *, can_retry: bool = False, copy_text: str = ""
    ) -> None:
        self.result_label.setText(text)
        self.result_label.setStyleSheet(
            theme.status_style(kind) if kind in ("attention", "error")
            else f"color: {theme.TEXT_MUTED if kind == 'muted' else theme.TEXT};"
        )
        self.result_label.setVisible(bool(text))
        self.retry_button.setVisible(can_retry)
        self.choose_location_button.setVisible(can_retry)
        self._copy_text = copy_text
        self.copy_paths_button.setVisible(bool(copy_text))

    def _toggle_mxlive(self, visible: bool) -> None:
        self.mxlive_table.setVisible(visible)
        self._stretch.setVisible(not visible)
        self.mxlive_toggle.setText("Hide records" if visible else "Show records")
