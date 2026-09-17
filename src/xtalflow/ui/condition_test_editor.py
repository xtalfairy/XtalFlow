"""Condition test editor: a notebook-style concentration × time table per additive."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.domain.condition_test import (
    Additive,
    Condition,
    ConditionTestDesign,
    ConditionTestPlan,
    Treatment,
    build_condition_test_plan,
    compute_doses,
    format_minutes,
    new_series,
    parse_minutes,
)
from xtalflow.domain.crystal_workflow import AssignmentOrder
from xtalflow.domain.labwork import build_condition_test_labworks
from xtalflow.domain.plan_lifecycle import PlanningDraft
from xtalflow.domain.worksheets import ECHO_HEADER, build_condition_echo_rounds, build_shifter_worksheet
from xtalflow.ui import theme
from xtalflow.ui.help_button import HelpButton
from xtalflow.ui.plan_editors import PlanEditorBase, _plural, _preview_table

CELL_WIDTH = 64
CELL_HEIGHT = 30

CONDITIONS_STYLE = f"""
QPushButton#SeriesChip {{ border: 1px solid {theme.BORDER}; border-radius: 14px;
    padding: 3px 14px; background: {theme.SURFACE}; }}
QPushButton#SeriesChip:checked {{ background: {theme.SELECTED}; border-color: {theme.BORDER_STRONG};
    font-weight: 600; }}
QPushButton#LinkButton {{ border: none; background: transparent; color: {theme.TEXT_MUTED};
    padding: 3px 6px; }}
QPushButton#LinkButton:hover {{ color: {theme.TEXT}; }}
QTableWidget#ConditionMatrix {{ border: 1px solid {theme.BORDER_STRONG}; border-radius: 0;
    gridline-color: {theme.BORDER_STRONG}; background: {theme.SURFACE}; }}
QTableWidget#ConditionMatrix QHeaderView::section {{ background: {theme.SIDEBAR};
    color: {theme.TEXT}; border: none; border-right: 1px solid {theme.BORDER_STRONG};
    border-bottom: 1px solid {theme.BORDER_STRONG}; padding: 4px 6px; font-weight: 500; }}
