"""Condition test editor: concentration × soaking duration, one well per replicate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QColor, QCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
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
    QMessageBox,
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

CELL_WIDTH = 98
CELL_HEIGHT = 32
MATRIX_MAX_HEIGHT = 360
PANEL_WIDTH = 300
# The "+" column and row that add values: narrow, so the table does not look unfinished.
ADD_COLUMN_WIDTH = 36
ADD_ROW_HEIGHT = 26
# While values are typed into it, the "+" column is as wide as the others.
ADD_EDITOR_WIDTH = CELL_WIDTH
DURATION_HINT = "Minutes or hours: 30, 45 min, 1 h, 1.5 h, 1 h 30 min."
MAX_WELLS_PER_CONDITION = 24

CONDITIONS_STYLE = f"""
QLabel#ConditionsTitle {{ font-weight: 600; }}
QScrollArea#ConditionsScroll, QWidget#ConditionsPage {{ background: {theme.BACKGROUND}; border: none; }}
QLabel#FieldLabel {{ color: {theme.TEXT_MUTED}; }}
QLabel#FieldError {{ color: {theme.ERROR}; }}
QLabel#FieldAttention {{ color: {theme.ATTENTION}; }}
QPushButton#SeriesChip {{ border: 1px solid {theme.BORDER}; border-radius: 14px;
    padding: 3px 30px 3px 14px; background: {theme.SURFACE}; text-align: left; }}
QToolButton#CellToggle {{ border: none; border-radius: 9px; padding: 0;
    color: {theme.TEXT_TERTIARY}; background: transparent; min-height: 16px; }}
QToolButton#CellToggle:hover {{ background: {theme.BORDER_STRONG}; color: {theme.TEXT}; }}
QToolButton#ChipClose {{ border: none; border-radius: 9px; padding: 0;
    color: {theme.TEXT_TERTIARY}; background: transparent; min-height: 18px; }}
QToolButton#ChipClose:hover {{ background: {theme.BORDER_STRONG}; color: {theme.TEXT}; }}
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
QLabel#AxisCaption {{ color: {theme.TEXT_MUTED}; font-weight: 500; background: transparent; }}
QLineEdit#AxisEditor {{ border: 1px solid {theme.FOCUS}; border-radius: 4px; padding: 0 6px;
    min-height: 0; }}
