"""Condition test editor: concentration × soaking duration, one well per replicate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    parse_minutes_list,
    parse_percent,
    parse_percent_list,
)
from xtalflow.domain.crystal_workflow import AssignmentOrder
from xtalflow.domain.labwork import build_condition_test_labworks
from xtalflow.domain.plan_lifecycle import PlanningDraft
from xtalflow.domain.worksheets import ECHO_HEADER, build_condition_echo_rounds, build_shifter_worksheet
from xtalflow.ui import theme
from xtalflow.ui.help_button import HelpButton
from xtalflow.ui.plan_editors import PlanEditorBase, _plural, _preview_table

CELL_WIDTH = 78
CELL_HEIGHT = 32
MATRIX_MAX_HEIGHT = 360
PANEL_WIDTH = 300
ROW_AXIS_WIDTH = 200
# Beyond this many concentrations the per-column transfer line would wrap into a paragraph.
DOSE_LINE_COLUMNS = 6
DURATION_HINT = "Minutes or hours: 30, 45 min, 1 h, 1.5 h, 1 h 30 min."
MAX_WELLS_PER_CONDITION = 24

CONDITIONS_STYLE = f"""
QLabel#ConditionsTitle {{ font-weight: 600; }}
QScrollArea#ConditionsScroll, QWidget#ConditionsPage {{ background: {theme.BACKGROUND}; border: none; }}
QLabel#FieldLabel {{ color: {theme.TEXT_MUTED}; }}
QLabel#FieldError {{ color: {theme.ERROR}; }}
QLabel#FieldAttention {{ color: {theme.ATTENTION}; }}
QPushButton#SeriesChip {{ border: 1px solid {theme.BORDER}; border-radius: 14px;
    padding: 3px 14px; background: {theme.SURFACE}; }}
QPushButton#SeriesChip:checked {{ background: {theme.SELECTED}; border-color: {theme.BORDER_STRONG};
    font-weight: 600; }}
QPushButton#LinkButton {{ border: none; background: transparent; color: {theme.TEXT_MUTED};
    padding: 3px 6px; }}
QPushButton#LinkButton:hover {{ color: {theme.TEXT}; }}
QPushButton#LinkButton:disabled {{ color: {theme.TEXT_TERTIARY}; }}
QFrame#SourceBox, QFrame#ConditionPanel {{ border: 1px solid {theme.BORDER};
    border-radius: 10px; background: {theme.SIDEBAR}; }}
QFrame#SourceBox[missing="true"] {{ border-color: {theme.ATTENTION}; }}
QFrame#SourceBox QLabel, QFrame#ConditionPanel QLabel, QFrame#ConditionPanel QCheckBox {{
    background: transparent; }}
QLineEdit[missing="true"], QDoubleSpinBox[missing="true"] {{
    border-color: {theme.ATTENTION}; background: {theme.ATTENTION_SOFT}; }}
QLineEdit[invalid="true"] {{ border-color: {theme.ERROR}; background: {theme.ERROR_SOFT}; }}
QTableWidget#ConditionMatrix {{ border: 1px solid {theme.BORDER_STRONG}; border-radius: 0;
    gridline-color: {theme.BORDER_STRONG}; background: {theme.SURFACE}; }}
QTableWidget#ConditionMatrix:focus {{ border: 1px solid {theme.FOCUS}; }}
QTableWidget#ConditionMatrix::item:selected {{ background: {theme.FOCUS_SOFT};
    color: {theme.TEXT}; border: 2px solid {theme.FOCUS}; }}
QTableWidget#ConditionMatrix QHeaderView::section {{ background: {theme.SIDEBAR};
    color: {theme.TEXT}; border: none; border-right: 1px solid {theme.BORDER_STRONG};
    border-bottom: 1px solid {theme.BORDER_STRONG}; padding: 4px 8px; font-weight: 500; }}