"""


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}"


class AdditiveDialog(QDialog):
    """Everything about one additive and what, if anything, comes before it."""

    def __init__(self, design: ConditionTestDesign, series, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        additive = design.additive(series.additive_id)
        self.setWindowTitle(f"Edit {additive.name}")
        self.name_input = QLineEdit(additive.name)
        self.stock_input = QDoubleSpinBox()
        self.stock_input.setRange(0.1, 100)
        self.stock_input.setDecimals(1)
        self.stock_input.setSuffix(" %")
        self.stock_input.setValue(float(additive.stock_percent))
        self.source_plate_input = QLineEdit(additive.source_plate)
        self.source_plate_input.setPlaceholderText("e.g. LDV-01")
        self.source_well_input = QLineEdit(additive.source_well)
        self.source_well_input.setPlaceholderText("e.g. A1")
        self.source_well_input.setMaximumWidth(80)
        self.smiles_input = QLineEdit(additive.smiles)
        self.smiles_input.setPlaceholderText("Optional, sent to MxLive")
        self.before_additive_input = QComboBox()
        self.before_additive_input.addItem("None", None)
        for item in design.additives:
            if item.id != additive.id:
                self.before_additive_input.addItem(item.name, item.id)
        before = series.before[0] if series.before else None
        self.before_percent_input = QDoubleSpinBox()
        self.before_percent_input.setRange(0, 99.9)
        self.before_percent_input.setDecimals(1)
        self.before_percent_input.setSuffix(" %")
        self.before_minutes_input = QLineEdit()
        self.before_minutes_input.setPlaceholderText("e.g. 1 h")
        if before is not None:
            self.before_additive_input.setCurrentIndex(
                max(0, self.before_additive_input.findData(before.additive_id))
            )
            self.before_percent_input.setValue(float(before.final_percent))
            self.before_minutes_input.setText(format_minutes(before.minutes))
        source = QHBoxLayout()
        source.addWidget(self.source_plate_input, 1)
        source.addWidget(self.source_well_input)
        before_row = QHBoxLayout()
        before_row.addWidget(self.before_additive_input, 1)
        before_row.addWidget(QLabel("at"))
        before_row.addWidget(self.before_percent_input)
        before_row.addWidget(QLabel("for"))
        before_row.addWidget(self.before_minutes_input)
        form = QFormLayout()
        form.addRow("Name", self.name_input)
        form.addRow("Stock", self.stock_input)
        form.addRow("Source plate / well", source)
        form.addRow("SMILES", self.smiles_input)
        form.addRow("Earlier treatment", before_row)
        hint = QLabel("An earlier treatment is added first and soaks for its time.")
        hint.setObjectName("Muted")
        form.addRow("", hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.before_additive_input.currentIndexChanged.connect(self._before_changed)
        self._before_changed()

    def _before_changed(self, *_args) -> None:
        enabled = self.before_additive_input.currentData() is not None
        self.before_percent_input.setEnabled(enabled)
        self.before_minutes_input.setEnabled(enabled)


class ConditionTestEditor(PlanEditorBase):
    """Solvent and cryo tests: conditions first, then one well per replicate."""

    def __init__(self, design: ConditionTestDesign, parent: QWidget | None = None) -> None:
        super().__init__(None, "transient-condition-editor", parent)
        self.design = design
        self.design_error: str | None = None
        self.selection_error: str | None = None
        self._series_index = 0

        self.drop_volume_input = QDoubleSpinBox()
        self.drop_volume_input.setRange(0, 100000)
        self.drop_volume_input.setDecimals(1)
        self.drop_volume_input.setSingleStep(10)
        self.drop_volume_input.setSuffix(" nL")
        self.drop_volume_input.setSpecialValueText("Not set")
        self.drop_volume_input.setMaximumWidth(160)

        # One chip per additive; most tests have just one.
        self.series_group = QButtonGroup(self)
        self.series_group.setExclusive(True)
        self.series_row = QHBoxLayout()
        self.series_row.setSpacing(theme.SPACING_S)
        self.add_series_button = QPushButton("+ Add additive")
        self.add_series_button.setObjectName("LinkButton")
        self.additive_summary_label = QLabel()
        self.additive_summary_label.setTextFormat(Qt.RichText)
        self.edit_additive_button = QPushButton("Edit…")
        self.remove_series_button = QPushButton("Remove")
        self.remove_series_button.setObjectName("LinkButton")

        self.matrix_table = QTableWidget()
        self.matrix_table.setObjectName("ConditionMatrix")
        self.matrix_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.matrix_table.setFocusPolicy(Qt.NoFocus)
        self.matrix_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.matrix_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.matrix_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.matrix_table.setCornerButtonEnabled(False)
        horizontal = self.matrix_table.horizontalHeader()
        horizontal.setContextMenuPolicy(Qt.CustomContextMenu)
        horizontal.setSectionResizeMode(QHeaderView.Fixed)
        horizontal.setDefaultSectionSize(CELL_WIDTH)
        horizontal.setDefaultAlignment(Qt.AlignCenter)
        vertical = self.matrix_table.verticalHeader()
        vertical.setContextMenuPolicy(Qt.CustomContextMenu)
        vertical.setSectionResizeMode(QHeaderView.Fixed)
        vertical.setDefaultSectionSize(CELL_HEIGHT)
        vertical.setDefaultAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.add_percent_button = QToolButton()
        self.add_percent_button.setText("+ %")
        self.add_percent_button.setToolTip("Add a final concentration column")
        self.add_time_button = QToolButton()
        self.add_time_button.setText("+ time")
        self.add_time_button.setToolTip("Add a soak time row")
        self.dose_label = QLabel()
        self.dose_label.setWordWrap(True)
        self.dose_label.setObjectName("Muted")

        self.budget_label = QLabel()
        self.available_input = QSpinBox()
        self.available_input.setRange(0, 9999)
        self.available_input.setSpecialValueText("—")
        self.available_input.setMaximumWidth(90)
        self.reduce_button = QPushButton()
        self.reduce_button.hide()
        self.design_error_label = QLabel()
        self.design_error_label.setWordWrap(True)
        self.design_error_label.setStyleSheet(theme.status_style("attention"))
        self.design_error_label.hide()

        self.assignment_table = _preview_table(
            ("Well", "Condition", "Replicate", "Additions", "Harvest"), stretch_last=True
        )
        self.schedule_table = _preview_table(("Time", "Action", "Wells"), stretch_last=True)
        self.echo_table = _preview_table(("Round", *ECHO_HEADER))
        self.preview_tabs.addTab(self.assignment_table, "Assignments")
        self.preview_tabs.addTab(self.schedule_table, "Schedule")
        self.preview_tabs.addTab(self.echo_table, "ECHO worksheets")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER worksheet")

        self.drop_volume_input.valueChanged.connect(self._drop_volume_changed)
        self.series_group.idClicked.connect(self._series_changed)
        self.add_series_button.clicked.connect(self._add_series)
        self.edit_additive_button.clicked.connect(self.edit_additive)
        self.remove_series_button.clicked.connect(lambda: self._remove_series(self._series_index))
        self.matrix_table.cellClicked.connect(self._toggle_cell)
        self.matrix_table.customContextMenuRequested.connect(self._show_cell_menu)
        horizontal.customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=True)
        )
        vertical.customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=False)
        )
        self.add_percent_button.clicked.connect(self._ask_percent)
        self.add_time_button.clicked.connect(self._ask_time)
        self.available_input.valueChanged.connect(self._available_changed)
        self.reduce_button.clicked.connect(self._reduce_replicates)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self._load_design_inputs()
        self.refresh_plan()

    # -- Step widgets -------------------------------------------------------------

    def setup_fields(self) -> list[tuple[str, QWidget]]:
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.drop_volume_input)
        layout.addWidget(HelpButton(
            "Volume of the crystallization drop before any additive. Final "
            "concentrations are calculated from it."
        ))
        layout.addStretch()
        row.setLayout(layout)
        return [("Drop volume", row)]

    def conditions_widget(self) -> QWidget:
        """Additive on one line, then the table, then what it dispenses and costs."""
        chips = QHBoxLayout()
        chips.setSpacing(theme.SPACING_S)
        chips.addLayout(self.series_row)
        chips.addWidget(self.add_series_button)
        chips.addStretch()

        additive = QHBoxLayout()
        additive.setSpacing(theme.SPACING_M)
        additive.addWidget(self.additive_summary_label)
        additive.addWidget(self.edit_additive_button)
        additive.addWidget(self.remove_series_button)
        additive.addStretch()

        caption = QHBoxLayout()
        caption_label = QLabel("Rows: soak time · Columns: final concentration · click a cell to include or exclude it")
        caption_label.setObjectName("Muted")
        caption.addWidget(caption_label)
        caption.addWidget(HelpButton(
            "Each number is how many crystals get that condition. Right-click a cell "
            "to change it, or a row or column header to change or remove the whole "
            "row or column. Excluded cells stay in the table as –. 0 % is the control."
        ))
        caption.addStretch()

        table_row = QHBoxLayout()
        table_row.setSpacing(theme.SPACING_S)
        table_row.addWidget(self.matrix_table, 0, Qt.AlignTop)
        table_row.addWidget(self.add_percent_button, 0, Qt.AlignTop)
        table_row.addStretch()

        budget = QHBoxLayout()
        budget.setSpacing(theme.SPACING_M)
        budget.addWidget(self.budget_label)
        budget.addSpacing(theme.SPACING_L)
        available = QLabel("Crystals available")
        available.setObjectName("Muted")
        budget.addWidget(available)
        budget.addWidget(self.available_input)
        budget.addWidget(self.reduce_button)
        budget.addStretch()

        widget = QWidget()
        widget.setStyleSheet(CONDITIONS_STYLE)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_S, 0, 0)
        layout.setSpacing(theme.SPACING_M)
        layout.addLayout(chips)
        layout.addLayout(additive)
        layout.addWidget(self.design_error_label)
        layout.addSpacing(theme.SPACING_S)
        layout.addLayout(caption)
        layout.addLayout(table_row)
        layout.addWidget(self.add_time_button, 0, Qt.AlignLeft)
        layout.addWidget(self.dose_label)
        layout.addSpacing(theme.SPACING_S)
        layout.addLayout(budget)
        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def review_widget(self) -> QWidget:
        widget = super().review_widget()
        order = QHBoxLayout()
        order.addWidget(QLabel("Well order"))
        order.addWidget(self.order_input)
        order.addStretch()
        widget.layout().insertLayout(2, order)
        return widget

    # -- Design edits ---------------------------------------------------------------

    @property
    def _series(self):
        return self.design.series[self._series_index]

    def _replace_series(self, series) -> None:
        items = list(self.design.series)
        items[self._series_index] = series
        self.design = replace(self.design, series=tuple(items))

    def _load_design_inputs(self) -> None:
        self.drop_volume_input.blockSignals(True)
        self.drop_volume_input.setValue(float(self.design.drop_volume_nl or 0))
        self.drop_volume_input.blockSignals(False)
        self.available_input.blockSignals(True)
        self.available_input.setValue(self.design.available_crystals or 0)
        self.available_input.blockSignals(False)
        self._series_index = min(self._series_index, len(self.design.series) - 1)
        self._render_chips()

    def _render_chips(self) -> None:
        for button in self.series_group.buttons():
            self.series_group.removeButton(button)
            self.series_row.removeWidget(button)
            # Hide now: a removed chip would otherwise paint until deleteLater runs.
            button.hide()
            button.deleteLater()
        for index, series in enumerate(self.design.series):
            chip = QPushButton(self.design.additive(series.additive_id).name)
            chip.setObjectName("SeriesChip")
            chip.setCheckable(True)
            chip.setChecked(index == self._series_index)
            self.series_group.addButton(chip, index)
            self.series_row.addWidget(chip)
        self.remove_series_button.setVisible(len(self.design.series) > 1)

    def _series_changed(self, index: int) -> None:
        if 0 <= index < len(self.design.series) and index != self._series_index:
            self._series_index = index
            self._render_chips()
            self.refresh_plan()

    def _add_series(self) -> None:
        number = len(self.design.additives) + 1
        additive = Additive(str(uuid4()), f"Additive {number}")
        series = new_series(additive.id, (Decimal("0"), Decimal("10")), (0,))
        self.design = replace(
            self.design,
            additives=(*self.design.additives, additive),
            series=(*self.design.series, series),
        )
        self._series_index = len(self.design.series) - 1
        self._load_design_inputs()
        self.refresh_plan()

    def _remove_series(self, index: int) -> None:
        if len(self.design.series) <= 1:
            return
        removed = self.design.series[index]
        series = tuple(item for position, item in enumerate(self.design.series) if position != index)
        used = {item.additive_id for item in series} | {
            step.additive_id for item in series for step in item.before
        }
        additives = tuple(
            item for item in self.design.additives
            if item.id != removed.additive_id or item.id in used
        )
        self.design = replace(self.design, additives=additives, series=series)
        self._series_index = max(0, min(self._series_index, len(series) - 1))
        self._load_design_inputs()
        self.refresh_plan()

    def edit_additive(self) -> None:
        dialog = AdditiveDialog(self.design, self._series, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            minutes = parse_minutes(dialog.before_minutes_input.text() or "0")
        except ValueError:
            minutes = 0
        before_id = dialog.before_additive_input.currentData()
        self.update_additive(
            name=dialog.name_input.text(),
            stock_percent=Decimal(str(dialog.stock_input.value())),
            source_plate=dialog.source_plate_input.text(),
            source_well=dialog.source_well_input.text(),
            smiles=dialog.smiles_input.text(),
            before=(
                (Treatment(before_id, Decimal(str(dialog.before_percent_input.value())), minutes),)
                if before_id is not None else ()
            ),
        )

    def update_additive(self, *, before=None, **changes) -> None:
        """Apply edits to the current additive; text fields are trimmed, wells upper-cased."""
        current = self.design.additive(self._series.additive_id)
        cleaned = {
            key: (value.strip().upper() if key == "source_well" else value.strip())
            if isinstance(value, str) else value
            for key, value in changes.items()
        }
        if not cleaned.get("name", current.name):
            cleaned.pop("name")
        try:
            updated = replace(current, **cleaned)
        except (ValueError, InvalidOperation) as error:
            self._show_design_error(str(error))
            return
        self.design = replace(
            self.design,
            additives=tuple(updated if item.id == current.id else item for item in self.design.additives),
        )
        if before is not None:
            self._replace_series(replace(self._series, before=tuple(before)))
        self._render_chips()
        self.refresh_plan()

    def _drop_volume_changed(self, value: float) -> None:
        self.design = replace(
            self.design, drop_volume_nl=Decimal(str(value)) if value > 0 else None
        )
        self.refresh_plan()

    def _available_changed(self, value: int) -> None:
        self.design = replace(self.design, available_crystals=value or None)
        self.refresh_plan()

    def _toggle_cell(self, row: int, column: int) -> None:
        series = self._series
        cell = series.cell(series.percents[column], series.times[row])
        if cell is not None:
            self._replace_series(series.with_cell(replace(cell, included=not cell.included)))
            self.refresh_plan()

    def _set_cells(self, cells, **changes) -> None:
        series = self._series
        for cell in cells:
            series = series.with_cell(replace(cell, **changes))
        self._replace_series(series)
        self.refresh_plan()

    def _show_cell_menu(self, position) -> None:
        index = self.matrix_table.indexAt(position)
        if not index.isValid():
            return
        series = self._series
        cell = series.cell(series.percents[index.column()], series.times[index.row()])
        menu = QMenu(self.matrix_table)
        toggle = menu.addAction("Exclude" if cell.included else "Include")
        toggle.triggered.connect(lambda: self._set_cells((cell,), included=not cell.included))
        replicates = menu.addMenu("Crystals")
        for count in range(1, 7):
            action = replicates.addAction(str(count))
            action.setCheckable(True)
            action.setChecked(count == cell.replicates)
            action.triggered.connect(
                lambda _=False, value=count: self._set_cells((cell,), replicates=value, included=True)
            )
        menu.exec_(self.matrix_table.viewport().mapToGlobal(position))

    def _show_axis_menu(self, position, columns: bool) -> None:
        header = self.matrix_table.horizontalHeader() if columns else self.matrix_table.verticalHeader()
        section = header.logicalIndexAt(position)
        series = self._series
        if section < 0:
            return
        if columns:
            value = series.percents[section]
            cells = tuple(item for item in series.cells if item.percent == value)
        else:
            value = series.times[section]
            cells = tuple(item for item in series.cells if item.minutes == value)
        menu = QMenu(header)
        menu.addAction("Include all").triggered.connect(lambda: self._set_cells(cells, included=True))
        menu.addAction("Exclude all").triggered.connect(lambda: self._set_cells(cells, included=False))
        replicates = menu.addMenu("Crystals")
        for count in range(1, 7):
            replicates.addAction(str(count)).triggered.connect(
                lambda _=False, number=count: self._set_cells(cells, replicates=number)
            )
        menu.addSeparator()
        remove = menu.addAction("Remove column" if columns else "Remove row")
        axis = series.percents if columns else series.times
        remove.setEnabled(len(axis) > 1)
        remove.triggered.connect(lambda: self._remove_axis_value(value, columns))
        menu.exec_(header.mapToGlobal(position))

    def _remove_axis_value(self, value, columns: bool) -> None:
        series = self._series
        if columns:
            updated = series.with_axes(tuple(item for item in series.percents if item != value), series.times)
        else:
            updated = series.with_axes(series.percents, tuple(item for item in series.times if item != value))
        self._replace_series(updated)
        self.refresh_plan()

    def _ask_percent(self) -> None:
        text, accepted = QInputDialog.getText(
            self, "Add concentration", "Final concentration (%):"
        )
        if accepted:
            self.add_percent(text)

    def _ask_time(self) -> None:
        text, accepted = QInputDialog.getText(
            self, "Add soak time", "Soak time (e.g. 30, 90 min, 2 h):"
        )
        if accepted:
            self.add_time(text)

    def add_percent(self, text: str) -> None:
        try:
            value = Decimal(text.strip().rstrip("%").strip())
            if value < 0 or value >= 100:
                raise ValueError
        except (InvalidOperation, ValueError):
            self._show_design_error("Enter a final concentration from 0 to below 100 %.")
            return
        series = self._series
        self._replace_series(series.with_axes((*series.percents, value), series.times))
        self.refresh_plan()

    def add_time(self, text: str) -> None:
        try:
            minutes = parse_minutes(text)
        except ValueError:
            self._show_design_error("Enter a soak time such as 30, 90 min, or 2 h.")
            return
        series = self._series
        self._replace_series(series.with_axes(series.percents, (*series.times, minutes)))
        self.refresh_plan()

    def _reduce_replicates(self) -> None:
        series = tuple(
            replace(item, cells=tuple(replace(cell, replicates=1) for cell in item.cells))
            for item in self.design.series
        )
        self.design = replace(self.design, series=series)
        self.refresh_plan()

    def _show_design_error(self, message: str) -> None:
        self.design_error_label.setText(message)
        self.design_error_label.setVisible(bool(message))

    # -- Rendering -------------------------------------------------------------------

    def _render_additive_summary(self) -> None:
        series = self._series
        additive = self.design.additive(series.additive_id)
        parts = [f"<b>{additive.name}</b>", f"{_percent(additive.stock_percent)} % stock"]
        if additive.source_plate and additive.source_well:
            parts.append(f"source {additive.source_plate} {additive.source_well}")
        else:
            parts.append(f'<span style="color:{theme.ATTENTION}">source not set</span>')
        for step in series.before:
            earlier = self.design.additive(step.additive_id).name
            parts.append(
                f"after {earlier} {_percent(step.final_percent)} % for {format_minutes(step.minutes)}"
            )
        self.additive_summary_label.setText(" · ".join(parts))

    def _render_matrix(self) -> None:
        series = self._series
        table = self.matrix_table
        table.clear()
        table.setColumnCount(len(series.percents))
        table.setRowCount(len(series.times))
        table.setHorizontalHeaderLabels([f"{_percent(percent)} %" for percent in series.percents])
        table.setVerticalHeaderLabels([format_minutes(minutes) for minutes in series.times])
        conditions = {condition.id: condition for condition in series.conditions()}
        for row, minutes in enumerate(series.times):
            for column, percent in enumerate(series.percents):
                cell = series.cell(percent, minutes)
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                if cell is not None and cell.included:
                    item.setText(str(cell.replicates))
                    item.setToolTip(
                        f"{self.design.label(conditions[cell.id])} · "
                        f"{_plural(cell.replicates, 'crystal')}\nClick to exclude"
                    )
                elif cell is not None:
                    item.setText("–")
                    item.setForeground(QColor(theme.TEXT_TERTIARY))
                    item.setBackground(QColor(theme.SUBTLE))
                    item.setToolTip("Excluded · click to include")
                table.setItem(row, column, item)
        # Sized to its cells so it reads as a small notebook table, not a page-wide grid.
        vertical = table.verticalHeader()
        table.setFixedSize(
            vertical.sizeHint().width() + CELL_WIDTH * len(series.percents) + 2 * table.frameWidth(),
            table.horizontalHeader().sizeHint().height() + CELL_HEIGHT * len(series.times)
            + 2 * table.frameWidth(),
        )

    def _render_doses(self) -> None:
        series = self._series
        parts = []
        for percent in series.percents:
            if percent == 0:
                continue
            # Doses do not depend on soak time, so one probe per column is enough.
            probe = Condition(
                "probe", series.id,
                (*series.before, Treatment(series.additive_id, percent, 0)), 1,
            )
            try:
                dose = compute_doses(self.design, probe)[-1]
            except ValueError:
                continue
            parts.append(
                f"{_percent(percent)} % → {dose.volume_nl.normalize():f} nL ({dose.actual_percent} %)"
            )
        if self.design.drop_volume_nl is None:
            self.dose_label.setText("Enter the drop volume in Setup to see dispensed volumes.")
        elif parts:
            self.dose_label.setText("Dispensed per well: " + " · ".join(parts))
        else:
            self.dose_label.setText("")

    def _update_budget(self) -> None:
        needed = self.design.required_crystals
        available = self.design.available_crystals
        conditions = len(self.design.conditions())
        text = f"{_plural(conditions, 'condition')} · {_plural(needed, 'crystal')} needed"
        attention = False
        self.reduce_button.hide()
        if available:
            if needed > available:
                text += f" · {needed - available} short"
                attention = True
                if conditions < needed:
                    self.reduce_button.setText(f"Use 1 crystal each ({conditions})")
                    self.reduce_button.show()
            else:
                text += f" · {available - needed} spare"
        self.budget_label.setText(text)
        self.budget_label.setStyleSheet(
            theme.status_style("attention") if attention else f"color: {theme.TEXT};"
        )

    def _check_design(self) -> str | None:
        conditions = self.design.conditions()
        if not conditions:
            return "Include at least one condition."
        for condition in conditions:
            try:
                doses = compute_doses(self.design, condition)
            except ValueError as error:
                return str(error)
            for dose in doses:
                additive = self.design.additive(dose.additive_id)
                if not additive.source_plate or not additive.source_well:
                    return f"Enter the source plate and well for {additive.name}."
        return None

    def refresh_plan(self) -> None:
        self._render_additive_summary()
        self._render_matrix()
        self._render_doses()
        self._update_budget()
        self.design_error = self._check_design()
        current = self.design.additive(self._series.additive_id)
        # A missing source for the additive on screen is already marked in its summary line.
        repeated = self.design_error == f"Enter the source plate and well for {current.name}."
        self._show_design_error("" if repeated else self.design_error or "")
        self.selection_error = None
        try:
            if self.design_error:
                raise ValueError(self.design_error)
            selection = self._require_selection()
            plan = build_condition_test_plan(self.design, selection, self.order_input.currentData())
        except ValueError as error:
            if not self.design_error and self.selection is not None:
                self.selection_error = str(error)
            for table in (self.assignment_table, self.schedule_table, self.echo_table, self.shifter_table):
                table.setRowCount(0)
            self._show_plan_error(error)
            return
        self.current_plan = plan
        self.error_label.hide()
        self._fill_tables(plan)
        self._refresh_facts()
        self._refresh_experiment_id()
        self.draft_changed.emit()

    def _fill_tables(self, plan: ConditionTestPlan) -> None:
        design = plan.design
        self._set_preview_rows(self.assignment_table, tuple(
            (
                f"{item.selected_well.plate_code} {item.selected_well.well_address}",
                design.label(item.condition),
                str(item.replicate),
                " · ".join(
                    f"{design.additive(dose.additive_id).name} {dose.volume_nl.normalize():f} nL"
                    f" → {dose.actual_percent} %"
                    for dose in item.doses
                ) or "none (control)",
                f"T+{format_minutes(item.harvest_minutes)}",
            )
            for item in plan.assignments
        ))
        schedule = []
        rounds = plan.dispense_rounds
        for index, (minute, additions) in enumerate(rounds, start=1):
            name = f"ECHO R{index}" if len(rounds) > 1 else "ECHO"
            wells = sorted({f"{item.selected_well.well_address}" for item, _dose in additions})
            schedule.append((minute, f"{name}: dispense", ", ".join(wells)))
        for minute, group in plan.harvest_groups:
            schedule.append((
                minute, "Harvest", ", ".join(item.selected_well.well_address for item in group)
            ))
        # At the same minute, additions happen before that minute's harvest.
        schedule.sort(key=lambda row: (row[0], row[1] == "Harvest"))
        self._set_preview_rows(self.schedule_table, tuple(
            (f"T+{format_minutes(minute)}", action, wells) for minute, action, wells in schedule
        ))
        echo_rows = []
        for index, (_minute, rows) in enumerate(build_condition_echo_rounds(plan), start=1):
            echo_rows.extend((f"R{index}", *row.values()) for row in rows)
        self._set_preview_rows(self.echo_table, tuple(echo_rows))
        self._set_preview_rows(
            self.shifter_table, tuple(row.values() for row in build_shifter_worksheet(plan))
        )

    def _summary_rows(self) -> list[tuple[str, str]]:
        rows = super()._summary_rows()
        design = self.design
        drop = f"{design.drop_volume_nl.normalize():f} nL" if design.drop_volume_nl else "Not set"
        rows[2:2] = [
            ("Conditions", f"{_plural(len(design.conditions()), 'condition')} · "
                           f"{_plural(design.required_crystals, 'crystal')}"),
            ("Drop volume", drop),
        ]
        plan = self.current_plan
        if isinstance(plan, ConditionTestPlan):
            totals = ", ".join(
                f"{additive.name} {total.normalize():f} nL from {additive.source_plate} {additive.source_well}"
                for additive, total in plan.additive_totals()
            )
            if totals:
                rows.insert(4, ("Source use", totals))
            harvest = ", ".join(f"T+{format_minutes(minute)}" for minute, _ in plan.harvest_groups)
            rows.insert(4, ("Harvest", harvest))
        return rows

    def _record_builder(self) -> Callable:
        return build_condition_test_labworks

    def restore_draft(self, draft: PlanningDraft) -> None:
        if draft.details_json:
            self.design = ConditionTestDesign.from_json(draft.details_json)
        for widget in (self.protein_input, self.order_input):
            widget.blockSignals(True)
        self.protein_input.setText(draft.protein)
        self.name_input.setText(draft.name)
        self.assigned_experiment_id = draft.experiment_id
        index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, index))
        for widget in (self.protein_input, self.order_input):
            widget.blockSignals(False)
        self._series_index = 0
        self._load_design_inputs()
        self.refresh_plan()