QLineEdit#AxisEditor[invalid="true"] {{ border-color: {theme.ERROR}; background: {theme.ERROR_SOFT}; }}
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
            "Crystal drops, each in its own well, for each new condition. Changing it "
            "also updates conditions that still use the previous number."
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

        # Concentrations and durations are edited on the table's own headers.
        self.axis_error_label = _message_label()
        # (columns, value being renamed or None when adding) while the header editor is open.
        self._axis_edit: tuple[bool, object] | None = None
        self._hover_axis: tuple[bool, object] | None = None

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
        self.row_caption = QLabel("Soaking duration", self.matrix_table)
        self.row_caption.setObjectName("AxisCaption")
        self.row_caption.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        vertical.setMinimumWidth(self.row_caption.sizeHint().width() + 20)
        self.axis_editor = QLineEdit(self.matrix_table)
        self.axis_editor.setObjectName("AxisEditor")
        self.axis_editor.hide()
        self.axis_editor.installEventFilter(self)
        self.axis_remove_button = QToolButton(self.matrix_table)
        self.axis_remove_button.setObjectName("CellToggle")
        self.axis_remove_button.setText("×")
        self.axis_remove_button.setFixedSize(16, 16)
        self.axis_remove_button.hide()
        self.axis_remove_button.installEventFilter(self)
        for header in (horizontal, vertical):
            # Header clicks edit values instead of selecting a whole column or row.
            header.setSectionsClickable(False)
            header.viewport().setMouseTracking(True)
            header.viewport().installEventFilter(self)

        # The selected condition, edited beside the table.
        self.condition_panel = QFrame()
        self.condition_panel.setObjectName("ConditionPanel")
        self.condition_panel.setFixedWidth(PANEL_WIDTH)
        self.condition_title_label = QLabel()
        self.condition_title_label.setWordWrap(True)
        self.condition_title_label.setStyleSheet("font-weight: 600;")
        self.condition_wells_input = QSpinBox()
        self.condition_wells_input.setRange(1, MAX_WELLS_PER_CONDITION)
        self.condition_wells_input.setMaximumWidth(80)
        self.include_button = QToolButton(self.matrix_table.viewport())
        self.include_button.setObjectName("CellToggle")
        self.include_button.setFixedSize(16, 16)
        self.include_button.hide()
        self.condition_state_label = QLabel()
        self.condition_state_label.setObjectName("Muted")
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
        self.axis_editor.editingFinished.connect(self.commit_axis_editor)
        self.axis_remove_button.clicked.connect(self._remove_hovered_axis)
        self.matrix_table.currentCellChanged.connect(self._current_cell_changed)
        self.matrix_table.customContextMenuRequested.connect(self._show_cell_menu)
        horizontal.customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=True)
        )
        vertical.customContextMenuRequested.connect(
            lambda position: self._show_axis_menu(position, columns=False)
        )
        self.condition_wells_input.valueChanged.connect(self._condition_wells_changed)
        self.include_button.clicked.connect(self.toggle_selected_condition)
        for scrollbar in (
            self.matrix_table.horizontalScrollBar(), self.matrix_table.verticalScrollBar()
        ):
            scrollbar.valueChanged.connect(lambda _value: self._place_include_button())
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
        chips.addWidget(self.add_series_button)
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
        self.advanced_box.setLayout(advanced)

        defaults = QHBoxLayout()
        defaults.setSpacing(theme.SPACING_M)
        defaults.setContentsMargins(0, 0, 0, theme.SPACING_M)
        defaults.addWidget(_field_label("Crystal drops per condition"))
        defaults.addWidget(self.replicates_input)
        defaults.addStretch()

        # The panel starts level with the table, beside the condition it describes.
        matrix = QGridLayout()
        matrix.setHorizontalSpacing(theme.SPACING_XL)
        matrix.setVerticalSpacing(theme.SPACING_S)
        matrix.addLayout(defaults, 0, 0)
        matrix.addWidget(self.matrix_table, 1, 0)
        matrix.addWidget(self.condition_panel, 1, 1, 3, 1, Qt.AlignTop)
        matrix.addWidget(self.axis_error_label, 2, 0)
        matrix.setColumnStretch(0, 1)
        matrix.setRowStretch(3, 1)

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
        # What this one well gets. Concentrations and durations belong to the whole
        # column or row, so they are named here but changed at the axis.
        facts = QFormLayout()
        facts.setLabelAlignment(Qt.AlignLeft)
        facts.setHorizontalSpacing(theme.SPACING_L)
        facts.setVerticalSpacing(theme.SPACING_S)
        facts.addRow(_field_label("Wells"), self.condition_wells_input)
        facts.addRow("", self.condition_state_label)
        # The table's column headers are these concentrations.
        concentration = QLabel("Final concentration in drop")
        concentration.setStyleSheet("font-weight: 500;")
        concentration.setContentsMargins(0, theme.SPACING_M, 0, 0)
        facts.addRow(concentration)
        facts.addRow(_field_label("Requested"), self.requested_label)
        facts.addRow(_field_label("Actual"), self.actual_label)
        facts.addRow(_field_label("Transfer / well"), self.transfer_label)
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
        layout.addWidget(divider)
        layout.addLayout(facts)
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
        values = (
            (self.drop_volume_input, float(self.design.drop_volume_nl or 0)),
            (self.conditions_drop_input, float(self.design.drop_volume_nl or 0)),
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
        """One chip per solvent, each with its own ×; the last solvent cannot be removed."""
        for button in self.series_group.buttons():
            self.series_group.removeButton(button)
            self.series_row.removeWidget(button)
            # Hide now: a removed chip would otherwise paint until deleteLater runs.
            button.hide()
            button.deleteLater()
        several = len(self.design.series) > 1
        for index, series in enumerate(self.design.series):
            name = self.design.additive(series.additive_id).name
            chip = QPushButton(name)
            chip.setObjectName("SeriesChip")
            chip.setCheckable(True)
            chip.setChecked(index == self._series_index)
            close = QToolButton(chip)
            close.setObjectName("ChipClose")
            close.setText("×")
            close.setToolTip(
                f"Remove {name} and its conditions" if several
                else "A test needs at least one solvent"
            )
            close.setAccessibleName(f"Remove {name}")
            close.setEnabled(several)
            close.setFixedSize(18, 18)
            close.clicked.connect(
                lambda _=False, removed=index: self.remove_series(removed)
            )
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, theme.SPACING_S, 0)
            # No stretch: it would let the chip grow across the row.
            row.addWidget(close, 0, Qt.AlignRight | Qt.AlignVCenter)
            chip.setLayout(row)
            chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.series_group.addButton(chip, index)
            self.series_row.addWidget(chip)

    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced_box.setVisible(visible)
        self.advanced_button.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
        self._load_earlier_treatment()

    def _clear_field_errors(self) -> None:
        for widget in (self.before_minutes_input, self.additive_name_input):
            _set_flag(widget, "invalid", False)
        for label in (self.before_error_label, self.name_error_label):
            _show_message(label, "")
        self._close_axis_editor()

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

    def remove_series(self, index: int) -> None:
        """Ask first: removing a solvent takes its whole table with it."""
        if not 0 <= index < len(self.design.series) or len(self.design.series) <= 1:
            return
        series = self.design.series[index]
        name = self.design.additive(series.additive_id).name
        conditions = series.conditions()
        wells = sum(condition.replicates for condition in conditions)
        if conditions and QMessageBox.question(
            self,
            f"Remove {name}",
            f"Remove {name} from this test?\n\n"
            f"Its {_plural(len(conditions), 'condition')} ({_wells(wells)}) are removed "
            "with it. Conditions for the other solvents stay as they are.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self._remove_series(index)

    def _remove_series(self, index: int) -> None:
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
        # Stay on the solvent that was open, unless it is the one removed.
        if index != self._series_index:
            self._series_index -= 1 if index < self._series_index else 0
        else:
            self._series_index = min(self._series_index, len(series) - 1)
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

    def _axis(self, columns: bool) -> tuple:
        return self._series.percents if columns else self._series.times

    def add_axis_values(self, text: str, columns: bool) -> bool:
        """Add one or several typed values; values that cannot be read change nothing."""
        try:
            values = parse_percent_list(text) if columns else parse_minutes_list(text)
        except ValueError as error:
            self._show_axis_error(error, columns)
            return False
        series = self._series
        axis = self._axis(columns)
        added = sorted(value for value in set(values) if value not in axis)
        if columns:
            updated = series.with_axes((*axis, *values), series.times, self.design.default_replicates)
        else:
            updated = series.with_axes(series.percents, (*axis, *values), self.design.default_replicates)
        self._show_axis_error(None, columns)
        self._replace_series(updated)
        if added:
            # Show where the new value went: values sort into place.
            current = self._selected_cell()
            percent = added[0] if columns else (current.percent if current else updated.percents[0])
            minutes = (current.minutes if current else updated.times[0]) if columns else added[0]
            self._selected_cell_id = updated.cell(percent, minutes).id
        self._load_design_inputs()
        self.refresh_plan()
        return True

    def rename_axis_value(self, old, text: str, columns: bool) -> bool:
        """Correct one column or row; its conditions keep their identity and settings."""
        series = self._series
        try:
            if columns:
                updated = series.with_percent_changed(old, parse_percent(text))
            else:
                updated = series.with_time_changed(old, parse_minutes(text))
        except ValueError as error:
            self._show_axis_error(error, columns)
            return False
        self._show_axis_error(None, columns)
        if updated != series:
            self._replace_series(updated)
            self._load_design_inputs()
            self.refresh_plan()
        return True

    def _show_axis_error(self, error: ValueError | None, columns: bool) -> None:
        if error is None:
            _show_message(self.axis_error_label, "")
            return
        hint = "" if columns else f" {DURATION_HINT}"
        _show_message(self.axis_error_label, f"{_sentence(error)}{hint}")

    def open_axis_editor(self, columns: bool, value=None) -> None:
        """Edit a value, or add values, in place on the header."""
        table = self.matrix_table
        header = table.horizontalHeader() if columns else table.verticalHeader()
        axis = self._axis(columns)
        section = axis.index(value) if value is not None else len(axis)
        if columns and value is None:
            # Room for typing comes from the "+" column itself, so every value stays in view.
            header.resizeSection(section, ADD_EDITOR_WIDTH)
            self._fit_matrix()
            # Lay the page out now, so the table is already wider when the editor is placed.
            page = table.parentWidget()
            if page is not None and page.layout() is not None:
                page.layout().activate()
        table.scrollTo(table.model().index(0 if columns else section, section if columns else 0))
        self._axis_edit = (columns, value)
        self.axis_remove_button.hide()
        editor = self.axis_editor
        if value is None:
            editor.setText("")
            # A column is too narrow for an example; its tooltip says what fits.
            editor.setPlaceholderText("" if columns else "e.g. 45 min, 2 h")
            editor.setToolTip(
                "Separate several values with commas." if columns
                else f"Separate several values with commas. {DURATION_HINT}"
            )
        else:
            editor.setText(_percent(value) if columns else format_minutes(value))
            editor.setPlaceholderText("")
            editor.setToolTip("" if columns else DURATION_HINT)
        _set_flag(editor, "invalid", False)
        self._show_axis_error(None, columns)
        # Exactly over its own header section: neighbouring values stay readable.
        start = header.sectionViewportPosition(section)
        size = header.sectionSize(section)
        if columns:
            width, height = size, header.height()
            local = QPoint(start, 0)
        else:
            width, height = header.width(), size
            local = QPoint(0, start)
        corner = header.viewport().mapTo(table, local)
        editor.setGeometry(corner.x(), corner.y(), width, height)
        editor.show()
        editor.raise_()
        editor.setFocus(Qt.OtherFocusReason)
        editor.selectAll()

    def commit_axis_editor(self) -> bool:
        if self._axis_edit is None:
            return True
        columns, old = self._axis_edit
        text = self.axis_editor.text()
        if not text.strip():
            self._close_axis_editor()
            return True
        done = (
            self.add_axis_values(text, columns) if old is None
            else self.rename_axis_value(old, text, columns)
        )
        if done:
            self._close_axis_editor()
        else:
            _set_flag(self.axis_editor, "invalid", True)
        return done

    def _close_axis_editor(self) -> None:
        editing = self._axis_edit
        self._axis_edit = None
        if editing is not None and editing == (True, None):
            # Back to its narrow width once nothing is being typed into it.
            columns = len(self._series.percents)
            if self.matrix_table.columnCount() > columns:
                self.matrix_table.horizontalHeader().resizeSection(columns, ADD_COLUMN_WIDTH)
                self._fit_matrix()
        _set_flag(self.axis_editor, "invalid", False)
        self.axis_editor.hide()
        if self.axis_error_label.isVisible():
            _show_message(self.axis_error_label, "")

    def _header_event(self, columns: bool, event) -> bool:
        """Clicks on the "+" add, a double-click edits, hovering offers ×."""
        table = self.matrix_table
        header = table.horizontalHeader() if columns else table.verticalHeader()
        kind = event.type()
        if kind == QEvent.MouseMove:
            self._hover_header(columns, header, event.pos())
            return False
        if kind == QEvent.Leave:
            cursor = table.mapFromGlobal(QCursor.pos())
            if not self.axis_remove_button.geometry().contains(cursor):
                self.axis_remove_button.hide()
            return False
        if kind not in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            return False
        if event.button() != Qt.LeftButton:
            return False
        axis = self._axis(columns)
        section = header.logicalIndexAt(event.pos())
        if section == len(axis):
            if kind == QEvent.MouseButtonPress:
                self.open_axis_editor(columns)
            return True
        if 0 <= section < len(axis):
            if kind == QEvent.MouseButtonDblClick:
                self.open_axis_editor(columns, axis[section])
            else:
                current = table.currentIndex()
                self.select_condition(
                    max(0, current.row()) if columns else section,
                    section if columns else max(0, current.column()),
                )
            return True
        return False

    def _hover_header(self, columns: bool, header, position) -> None:
        axis = self._axis(columns)
        section = header.logicalIndexAt(position)
        button = self.axis_remove_button
        if not 0 <= section < len(axis) or len(axis) < 2 or self.axis_editor.isVisible():
            self._hover_axis = None
            button.hide()
            return
        value = axis[section]
        self._hover_axis = (columns, value)
        name = f"{_percent(value)} % column" if columns else f"{format_minutes(value)} row"
        button.setToolTip(f"Remove the {name}")
        button.setAccessibleName(f"Remove the {name}")
        start = header.sectionViewportPosition(section)
        size = header.sectionSize(section)
        edge = button.height()
        if columns:
            local = QPoint(start + size - edge - 4, (header.height() - edge) // 2)
        else:
            # Row labels are right-aligned, so × sits on the left.
            local = QPoint(4, start + (size - edge) // 2)
        button.move(header.viewport().mapTo(self.matrix_table, local))
        button.show()
        button.raise_()

    def _remove_hovered_axis(self) -> None:
        if self._hover_axis is not None:
            columns, value = self._hover_axis
            self._hover_axis = None
            self.axis_remove_button.hide()
            self._remove_axis_value(value, columns)

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
                self._render_condition_panel()
                self._place_include_button()

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

    def _condition_wells_changed(self, value: int) -> None:
        cell = self._selected_cell()
        if cell is not None and cell.replicates != value:
            self._set_cells((cell,), replicates=value)

    def toggle_selected_condition(self) -> None:
        cell = self._selected_cell()
        if cell is not None:
            self._set_cells((cell,), included=not cell.included)

    def _change_selected_wells(self, step: int) -> None:
        cell = self._selected_cell()
        if cell is None:
            return
        value = min(MAX_WELLS_PER_CONDITION, max(1, cell.replicates + step))
        if value != cell.replicates:
            self._set_cells((cell,), replicates=value)

    def eventFilter(self, watched, event) -> bool:
        if "axis_remove_button" not in self.__dict__ or sip.isdeleted(self.matrix_table):
            # The table sends events while its overlays are built and while it is torn down.
            return super().eventFilter(watched, event)
        table = self.matrix_table
        if watched is table.horizontalHeader().viewport():
            return self._header_event(True, event) or super().eventFilter(watched, event)
        if watched is table.verticalHeader().viewport():
            return self._header_event(False, event) or super().eventFilter(watched, event)
        if watched is self.axis_editor and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._close_axis_editor()
                table.setFocus(Qt.OtherFocusReason)
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                # Handled here: passed on, Enter would also toggle the selected condition.
                if self.commit_axis_editor():
                    table.setFocus(Qt.OtherFocusReason)
                return True
        if watched is self.axis_remove_button and event.type() == QEvent.Leave:
            self.axis_remove_button.hide()
        if watched is self.matrix_table and event.type() == QEvent.Resize:
            self._fit_matrix()
            self._place_include_button()
        if watched is self.matrix_table and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                self.toggle_selected_condition()
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
        series = self._series
        if (
            not index.isValid() or index.row() >= len(series.times)
            or index.column() >= len(series.percents)
        ):
            return
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
        if not 0 <= section < len(series.percents if columns else series.times):
            return
        if columns:
            value = series.percents[section]
            cells = tuple(item for item in series.cells if item.percent == value)
        else:
            value = series.times[section]
            cells = tuple(item for item in series.cells if item.minutes == value)
        menu = QMenu(header)
        menu.addAction("Edit value…").triggered.connect(
            lambda: self.open_axis_editor(columns, value)
        )
        menu.addSeparator()
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
        columns, rows = len(series.percents), len(series.times)
        # One more column and row, headed "+", add values where they will appear.
        table.setColumnCount(columns + 1)
        table.setRowCount(rows + 1)
        table.setHorizontalHeaderLabels([*(f"{_percent(percent)} %" for percent in series.percents), "+"])
        table.setVerticalHeaderLabels([*(format_minutes(minutes) for minutes in series.times), "+"])
        for header, count, size, add_size in (
            (table.horizontalHeader(), columns, CELL_WIDTH, ADD_COLUMN_WIDTH),
            (table.verticalHeader(), rows, CELL_HEIGHT, ADD_ROW_HEIGHT),
        ):
            for section in range(count):
                header.resizeSection(section, size)
            header.resizeSection(count, add_size)
        for item, tip in (
            (table.horizontalHeaderItem(columns), "Add concentrations"),
            (table.verticalHeaderItem(rows), "Add soaking durations"),
        ):
            item.setForeground(QColor(theme.TEXT_MUTED))
            item.setTextAlignment(Qt.AlignCenter)
            item.setToolTip(tip)
        for row in range(rows + 1):
            for column in range(columns + 1):
                if row == rows or column == columns:
                    empty = QTableWidgetItem()
                    empty.setFlags(Qt.NoItemFlags)
                    table.setItem(row, column, empty)
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
        self._place_include_button()

    def _place_include_button(self) -> None:
        """One small toggle, in the selected cell only, so the table stays plain."""
        cell = self._selected_cell()
        table = self.matrix_table
        item = table.currentItem()
        if cell is None or item is None:
            self.include_button.hide()
            return
        self.include_button.setText("×" if cell.included else "+")
        self.include_button.setToolTip(
            ("Exclude this condition (Space)" if cell.included
             else "Include this condition (Space)")
        )
        self.include_button.setAccessibleName(
            "Exclude condition" if cell.included else "Include condition"
        )
        rect = table.visualItemRect(item)
        size = self.include_button.height()
        self.include_button.move(
            rect.right() - size - 3, rect.top() + (rect.height() - size) // 2
        )
        self.include_button.setVisible(rect.width() > size * 2)
        self.include_button.raise_()

    def _fit_matrix(self) -> None:
        """As large as its cells up to a limit; beyond that the table scrolls inside itself."""
        table = self.matrix_table
        frame = 2 * table.frameWidth()
        # The row header is widened to fit its axis name, beyond its own size hint.
        row_header = max(table.verticalHeader().sizeHint().width(), table.verticalHeader().minimumWidth())
        width = row_header + table.horizontalHeader().length() + frame
        height = table.horizontalHeader().sizeHint().height() + table.verticalHeader().length() + frame
        bar = table.style().pixelMetric(table.style().PM_ScrollBarExtent)
        clipped_rows = height > MATRIX_MAX_HEIGHT
        table.setMaximumWidth(width + (bar if clipped_rows else 0))
        clipped_columns = table.isVisible() and table.width() < table.maximumWidth()
        table.setFixedHeight(min(height, MATRIX_MAX_HEIGHT) + (bar if clipped_columns else 0))
        # The row axis is named in the empty corner; the panel names the concentration.
        column_header = table.horizontalHeader().sizeHint().height()
        self.row_caption.setGeometry(
            table.frameWidth() + 8, table.frameWidth(), row_header - 10, column_header
        )

    def _render_condition_panel(self) -> None:
        cell = self._selected_cell()
        self.condition_panel.setEnabled(cell is not None)
        if cell is None:
            self.condition_title_label.setText("Select a condition")
            return
        series = self._series
        condition = self._condition_for(cell)
        self.condition_title_label.setText(self.design.label(condition))
        self.condition_wells_input.blockSignals(True)
        self.condition_wells_input.setValue(cell.replicates)
        self.condition_wells_input.blockSignals(False)
        self.condition_wells_input.setEnabled(cell.included)
        self.condition_state_label.setText(
            "" if cell.included else "Excluded from this test"
        )

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
