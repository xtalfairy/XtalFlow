"""Condition test editor: additives, a notebook-style concentration × time table, and results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
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
from uuid import uuid4


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}"


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

        self.series_tabs = QTabBar()
        self.series_tabs.setTabsClosable(True)
        self.series_tabs.setExpanding(False)
        self.add_series_button = QPushButton("+ Additive")
        self.additive_name_input = QLineEdit()
        self.additive_name_input.setMaximumWidth(160)
        self.stock_input = QDoubleSpinBox()
        self.stock_input.setRange(0.1, 100)
        self.stock_input.setDecimals(1)
        self.stock_input.setSuffix(" %")
        self.stock_input.setMaximumWidth(110)
        self.source_plate_input = QLineEdit()
        self.source_plate_input.setPlaceholderText("Plate")
        self.source_plate_input.setMaximumWidth(140)
        self.source_well_input = QLineEdit()
        self.source_well_input.setPlaceholderText("Well")
        self.source_well_input.setMaximumWidth(70)
        self.smiles_input = QLineEdit()
        self.smiles_input.setPlaceholderText("SMILES for MxLive (optional)")
        self.smiles_input.setMaximumWidth(220)
        self.before_additive_input = QComboBox()
        self.before_percent_input = QDoubleSpinBox()
        self.before_percent_input.setRange(0, 99.9)
        self.before_percent_input.setDecimals(1)
        self.before_percent_input.setSuffix(" %")
        self.before_percent_input.setMaximumWidth(100)
        self.before_minutes_input = QLineEdit()
        self.before_minutes_input.setPlaceholderText("e.g. 30 min")
        self.before_minutes_input.setMaximumWidth(110)

        self.matrix_table = QTableWidget()
        self.matrix_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.matrix_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.matrix_table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.matrix_table.verticalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.matrix_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.matrix_table.verticalHeader().setDefaultSectionSize(40)
        self.new_percent_input = QLineEdit()
        self.new_percent_input.setPlaceholderText("e.g. 15")
        self.new_percent_input.setMaximumWidth(90)
        self.add_percent_button = QPushButton("Add %")
        self.new_time_input = QLineEdit()
        self.new_time_input.setPlaceholderText("e.g. 90 min")
        self.new_time_input.setMaximumWidth(110)
        self.add_time_button = QPushButton("Add time")
        self.available_input = QSpinBox()
        self.available_input.setRange(0, 9999)
        self.available_input.setSpecialValueText("Not set")
        self.available_input.setMaximumWidth(110)
        self.budget_label = QLabel()
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
        self.series_tabs.currentChanged.connect(self._series_changed)
        self.series_tabs.tabCloseRequested.connect(self._remove_series)
        self.add_series_button.clicked.connect(self._add_series)
        for widget in (self.additive_name_input, self.source_plate_input,
                       self.source_well_input, self.smiles_input):
            widget.editingFinished.connect(self._additive_edited)
        self.stock_input.valueChanged.connect(self._additive_edited)
        self.before_additive_input.currentIndexChanged.connect(self._before_edited)
        self.before_percent_input.valueChanged.connect(self._before_edited)
        self.before_minutes_input.editingFinished.connect(self._before_edited)
        self.matrix_table.cellClicked.connect(self._toggle_cell)
        self.matrix_table.customContextMenuRequested.connect(self._show_cell_menu)
        self.matrix_table.horizontalHeader().customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=True)
        )
        self.matrix_table.verticalHeader().customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=False)
        )
        self.add_percent_button.clicked.connect(self._add_percent)
        self.new_percent_input.returnPressed.connect(self._add_percent)
        self.add_time_button.clicked.connect(self._add_time)
        self.new_time_input.returnPressed.connect(self._add_time)
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
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.series_tabs)
        tabs_row.addWidget(self.add_series_button)
        tabs_row.addStretch()
        tabs_row.addWidget(QLabel("Crystals available"))
        tabs_row.addWidget(self.available_input)
        tabs_row.addWidget(self.budget_label)
        tabs_row.addWidget(self.reduce_button)

        additive = QGridLayout()
        additive.setHorizontalSpacing(theme.SPACING_M)
        additive.setVerticalSpacing(theme.SPACING_S)
        additive.addWidget(QLabel("Additive"), 0, 0)
        additive.addWidget(self.additive_name_input, 0, 1)
        additive.addWidget(QLabel("Stock"), 0, 2)
        additive.addWidget(self.stock_input, 0, 3)
        additive.addWidget(QLabel("Source"), 0, 4)
        additive.addWidget(self.source_plate_input, 0, 5)
        additive.addWidget(self.source_well_input, 0, 6)
        additive.addWidget(self.smiles_input, 0, 7)
        additive.addWidget(QLabel("After"), 1, 0)
        additive.addWidget(self.before_additive_input, 1, 1)
        additive.addWidget(QLabel("at"), 1, 2)
        additive.addWidget(self.before_percent_input, 1, 3)
        additive.addWidget(QLabel("for"), 1, 4)
        additive.addWidget(self.before_minutes_input, 1, 5)
        additive.addWidget(HelpButton(
            "Combined treatment: this additive is added after an earlier one has "
            "soaked for the given time. Leave as No earlier treatment for one additive."
        ), 1, 6)
        additive.setColumnStretch(8, 1)

        axes = QHBoxLayout()
        axes.addWidget(QLabel("Final concentration"))
        axes.addWidget(self.new_percent_input)
        axes.addWidget(self.add_percent_button)
        axes.addSpacing(theme.SPACING_XL)
        axes.addWidget(QLabel("Soak time"))
        axes.addWidget(self.new_time_input)
        axes.addWidget(self.add_time_button)
        axes.addWidget(HelpButton(
            "Columns are final concentrations and rows are soak times. Click a cell "
            "to include or exclude it; excluded cells stay visible, crossed out. "
            "Right-click a cell for replicates, or a header to change the whole row "
            "or column. 0 % is the control."
        ))
        axes.addStretch()

        widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_S, 0, 0)
        layout.setSpacing(theme.SPACING_M)
        layout.addLayout(tabs_row)
        layout.addLayout(additive)
        layout.addWidget(self.design_error_label)
        layout.addWidget(self.matrix_table)
        layout.addLayout(axes)
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
        self.series_tabs.blockSignals(True)
        while self.series_tabs.count():
            self.series_tabs.removeTab(0)
        for series in self.design.series:
            self.series_tabs.addTab(self.design.additive(series.additive_id).name)
        self._series_index = min(self._series_index, len(self.design.series) - 1)
        self.series_tabs.setCurrentIndex(self._series_index)
        self.series_tabs.setTabsClosable(len(self.design.series) > 1)
        self.series_tabs.blockSignals(False)
        self._load_series_inputs()

    def _load_series_inputs(self) -> None:
        series = self._series
        additive = self.design.additive(series.additive_id)
        widgets = (
            self.additive_name_input, self.stock_input, self.source_plate_input,
            self.source_well_input, self.smiles_input, self.before_additive_input,
            self.before_percent_input, self.before_minutes_input,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.additive_name_input.setText(additive.name)
        self.stock_input.setValue(float(additive.stock_percent))
        self.source_plate_input.setText(additive.source_plate)
        self.source_well_input.setText(additive.source_well)
        self.smiles_input.setText(additive.smiles)
        self.before_additive_input.clear()
        self.before_additive_input.addItem("No earlier treatment", None)
        for item in self.design.additives:
            if item.id != additive.id:
                self.before_additive_input.addItem(item.name, item.id)
        before = series.before[0] if series.before else None
        index = self.before_additive_input.findData(before.additive_id) if before else 0
        self.before_additive_input.setCurrentIndex(max(0, index))
        self.before_percent_input.setValue(float(before.final_percent) if before else 0)
        self.before_minutes_input.setText(format_minutes(before.minutes) if before else "")
        has_before = before is not None
        self.before_percent_input.setEnabled(has_before)
        self.before_minutes_input.setEnabled(has_before)
        for widget in widgets:
            widget.blockSignals(False)

    def _series_changed(self, index: int) -> None:
        if 0 <= index < len(self.design.series):
            self._series_index = index
            self._load_series_inputs()
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
        self.additive_name_input.setFocus(Qt.OtherFocusReason)
        self.additive_name_input.selectAll()

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

    def _additive_edited(self, *_args) -> None:
        current = self.design.additive(self._series.additive_id)
        try:
            updated = Additive(
                current.id,
                self.additive_name_input.text().strip() or current.name,
                Decimal(str(self.stock_input.value())),
                self.smiles_input.text().strip(),
                self.source_plate_input.text().strip(),
                self.source_well_input.text().strip().upper(),
            )
        except (ValueError, InvalidOperation):
            return
        if updated == current:
            return
        self.design = replace(
            self.design,
            additives=tuple(updated if item.id == current.id else item for item in self.design.additives),
        )
        self.series_tabs.setTabText(self._series_index, updated.name)
        self.refresh_plan()

    def _before_edited(self, *_args) -> None:
        additive_id = self.before_additive_input.currentData()
        self.before_percent_input.setEnabled(additive_id is not None)
        self.before_minutes_input.setEnabled(additive_id is not None)
        before: tuple[Treatment, ...] = ()
        if additive_id is not None:
            try:
                minutes = parse_minutes(self.before_minutes_input.text() or "0")
            except ValueError:
                minutes = 0
            before = (
                Treatment(additive_id, Decimal(str(self.before_percent_input.value())), minutes),
            )
        if before != self._series.before:
            self._replace_series(replace(self._series, before=before))
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
        replicates = menu.addMenu("Replicates")
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
        replicates = menu.addMenu("Replicates")
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

    def _add_percent(self) -> None:
        text = self.new_percent_input.text().strip().rstrip("%").strip()
        try:
            value = Decimal(text)
            if value < 0 or value >= 100:
                raise ValueError
        except (InvalidOperation, ValueError):
            self._show_design_error("Enter a final concentration from 0 to below 100 %.")
            return
        series = self._series
        self._replace_series(series.with_axes((*series.percents, value), series.times))
        self.new_percent_input.clear()
        self.refresh_plan()

    def _add_time(self) -> None:
        try:
            minutes = parse_minutes(self.new_time_input.text())
        except ValueError:
            self._show_design_error("Enter a soak time such as 30, 90 min, or 2 h.")
            return
        series = self._series
        self._replace_series(series.with_axes(series.percents, (*series.times, minutes)))
        self.new_time_input.clear()
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

    def _render_matrix(self) -> None:
        series = self._series
        table = self.matrix_table
        table.clear()
        table.setColumnCount(len(series.percents))
        table.setRowCount(len(series.times))
        headers = []
        for percent in series.percents:
            if percent == 0:
                headers.append("0 %\ncontrol")
                continue
            # Doses do not depend on soak time, so one probe per column is enough.
            probe = Condition(
                "probe", series.id,
                (*series.before, Treatment(series.additive_id, percent, 0)), 1,
            )
            try:
                dose = compute_doses(self.design, probe)[-1]
                detail = f"{dose.volume_nl.normalize():f} nL → {dose.actual_percent} %"
            except ValueError:
                detail = "set drop volume" if self.design.drop_volume_nl is None else "△ see note"
            headers.append(f"{_percent(percent)} %\n{detail}")
        table.setHorizontalHeaderLabels(headers)
        table.setVerticalHeaderLabels([format_minutes(minutes) for minutes in series.times])
        # The table is as tall as its rows, so the axis inputs stay right under it.
        table.setFixedHeight(
            table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth()
            + table.verticalHeader().defaultSectionSize() * len(series.times)
        )
        conditions = {condition.id: condition for condition in series.conditions()}
        for row, minutes in enumerate(series.times):
            for column, percent in enumerate(series.percents):
                cell = series.cell(percent, minutes)
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                if cell is None:
                    table.setItem(row, column, item)
                    continue
                if cell.included:
                    item.setText(f"× {cell.replicates}")
                    item.setToolTip(
                        f"{self.design.label(conditions[cell.id])}\n"
                        f"{_plural(cell.replicates, 'crystal')} · click to exclude"
                    )
                else:
                    item.setText("excluded")
                    font = QFont(item.font())
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setForeground(QColor(theme.TEXT_TERTIARY))
                    item.setBackground(QColor(theme.SUBTLE))
                    item.setToolTip("Excluded · click to include")
                table.setItem(row, column, item)

    def _update_budget(self) -> None:
        needed = self.design.required_crystals
        available = self.design.available_crystals
        conditions = len(self.design.conditions())
        text = f"{_plural(conditions, 'condition')} · {_plural(needed, 'crystal')} needed"
        kind = "muted"
        self.reduce_button.hide()
        if available:
            if needed > available:
                text += f" · {needed - available} short"
                kind = "attention"
                single = conditions
                if single < needed:
                    self.reduce_button.setText(f"Use 1 replicate each ({single})")
                    self.reduce_button.show()
            else:
                text += f" · {available - needed} spare"
        self.budget_label.setText(text)
        self.budget_label.setStyleSheet(
            theme.status_style("attention") if kind == "attention" else f"color: {theme.TEXT_MUTED};"
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
        self._render_matrix()
        self._update_budget()
        self.design_error = self._check_design()
        self._show_design_error(self.design_error or "")
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