"""


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _wells(count: int) -> str:
    return _plural(count, "well")


def _set_flag(widget: QWidget, name: str, value: bool) -> None:
    """Style a field as missing or invalid; the stylesheet reads the property."""
    if bool(widget.property(name)) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("FieldLabel")
    return label


def _message_label(name: str = "FieldError") -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setWordWrap(True)
    label.hide()
    return label


def _sentence(error: ValueError) -> str:
    message = str(error)
    return f"{message[0].upper()}{message[1:]}."


def _show_message(label: QLabel, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))


class ConditionTestEditor(PlanEditorBase):
    """Solvent and cryo tests: conditions first, then one well per replicate."""

    def __init__(self, design: ConditionTestDesign, parent: QWidget | None = None) -> None:
        super().__init__(None, "transient-condition-editor", parent)
        self.design = design
        self.design_error: str | None = None
        self.selection_error: str | None = None
        self._series_index = 0
        self._selected_cell_id: str | None = None
        self._rendering = False

        # Drop volume is one value shown in Setup and next to the conditions.
        self.drop_volume_input = self._drop_volume_spin()
        self.conditions_drop_input = self._drop_volume_spin()

        self.title_label = QLabel()
        self.title_label.setObjectName("ConditionsTitle")
        font = self.title_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        self.title_label.setFont(font)
        self.subtitle_label = QLabel(
            "Each condition uses separate wells. Numbers indicate wells per condition."
        )
        self.subtitle_label.setObjectName("Muted")

        # One chip per additive; most tests have just one.
        self.series_group = QButtonGroup(self)
        self.series_group.setExclusive(True)
        self.series_row = QHBoxLayout()
        self.series_row.setSpacing(theme.SPACING_S)
        self.add_series_button = QPushButton()
        self.add_series_button.setObjectName("LinkButton")
        self.remove_series_button = QPushButton()
        self.remove_series_button.setObjectName("LinkButton")

        self.additive_name_label = _field_label("")
        self.additive_name_input = QLineEdit()
        self.additive_name_input.setMaximumWidth(160)
        self.stock_input = QDoubleSpinBox()
        self.stock_input.setRange(0.1, 100)
        self.stock_input.setDecimals(1)
        self.stock_input.setSuffix(" %")
        self.stock_input.setMaximumWidth(110)
        self.replicates_input = QSpinBox()
        self.replicates_input.setRange(1, MAX_WELLS_PER_CONDITION)
        self.replicates_input.setMaximumWidth(80)
        self.replicates_input.setToolTip(
            "Wells for each new condition. Changing it also updates conditions that "
            "still use the previous number."
        )
        self.name_error_label = _message_label()

        self.source_box = QFrame()
        self.source_box.setObjectName("SourceBox")
        self.source_plate_input = QLineEdit()
        self.source_plate_input.setPlaceholderText("Plate, e.g. LDV-01")
        self.source_plate_input.setMaximumWidth(160)
        self.source_well_input = QLineEdit()
        self.source_well_input.setPlaceholderText("Well, e.g. A1")
        self.source_well_input.setMaximumWidth(100)
        self.source_message_label = _message_label("FieldAttention")

        self.advanced_button = QToolButton()
        self.advanced_button.setCheckable(True)
        self.advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_button.setArrowType(Qt.RightArrow)
        self.advanced_button.setText("Advanced")
        self.advanced_box = QWidget()
        self.advanced_box.hide()
        self.before_additive_input = QComboBox()
        self.before_percent_input = QDoubleSpinBox()
        self.before_percent_input.setRange(0, 99.9)
        self.before_percent_input.setDecimals(1)
        self.before_percent_input.setSuffix(" %")
        self.before_percent_input.setMaximumWidth(100)
        self.before_minutes_input = QLineEdit()
        self.before_minutes_input.setPlaceholderText("e.g. 1 h")
        self.before_minutes_input.setToolTip(DURATION_HINT)
        self.before_minutes_input.setMaximumWidth(110)
        self.before_error_label = _message_label()
        self.smiles_input = QLineEdit()
        self.smiles_input.setPlaceholderText("Optional, sent to MxLive")
        self.smiles_input.setMaximumWidth(320)

        self.percents_input = QLineEdit()
        self.percents_input.setPlaceholderText("e.g. 0, 5, 10, 20")
        self.percents_input.setToolTip("Separate values with commas.")
        self.percents_error_label = _message_label()
        self.times_input = QLineEdit()
        self.times_input.setPlaceholderText("e.g. 0, 30 min, 1 h")
        self.times_input.setToolTip(f"Separate values with commas. {DURATION_HINT}")
        self.times_error_label = _message_label()

        self.matrix_table = QTableWidget()
        self.matrix_table.setObjectName("ConditionMatrix")
        self.matrix_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.matrix_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.matrix_table.setFocusPolicy(Qt.StrongFocus)
        self.matrix_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.matrix_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.matrix_table.setMinimumWidth(220)
        self.matrix_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.matrix_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.matrix_table.setCornerButtonEnabled(False)
        self.matrix_table.setAccessibleName("Conditions")
        self.matrix_table.setAccessibleDescription(
            "Arrow keys select a condition. Space includes or excludes it; plus and "
            "minus change its wells."
        )
        self.matrix_table.installEventFilter(self)
        horizontal = self.matrix_table.horizontalHeader()
        horizontal.setContextMenuPolicy(Qt.CustomContextMenu)
        horizontal.setSectionResizeMode(QHeaderView.Fixed)
        horizontal.setDefaultSectionSize(CELL_WIDTH)
        horizontal.setDefaultAlignment(Qt.AlignCenter)
        horizontal.setHighlightSections(False)
        vertical = self.matrix_table.verticalHeader()
        vertical.setContextMenuPolicy(Qt.CustomContextMenu)
        vertical.setSectionResizeMode(QHeaderView.Fixed)
        vertical.setDefaultSectionSize(CELL_HEIGHT)
        vertical.setDefaultAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vertical.setHighlightSections(False)
        self.dose_label = QLabel()
        self.dose_label.setWordWrap(True)
        self.dose_label.setObjectName("Muted")

        # The selected condition, edited beside the table.
        self.condition_panel = QFrame()
        self.condition_panel.setObjectName("ConditionPanel")
        self.condition_panel.setFixedWidth(PANEL_WIDTH)
        self.condition_title_label = QLabel()
        self.condition_title_label.setWordWrap(True)
        self.condition_title_label.setStyleSheet("font-weight: 600;")
        self.condition_percent_input = QLineEdit()
        self.condition_percent_input.setMaximumWidth(90)
        self.condition_time_input = QLineEdit()
        self.condition_time_input.setMaximumWidth(110)
        self.condition_time_input.setToolTip(DURATION_HINT)
        self.condition_error_label = _message_label()
        self.condition_wells_input = QSpinBox()
        self.condition_wells_input.setRange(1, MAX_WELLS_PER_CONDITION)
        self.condition_wells_input.setMaximumWidth(80)
        self.condition_include_input = QCheckBox("Include")
        self.requested_label = QLabel()
        self.transfer_label = QLabel()
        self.transfer_label.setWordWrap(True)
        self.actual_label = QLabel()
        self.actual_label.setWordWrap(True)
        self.earlier_label = QLabel()
        self.earlier_label.setWordWrap(True)
        self.harvest_label = QLabel()
        self.harvest_label.setWordWrap(True)
        self.harvest_label.setToolTip(
            "Planned time relative to the start of dispensing (T+0). It is not a "
            "record of when wells were actually dispensed or harvested."
        )
        self.remove_percent_button = QPushButton()
        self.remove_percent_button.setToolTip("Remove this concentration column")
        self.remove_percent_button.setObjectName("LinkButton")
        self.remove_time_button = QPushButton()
        self.remove_time_button.setToolTip("Remove this soaking duration row")
        self.remove_time_button.setObjectName("LinkButton")

        self.available_input = QSpinBox()
        self.available_input.setRange(0, 9999)
        self.available_input.setSpecialValueText("—")
        self.available_input.setMaximumWidth(90)
        self.budget_label = QLabel()
        self.budget_label.setObjectName("Muted")
        self.design_error_label = QLabel()
        self.design_error_label.setWordWrap(True)
        self.design_error_label.setStyleSheet(theme.status_style("attention"))
        self.design_error_label.hide()

        self.assignment_table = _preview_table(
            ("Well", "Condition", "Replicate", "Additions", "Planned harvest"), stretch_last=True
        )
        self.schedule_table = _preview_table(("Planned time", "Action", "Wells"), stretch_last=True)
        self.echo_table = _preview_table(("Round", *ECHO_HEADER))
        self.preview_tabs.addTab(self.assignment_table, "Assignments")
        self.preview_tabs.addTab(self.schedule_table, "Planned schedule")
        self.preview_tabs.addTab(self.echo_table, "ECHO worksheets")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER worksheet")

        self._build_condition_panel()

        self.drop_volume_input.valueChanged.connect(self._drop_volume_changed)
        self.conditions_drop_input.valueChanged.connect(self._drop_volume_changed)
        self.series_group.idClicked.connect(self._series_changed)
        self.add_series_button.clicked.connect(self._add_series)
        self.remove_series_button.clicked.connect(lambda: self._remove_series(self._series_index))
        self.additive_name_input.editingFinished.connect(self._name_edited)
        self.stock_input.valueChanged.connect(
            lambda value: self.update_additive(stock_percent=Decimal(str(value)))
        )
        self.replicates_input.valueChanged.connect(self._default_replicates_changed)
        self.source_plate_input.editingFinished.connect(
            lambda: self.update_additive(source_plate=self.source_plate_input.text())
        )
        self.source_well_input.editingFinished.connect(
            lambda: self.update_additive(source_well=self.source_well_input.text())
        )
        self.smiles_input.editingFinished.connect(
            lambda: self.update_additive(smiles=self.smiles_input.text())
        )
        self.advanced_button.toggled.connect(self._toggle_advanced)
        self.before_additive_input.activated.connect(lambda _index: self.apply_earlier_treatment())
        self.before_percent_input.valueChanged.connect(lambda _value: self.apply_earlier_treatment())
        self.before_minutes_input.editingFinished.connect(self.apply_earlier_treatment)
        self.percents_input.editingFinished.connect(lambda: self.set_axis_text(columns=True))
        self.times_input.editingFinished.connect(lambda: self.set_axis_text(columns=False))
        self.matrix_table.currentCellChanged.connect(self._current_cell_changed)
        self.matrix_table.customContextMenuRequested.connect(self._show_cell_menu)
        horizontal.customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=True)
        )
        vertical.customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=False)
        )
        self.condition_percent_input.editingFinished.connect(self._condition_percent_edited)
        self.condition_time_input.editingFinished.connect(self._condition_time_edited)
        self.condition_wells_input.valueChanged.connect(self._condition_wells_changed)
        self.condition_include_input.toggled.connect(self._condition_include_changed)
        self.remove_percent_button.clicked.connect(lambda: self._remove_selected_axis(columns=True))
        self.remove_time_button.clicked.connect(lambda: self._remove_selected_axis(columns=False))
        self.available_input.valueChanged.connect(self._available_changed)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self._load_design_inputs()
        self.refresh_plan()

    @staticmethod
    def _drop_volume_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 100000)
        spin.setDecimals(1)
        spin.setSingleStep(10)
        spin.setSuffix(" nL")
        spin.setSpecialValueText("Not set")
        spin.setMaximumWidth(130)
        spin.setToolTip(
            "Volume of the crystallization drop before any additive. Final "
            "concentrations are calculated from it. Shared with Setup."
        )
        return spin

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
        return [("Initial drop volume", row)]

    def conditions_widget(self) -> QWidget:
        """What is tested on top, the matrix with the selected condition, then costs."""
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        heading = QHBoxLayout()
        heading.addLayout(titles)
        heading.addStretch()
        # Several solvents are rare; their chips appear only once there are two.
        chips = QHBoxLayout()
        chips.setSpacing(theme.SPACING_S)
        chips.addLayout(self.series_row)
        chips.addWidget(self.remove_series_button)
        chips.addStretch()

        inputs = QHBoxLayout()
        inputs.setSpacing(theme.SPACING_M)
        for label, field in (
            (self.additive_name_label, self.additive_name_input),
            (_field_label("Stock concentration"), self.stock_input),
            (_field_label("Initial drop volume"), self.conditions_drop_input),
        ):
            inputs.addWidget(label)
            inputs.addWidget(field)
            inputs.addSpacing(theme.SPACING_L)
        inputs.addStretch()

        source = QHBoxLayout()
        source.setContentsMargins(theme.SPACING_L, theme.SPACING_M, theme.SPACING_L, theme.SPACING_M)
        source.setSpacing(theme.SPACING_M)
        source_title = QLabel("Dispensing source")
        source_title.setStyleSheet("font-weight: 500;")
        source.addWidget(source_title)
        source.addSpacing(theme.SPACING_M)
        source.addWidget(_field_label("Plate"))
        source.addWidget(self.source_plate_input)
        source.addWidget(_field_label("Well"))
        source.addWidget(self.source_well_input)
        source.addSpacing(theme.SPACING_M)
        source.addWidget(self.source_message_label, 1)
        self.source_box.setLayout(source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_box)
        source_row.addStretch()

        before = QHBoxLayout()
        before.setSpacing(theme.SPACING_M)
        before.addWidget(_field_label("Earlier treatment"))
        before.addWidget(self.before_additive_input)
        before.addWidget(_field_label("at"))
        before.addWidget(self.before_percent_input)
        before.addWidget(_field_label("for"))
        before.addWidget(self.before_minutes_input)
        before.addWidget(self.before_error_label)
        before.addStretch()
        smiles = QHBoxLayout()
        smiles.setSpacing(theme.SPACING_M)
        smiles.addWidget(_field_label("SMILES"))
        smiles.addWidget(self.smiles_input)
        smiles.addStretch()
        advanced = QVBoxLayout()
        advanced.setContentsMargins(theme.SPACING_XL, 0, 0, 0)
        advanced.setSpacing(theme.SPACING_M)
        advanced.addLayout(before)
        advanced.addLayout(smiles)
        advanced.addWidget(self.add_series_button, 0, Qt.AlignLeft)
        self.advanced_box.setLayout(advanced)

        column_axis = QHBoxLayout()
        column_axis.setSpacing(theme.SPACING_M)
        column_axis.addWidget(QLabel("Final concentration in drop (%)"))
        column_axis.addWidget(self.percents_input, 1)
        column_axis_box = QVBoxLayout()
        column_axis_box.setSpacing(2)
        column_axis_box.addLayout(column_axis)
        column_axis_box.addWidget(self.percents_error_label)

        row_axis = QVBoxLayout()
        row_axis.setSpacing(theme.SPACING_S)
        row_axis.addWidget(QLabel("Soaking duration"))
        row_axis.addWidget(self.times_input)
        row_axis.addWidget(self.times_error_label)
        row_axis.addSpacing(theme.SPACING_L)
        row_axis.addWidget(QLabel("Wells per condition"))
        row_axis.addWidget(self.replicates_input)
        row_axis.addStretch()
        row_axis_widget = QWidget()
        row_axis_widget.setFixedWidth(ROW_AXIS_WIDTH)
        row_axis_widget.setLayout(row_axis)

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.SPACING_L)
        grid.setVerticalSpacing(theme.SPACING_S)
        grid.addLayout(column_axis_box, 0, 1)
        grid.addWidget(row_axis_widget, 1, 0, 3, 1, Qt.AlignTop)
        grid.addWidget(self.matrix_table, 1, 1)
        grid.addWidget(self.dose_label, 2, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(3, 1)

        matrix = QHBoxLayout()
        matrix.setSpacing(theme.SPACING_XL)
        matrix.addLayout(grid, 1)
        matrix.addWidget(self.condition_panel, 0, Qt.AlignTop)

        budget = QHBoxLayout()
        budget.setSpacing(theme.SPACING_M)
        budget.addWidget(_field_label("Crystals on hand (estimate)"))
        budget.addWidget(self.available_input)
        budget.addWidget(self.budget_label)
        budget.addStretch()

        widget = QWidget()
        widget.setObjectName("ConditionsPage")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, theme.SPACING_S, theme.SPACING_S, theme.SPACING_L)
        layout.setSpacing(theme.SPACING_M)
        layout.addLayout(heading)
        layout.addSpacing(theme.SPACING_S)
        layout.addLayout(chips)
        layout.addLayout(inputs)
        layout.addWidget(self.name_error_label)
        layout.addLayout(source_row)
        layout.addWidget(self.advanced_button, 0, Qt.AlignLeft)
        layout.addWidget(self.advanced_box)
        layout.addWidget(self.design_error_label)
        layout.addSpacing(theme.SPACING_S)
        layout.addLayout(matrix)
        layout.addSpacing(theme.SPACING_S)
        layout.addLayout(budget)
        layout.addStretch()
        widget.setLayout(layout)
        # Small windows scroll the page instead of squeezing the table and panel.
        scroll = QScrollArea()
        scroll.setObjectName("ConditionsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        scroll.setStyleSheet(CONDITIONS_STYLE)
        return scroll

    def _build_condition_panel(self) -> None:
        edit = QFormLayout()
        edit.setLabelAlignment(Qt.AlignLeft)
        edit.setHorizontalSpacing(theme.SPACING_L)
        edit.setVerticalSpacing(theme.SPACING_M)
        percent = QHBoxLayout()
        percent.setSpacing(theme.SPACING_S)
        percent.addWidget(self.condition_percent_input)
        percent.addWidget(_field_label("%"))
        percent.addStretch()
        edit.addRow(_field_label("Concentration"), percent)
        edit.addRow(_field_label("Duration"), self.condition_time_input)
        edit.addRow(_field_label("Wells"), self.condition_wells_input)
        edit.addRow("", self.condition_include_input)

        facts = QFormLayout()
        facts.setLabelAlignment(Qt.AlignLeft)
        facts.setHorizontalSpacing(theme.SPACING_L)
        facts.setVerticalSpacing(theme.SPACING_S)
        facts.addRow(_field_label("Requested"), self.requested_label)
        facts.addRow(_field_label("Transfer / well"), self.transfer_label)
        facts.addRow(_field_label("Actual"), self.actual_label)
        facts.addRow(_field_label("Earlier"), self.earlier_label)
        facts.addRow(_field_label("Planned harvest"), self.harvest_label)
        self._facts_layout = facts

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"color: {theme.BORDER};")
        layout = QVBoxLayout()
        layout.setContentsMargins(theme.SPACING_L, theme.SPACING_L, theme.SPACING_L, theme.SPACING_L)
        layout.setSpacing(theme.SPACING_M)
        layout.addWidget(self.condition_title_label)
        layout.addLayout(edit)
        layout.addWidget(self.condition_error_label)
        layout.addWidget(divider)
        layout.addLayout(facts)
        removes = QHBoxLayout()
        removes.setSpacing(0)
        removes.addWidget(self.remove_percent_button)
        removes.addWidget(self.remove_time_button)
        removes.addStretch()
        layout.addLayout(removes)
        self.condition_panel.setLayout(layout)

    def review_widget(self) -> QWidget:
        widget = super().review_widget()
        order = QHBoxLayout()
        order.addWidget(QLabel("Assign wells in"))
        order.addWidget(self.order_input)
        order.addWidget(HelpButton(
            "Changing this reassigns conditions to different wells. It is not a "
            "display sort."
        ))
        order.addStretch()
        widget.layout().insertLayout(2, order)
        return widget

    # -- Words for the kind of test -----------------------------------------------------

    @property
    def _noun(self) -> str:
        return "cryoprotectant" if self.design.kind == "cryo" else "solvent"

    # -- Design edits ---------------------------------------------------------------

    @property
    def _series(self):
        return self.design.series[self._series_index]

    def _replace_series(self, series) -> None:
        items = list(self.design.series)
        items[self._series_index] = series
        self.design = replace(self.design, series=tuple(items))

    def _load_design_inputs(self) -> None:
        """Show the design in every field, except a field still holding a rejected entry."""
        self._series_index = min(self._series_index, len(self.design.series) - 1)
        series = self._series
        additive = self.design.additive(series.additive_id)
        noun = self._noun
        self.title_label.setText(f"Compare {noun} concentration and soaking time")
        self.additive_name_label.setText(noun.capitalize())
        self.add_series_button.setText(f"+ Add {noun}")
        self.remove_series_button.setText(f"Remove {additive.name}")
        values = (
            (self.drop_volume_input, float(self.design.drop_volume_nl or 0)),
            (self.conditions_drop_input, float(self.design.drop_volume_nl or 0)),
            (self.available_input, self.design.available_crystals or 0),
            (self.stock_input, float(additive.stock_percent)),
            (self.replicates_input, self.design.default_replicates),
        )
        for widget, value in values:
            if widget.hasFocus() and widget.value() == value:
                continue  # Rewriting a spin box being typed in would move its cursor.
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        texts = (
            (self.additive_name_input, additive.name),
            (self.source_plate_input, additive.source_plate),
            (self.source_well_input, additive.source_well),
            (self.smiles_input, additive.smiles),
            (self.percents_input, ", ".join(_percent(value) for value in series.percents)),
            (self.times_input, ", ".join(format_minutes(value) for value in series.times)),
        )
        for widget, text in texts:
            if not widget.property("invalid") and widget.text() != text:
                widget.setText(text)
                widget.setCursorPosition(0)
        self._load_earlier_treatment()
        self._render_chips()

    def _load_earlier_treatment(self) -> None:
        series = self._series
        before = series.before[0] if series.before else None
        combo = self.before_additive_input
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("None", None)
        for item in self.design.additives:
            if item.id != series.additive_id:
                combo.addItem(item.name, item.id)
        combo.setCurrentIndex(max(0, combo.findData(before.additive_id)) if before else 0)
        combo.setToolTip(
            "" if combo.count() > 1
            else f"Add another {self._noun} first to treat with it beforehand."
        )
        combo.blockSignals(False)
        self.before_percent_input.blockSignals(True)
        self.before_percent_input.setValue(float(before.final_percent) if before else 0)
        self.before_percent_input.blockSignals(False)
        if not self.before_minutes_input.property("invalid"):
            self.before_minutes_input.setText(format_minutes(before.minutes) if before else "")
        enabled = before is not None
        self.before_percent_input.setEnabled(enabled)
        self.before_minutes_input.setEnabled(enabled)
        if before is not None and not self.advanced_button.isChecked():
            self.advanced_button.setText(
                f"Advanced · after {self.design.additive(before.additive_id).name} "
                f"{_percent(before.final_percent)} % for {format_minutes(before.minutes)}"
            )
        else:
            self.advanced_button.setText("Advanced")

    def _render_chips(self) -> None:
        for button in self.series_group.buttons():
            self.series_group.removeButton(button)
            self.series_row.removeWidget(button)
            # Hide now: a removed chip would otherwise paint until deleteLater runs.
            button.hide()
            button.deleteLater()
        several = len(self.design.series) > 1
        if several:
            for index, series in enumerate(self.design.series):
                chip = QPushButton(self.design.additive(series.additive_id).name)
                chip.setObjectName("SeriesChip")
                chip.setCheckable(True)
                chip.setChecked(index == self._series_index)
                self.series_group.addButton(chip, index)
                self.series_row.addWidget(chip)
        self.remove_series_button.setVisible(several)

    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced_box.setVisible(visible)
        self.advanced_button.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
        self._load_earlier_treatment()

    def _clear_field_errors(self) -> None:
        for widget in (
            self.percents_input, self.times_input, self.before_minutes_input,
            self.condition_percent_input, self.condition_time_input, self.additive_name_input,
        ):
            _set_flag(widget, "invalid", False)
        for label in (
            self.percents_error_label, self.times_error_label, self.before_error_label,
            self.condition_error_label, self.name_error_label,
        ):
            _show_message(label, "")

    def _series_changed(self, index: int) -> None:
        if 0 <= index < len(self.design.series) and index != self._series_index:
            self._series_index = index
            self._selected_cell_id = None
            self._clear_field_errors()
            self._load_design_inputs()
            self.refresh_plan()

    def _add_series(self) -> None:
        number = len(self.design.additives) + 1
        additive = Additive(str(uuid4()), f"{self._noun.capitalize()} {number}")
        series = new_series(
            additive.id, (Decimal("0"), Decimal("10")), (0,), self.design.default_replicates
        )
        self.design = replace(
            self.design,
            additives=(*self.design.additives, additive),
            series=(*self.design.series, series),
        )
        self._series_index = len(self.design.series) - 1
        self._selected_cell_id = None
        self._clear_field_errors()
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
        self._selected_cell_id = None
        self._clear_field_errors()
        self._load_design_inputs()
        self.refresh_plan()

    def _name_edited(self) -> None:
        name = self.additive_name_input.text().strip()
        invalid = not name
        _set_flag(self.additive_name_input, "invalid", invalid)
        _show_message(self.name_error_label, f"Enter the {self._noun} name." if invalid else "")
        if not invalid:
            self.update_additive(name=name)

    def update_additive(self, *, before=None, **changes) -> None:
        """Apply edits to the current additive; text fields are trimmed, wells upper-cased."""
        current = self.design.additive(self._series.additive_id)
        cleaned = {
            key: (value.strip().upper() if key == "source_well" else value.strip())
            if isinstance(value, str) else value
            for key, value in changes.items()
        }
        if "name" in cleaned and not cleaned["name"]:
            cleaned.pop("name")
        try:
            updated = replace(current, **cleaned)
        except (ValueError, InvalidOperation) as error:
            self._show_design_error(str(error))
            return
        if updated == current and before is None:
            return
        self.design = replace(
            self.design,
            additives=tuple(updated if item.id == current.id else item for item in self.design.additives),
        )
        if before is not None:
            self._replace_series(replace(self._series, before=tuple(before)))
        self._load_design_inputs()
        self.refresh_plan()

    def apply_earlier_treatment(self) -> bool:
        """Use the earlier treatment as entered; a duration that cannot be read changes nothing."""
        additive_id = self.before_additive_input.currentData()
        enabled = additive_id is not None
        self.before_percent_input.setEnabled(enabled)
        self.before_minutes_input.setEnabled(enabled)
        if not enabled:
            _set_flag(self.before_minutes_input, "invalid", False)
            _show_message(self.before_error_label, "")
            if self._series.before:
                self.update_additive(before=())
            return True
        text = self.before_minutes_input.text()
        if not text.strip():
            # Not filled in yet: nothing to apply and nothing wrong so far.
            _set_flag(self.before_minutes_input, "invalid", False)
            _show_message(self.before_error_label, "")
            self.before_minutes_input.setFocus(Qt.OtherFocusReason)
            return False
        try:
            minutes = parse_minutes(text)
        except ValueError as error:
            _set_flag(self.before_minutes_input, "invalid", True)
            _show_message(
                self.before_error_label, f"{_sentence(error)} {DURATION_HINT}"
            )
            return False
        _set_flag(self.before_minutes_input, "invalid", False)
        _show_message(self.before_error_label, "")
        percent = Decimal(str(self.before_percent_input.value()))
        treatment = (Treatment(additive_id, percent, minutes),)
        if self._series.before != treatment:
            self.update_additive(before=treatment)
        return True

    def _drop_volume_changed(self, value: float) -> None:
        self.design = replace(
            self.design, drop_volume_nl=Decimal(str(value)) if value > 0 else None
        )
        self._load_design_inputs()
        self.refresh_plan()

    def _available_changed(self, value: int) -> None:
        self.design = replace(self.design, available_crystals=value or None)
        self.refresh_plan()

    def _default_replicates_changed(self, value: int) -> None:
        """Conditions still at the previous default follow; individually set ones stay."""
        previous = self.design.default_replicates
        series = tuple(
            replace(item, cells=tuple(
                replace(cell, replicates=value) if cell.replicates == previous else cell
                for cell in item.cells
            ))
            for item in self.design.series
        )
        self.design = replace(self.design, series=series, default_replicates=value)
        self.refresh_plan()

    def set_axis_text(self, columns: bool) -> bool:
        """Read the typed concentrations or durations; a value that cannot be read changes nothing."""
        field = self.percents_input if columns else self.times_input
        label = self.percents_error_label if columns else self.times_error_label
        series = self._series
        try:
            if columns:
                updated = series.with_axes(
                    parse_percent_list(field.text()), series.times, self.design.default_replicates
                )
            else:
                updated = series.with_axes(
                    series.percents, parse_minutes_list(field.text()), self.design.default_replicates
                )
        except ValueError as error:
            _set_flag(field, "invalid", True)
            _show_message(
                label, _sentence(error) if columns else f"{_sentence(error)} {DURATION_HINT}"
            )
            return False
        _set_flag(field, "invalid", False)
        _show_message(label, "")
        if updated != series:
            self._replace_series(updated)
        self._load_design_inputs()
        self.refresh_plan()
        return True

    # Kept for callers that add a single value.
    def add_percent(self, text: str) -> bool:
        return self._add_axis_value(text, columns=True)

    def add_time(self, text: str) -> bool:
        return self._add_axis_value(text, columns=False)

    def _add_axis_value(self, text: str, columns: bool) -> bool:
        field = self.percents_input if columns else self.times_input
        field.setText(f"{field.text()}, {text}" if field.text().strip() else text)
        return self.set_axis_text(columns)

    def _selected_cell(self):
        series = self._series
        return next((cell for cell in series.cells if cell.id == self._selected_cell_id), None)

    def _current_cell_changed(self, row: int, column: int, *_previous) -> None:
        if self._rendering:
            return
        series = self._series
        if 0 <= row < len(series.times) and 0 <= column < len(series.percents):
            cell = series.cell(series.percents[column], series.times[row])
            if cell is not None and cell.id != self._selected_cell_id:
                self._selected_cell_id = cell.id
                _set_flag(self.condition_percent_input, "invalid", False)
                _set_flag(self.condition_time_input, "invalid", False)
                _show_message(self.condition_error_label, "")
                self._render_condition_panel()

    def select_condition(self, row: int, column: int) -> None:
        self.matrix_table.setCurrentCell(row, column)

    def _toggle_cell(self, row: int, column: int) -> None:
        series = self._series
        cell = series.cell(series.percents[column], series.times[row])
        if cell is not None:
            self._selected_cell_id = cell.id
            self._set_cells((cell,), included=not cell.included)

    def _set_cells(self, cells, **changes) -> None:
        series = self._series
        for cell in cells:
            series = series.with_cell(replace(cell, **changes))
        self._replace_series(series)
        self.refresh_plan()

    def _condition_percent_edited(self) -> None:
        cell = self._selected_cell()
        if cell is None:
            return
        try:
            value = parse_percent(self.condition_percent_input.text())
            updated = self._series.with_percent_changed(cell.percent, value)
        except ValueError as error:
            self._show_condition_error(self.condition_percent_input, error)
            return
        self._apply_condition_axis(updated)

    def _condition_time_edited(self) -> None:
        cell = self._selected_cell()
        if cell is None:
            return
        try:
            value = parse_minutes(self.condition_time_input.text())
            updated = self._series.with_time_changed(cell.minutes, value)
        except ValueError as error:
            self._show_condition_error(self.condition_time_input, error)
            return
        self._apply_condition_axis(updated)

    def _show_condition_error(self, field: QLineEdit, error: ValueError) -> None:
        _set_flag(field, "invalid", True)
        hint = "" if field is self.condition_percent_input else f" {DURATION_HINT}"
        _show_message(self.condition_error_label, f"{_sentence(error)}{hint}")

    def _apply_condition_axis(self, updated) -> None:
        for field in (self.condition_percent_input, self.condition_time_input):
            _set_flag(field, "invalid", False)
        _show_message(self.condition_error_label, "")
        if updated != self._series:
            self._replace_series(updated)
            self._load_design_inputs()
            self.refresh_plan()
        else:
            self._render_condition_panel()

    def _condition_wells_changed(self, value: int) -> None:
        cell = self._selected_cell()
        if cell is not None and cell.replicates != value:
            self._set_cells((cell,), replicates=value)

    def _condition_include_changed(self, included: bool) -> None:
        cell = self._selected_cell()
        if cell is not None and cell.included != included:
            self._set_cells((cell,), included=included)

    def _change_selected_wells(self, step: int) -> None:
        cell = self._selected_cell()
        if cell is None:
            return
        value = min(MAX_WELLS_PER_CONDITION, max(1, cell.replicates + step))
        if value != cell.replicates:
            self._set_cells((cell,), replicates=value)

    def _remove_selected_axis(self, columns: bool) -> None:
        cell = self._selected_cell()
        if cell is not None:
            self._remove_axis_value(cell.percent if columns else cell.minutes, columns)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.matrix_table and event.type() == QEvent.Resize:
            self._fit_matrix()
        if watched is self.matrix_table and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                cell = self._selected_cell()
                if cell is not None:
                    self._set_cells((cell,), included=not cell.included)
                return True
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self._change_selected_wells(1)
                return True
            if key in (Qt.Key_Minus, Qt.Key_Underscore):
                self._change_selected_wells(-1)
                return True
        return super().eventFilter(watched, event)

    def _show_cell_menu(self, position) -> None:
        index = self.matrix_table.indexAt(position)
        if not index.isValid():
            return
        series = self._series
        cell = series.cell(series.percents[index.column()], series.times[index.row()])
        self.select_condition(index.row(), index.column())
        menu = QMenu(self.matrix_table)
        toggle = menu.addAction("Exclude" if cell.included else "Include")
        toggle.triggered.connect(lambda: self._set_cells((cell,), included=not cell.included))
        replicates = menu.addMenu("Wells")
        for count in range(1, 7):
            action = replicates.addAction(str(count))
            action.setCheckable(True)
            action.setChecked(count == cell.replicates)
            action.triggered.connect(
                lambda _=False, value=count: self._set_cells((cell,), replicates=value)
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
        menu.addSeparator()
        remove = menu.addAction("Remove column" if columns else "Remove row")
        axis = series.percents if columns else series.times
        remove.setEnabled(len(axis) > 1)
        remove.triggered.connect(lambda: self._remove_axis_value(value, columns))
        menu.exec_(header.mapToGlobal(position))

    def _remove_axis_value(self, value, columns: bool) -> None:
        series = self._series
        axis = series.percents if columns else series.times
        if len(axis) <= 1:
            return
        if columns:
            updated = series.with_axes(tuple(item for item in series.percents if item != value), series.times)
        else:
            updated = series.with_axes(series.percents, tuple(item for item in series.times if item != value))
        self._replace_series(updated)
        self._load_design_inputs()
        self.refresh_plan()

    def _show_design_error(self, message: str) -> None:
        self.design_error_label.setText(message)
        self.design_error_label.setVisible(bool(message))

    # -- Rendering -------------------------------------------------------------------

    def _render_source(self) -> None:
        additive = self.design.additive(self._series.additive_id)
        plate_missing = not additive.source_plate
        well_missing = not additive.source_well
        _set_flag(self.source_plate_input, "missing", plate_missing)
        _set_flag(self.source_well_input, "missing", well_missing)
        _set_flag(self.source_box, "missing", plate_missing or well_missing)
        missing = " and ".join(
            name for name, absent in (("plate", plate_missing), ("well", well_missing)) if absent
        )
        _show_message(
            self.source_message_label,
            f"Enter the source {missing}; the ECHO worksheet needs it." if missing else "",
        )
        drop_missing = self.design.drop_volume_nl is None
        _set_flag(self.drop_volume_input, "missing", drop_missing)
        _set_flag(self.conditions_drop_input, "missing", drop_missing)

    def _condition_for(self, cell) -> Condition:
        series = self._series
        return Condition(
            cell.id, series.id,
            (*series.before, Treatment(series.additive_id, cell.percent, cell.minutes)),
            cell.replicates, cell.note,
        )

    def _render_matrix(self) -> None:
        series = self._series
        table = self.matrix_table
        self._rendering = True
        table.clear()
        table.setColumnCount(len(series.percents))
        table.setRowCount(len(series.times))
        table.setHorizontalHeaderLabels([f"{_percent(percent)} %" for percent in series.percents])
        table.setVerticalHeaderLabels([format_minutes(minutes) for minutes in series.times])
        if self._selected_cell() is None and series.cells:
            first = series.cell(series.percents[0], series.times[0])
            self._selected_cell_id = first.id if first is not None else None
        current = (0, 0)
        for row, minutes in enumerate(series.times):
            for column, percent in enumerate(series.percents):
                cell = series.cell(percent, minutes)
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                if cell is None:
                    table.setItem(row, column, item)
                    continue
                if cell.id == self._selected_cell_id:
                    current = (row, column)
                label = self.design.label(self._condition_for(cell))
                if cell.included:
                    item.setText(_wells(cell.replicates))
                    item.setToolTip(f"{label} · {_wells(cell.replicates)}")
                else:
                    item.setText("Excluded")
                    item.setForeground(QColor(theme.TEXT_TERTIARY))
                    item.setBackground(QColor(theme.SUBTLE))
                    item.setToolTip(f"{label} · excluded")
                table.setItem(row, column, item)
        table.setCurrentCell(*current)
        self._rendering = False
        self._fit_matrix()

    def _fit_matrix(self) -> None:
        """As large as its cells up to a limit; beyond that the table scrolls inside itself."""
        table = self.matrix_table
        frame = 2 * table.frameWidth()
        width = (
            table.verticalHeader().sizeHint().width()
            + table.horizontalHeader().length() + frame
        )
        height = table.horizontalHeader().sizeHint().height() + table.verticalHeader().length() + frame
        bar = table.style().pixelMetric(table.style().PM_ScrollBarExtent)
        clipped_rows = height > MATRIX_MAX_HEIGHT
        table.setMaximumWidth(width + (bar if clipped_rows else 0))
        clipped_columns = table.isVisible() and table.width() < table.maximumWidth()
        table.setFixedHeight(min(height, MATRIX_MAX_HEIGHT) + (bar if clipped_columns else 0))

    def _render_condition_panel(self) -> None:
        cell = self._selected_cell()
        self.condition_panel.setEnabled(cell is not None)
        if cell is None:
            self.condition_title_label.setText("Select a condition")
            return
        series = self._series
        condition = self._condition_for(cell)
        self.condition_title_label.setText(self.design.label(condition))
        if not self.condition_percent_input.property("invalid") and not self.condition_percent_input.hasFocus():
            self.condition_percent_input.setText(_percent(cell.percent))
        if not self.condition_time_input.property("invalid") and not self.condition_time_input.hasFocus():
            self.condition_time_input.setText(format_minutes(cell.minutes))
        for widget, setter, value in (
            (self.condition_wells_input, self.condition_wells_input.setValue, cell.replicates),
            (self.condition_include_input, self.condition_include_input.setChecked, cell.included),
        ):
            widget.blockSignals(True)
            setter(value)
            widget.blockSignals(False)
        self.condition_wells_input.setEnabled(cell.included)
        self.remove_percent_button.setText(f"Remove {_percent(cell.percent)} %")
        self.remove_time_button.setText(f"Remove {format_minutes(cell.minutes)}")
        self.remove_percent_button.setEnabled(len(series.percents) > 1)
        self.remove_time_button.setEnabled(len(series.times) > 1)

        additive = self.design.additive(series.additive_id)
        self.requested_label.setText(f"{_percent(cell.percent)} % {additive.name}")
        earlier = [treatment for treatment in series.before]
        start = sum(treatment.minutes for treatment in earlier)
        try:
            doses = compute_doses(self.design, condition)
        except ValueError as error:
            self.transfer_label.setText(str(error))
            self.transfer_label.setStyleSheet(f"color: {theme.ATTENTION};")
            self.actual_label.setText("—")
            self.earlier_label.setText("")
        else:
            self.transfer_label.setStyleSheet("")
            own = next(
                (dose for dose in doses if dose.additive_id == additive.id and dose.start_minutes == start),
                None,
            )
            if cell.percent == 0 or own is None:
                self.transfer_label.setText(f"Nothing (no {additive.name} added)")
                self.actual_label.setText(f"0 % {additive.name}")
            else:
                self.transfer_label.setText(f"{own.volume_nl.normalize():f} nL")
                self.actual_label.setText(f"{own.actual_percent} % {additive.name}")
            self.earlier_label.setText(" · ".join(
                f"{self.design.additive(dose.additive_id).name} {dose.volume_nl.normalize():f} nL "
                f"({dose.actual_percent} %) at T+{format_minutes(dose.start_minutes)}"
                for dose in doses if dose is not own
            ))
        self._facts_layout.labelForField(self.earlier_label).setVisible(bool(earlier))
        self.earlier_label.setVisible(bool(earlier))
        harvest = start + cell.minutes
        if harvest == 0:
            self.harvest_label.setText("T+0 min · as soon as possible after dispensing")
        elif earlier:
            self.harvest_label.setText(
                f"T+{format_minutes(harvest)} · {format_minutes(cell.minutes)} after the "
                f"{additive.name} dispense at T+{format_minutes(start)}"
            )
        else:
            self.harvest_label.setText(f"T+{format_minutes(harvest)} after dispensing")

    def _render_doses(self) -> None:
        series = self._series
        parts = []
        for percent in series.percents:
            if percent == 0:
                continue
            # Doses do not depend on soaking time, so one probe per column is enough.
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
            self.dose_label.setText("Enter the initial drop volume to see transfer volumes.")
        elif len(parts) > DOSE_LINE_COLUMNS:
            self.dose_label.setText("Select a condition to see its transfer volume and actual concentration.")
        elif parts:
            self.dose_label.setText("Transfer / well (actual): " + " · ".join(parts))
        else:
            self.dose_label.setText("")

    def _update_budget(self) -> None:
        needed = self.design.required_crystals
        available = self.design.available_crystals
        if not available:
            self.budget_label.setText("")
        elif needed > available:
            self.budget_label.setText(f"{needed - available} fewer than the {_wells(needed)} required")
        elif needed < available:
            self.budget_label.setText(f"{available - needed} more than the {_wells(needed)} required")
        else:
            self.budget_label.setText(f"Matches the {_wells(needed)} required")

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
        self._render_source()
        self._render_matrix()
        self._render_condition_panel()
        self._render_doses()
        self._update_budget()
        self.design_error = self._check_design()
        current = self.design.additive(self._series.additive_id)
        # Problems already marked at their field are not repeated above the table.
        shown_in_place = (
            f"Enter the source plate and well for {current.name}.",
            "Enter the initial drop volume.",
        )
        self._show_design_error(
            "" if self.design_error in shown_in_place else self.design_error or ""
        )
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
                ) or "none",
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
                           f"{_wells(design.required_crystals)} required"),
            ("Initial drop volume", drop),
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
            rows.insert(4, ("Planned harvest", harvest))
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
        self._selected_cell_id = None
        self._clear_field_errors()
        self._load_design_inputs()
        self.refresh_plan()
