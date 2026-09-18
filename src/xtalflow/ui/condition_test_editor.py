"""Condition test editor: concentration × soaking duration, one well per replicate."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
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
    additive_transfers,
    source_statuses,
    Condition,
    ConditionTestDesign,
    ConditionTestPlan,
    Treatment,
    build_condition_test_plan,
    compute_doses,
    fit_to_wells,
    format_minutes,
    new_series,
    parse_minutes,
    parse_minutes_list,
    parse_percent,
    parse_percent_list,
)
from xtalflow.domain.crystal_workflow import AssignmentOrder
from xtalflow.domain.echo_source import (
    SOURCE_PLATE_TYPES,
    PlateRecordEntry,
    PlateSummary,
    allocate_source_wells,
    next_free_wells,
    ul_text,
    parse_wells,
    source_plate_type,
)
from xtalflow.infrastructure.source_plate_record import SourcePlateRecordUnavailable
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
RECHECK_NOTE = "Wells are checked again when worksheets are saved."
NEW_PLATE = "\u0000new plate"
NAMING_NOTE = "Type the new plate's name, then press Enter; its wells start at A1."

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
QLabel#SourceMessage {{ color: {theme.TEXT}; }}
QLabel#SourceMessage[attention="true"] {{ color: {theme.ATTENTION}; }}
QFrame#SourceBox QLabel, QFrame#ConditionPanel QLabel, QFrame#ConditionPanel QCheckBox {{
    background: transparent; }}
QLineEdit[missing="true"], QDoubleSpinBox[missing="true"], QComboBox[missing="true"] {{
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


class _PlateChoice(QComboBox):
    """A combo box that says when it is about to open, so its list can be read afresh."""

    opening = pyqtSignal()

    def showPopup(self) -> None:
        self.opening.emit()
        super().showPopup()


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
        self.source_type_input = QComboBox()
        self.source_type_input.addItem("Choose plate type", "")
        for plate_type in SOURCE_PLATE_TYPES:
            self.source_type_input.addItem(
                f"{plate_type.label} ({_percent(plate_type.dead_ul)}–{_percent(plate_type.max_ul)} µL)",
                plate_type.id,
            )
            self.source_type_input.setItemData(
                self.source_type_input.count() - 1,
                f"{plate_type.part_number} · calibration {plate_type.id}\n"
                f"For: {plate_type.fluids}\n"
                "The lower end is the dead volume a well cannot give; the upper end is its "
                "maximum fill.",
                Qt.ToolTipRole,
            )
        # Wide enough for its longest name, so the working range is never cut off.
        self.source_type_input.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        # Shared plates to choose from, re-read each time the list opens, and New plate….
        self.source_plate_input = _PlateChoice()
        self.source_plate_input.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.source_plate_input.setMinimumWidth(200)
        # True while the Plate field takes a new plate's name typed into it.
        self._naming_plate = False
        self.new_plate_cancel_button = QPushButton("Cancel")
        self.new_plate_cancel_button.setObjectName("LinkButton")
        self.new_plate_cancel_button.hide()
        self.source_well_input = QLineEdit()
        self.source_well_input.setPlaceholderText("Wells, e.g. A1 or A1, A2")
        self.source_well_input.setMinimumWidth(130)
        self.source_well_input.setMaximumWidth(170)
        # What to have ready: crystal drops, and each solvent's source wells and volumes.
        self.preparation_label = QLabel()
        self.preparation_label.setTextFormat(Qt.RichText)
        self.preparation_label.setWordWrap(True)
        self.source_message_label = QLabel()
        self.source_message_label.setObjectName("SourceMessage")
        self.source_message_label.setWordWrap(True)
        self.source_message_label.hide()
        # A problem shown in the source box, so it is not repeated above the table.
        self._source_problem: str | None = None
        self._source_well_error: str | None = None
        # The shared source plates' records (see infrastructure.source_plate_record).
        self._source_record = None
        self._username = ""
        # None for a plate whose record could not be read; it is read again when asked afresh.
        self._plate_records: dict[str, tuple[PlateRecordEntry, ...] | None] = {}
        # Additives whose wells were filled in by the rule rather than typed.
        self._auto_wells: set[str] = set()
        self.fit_button = QPushButton()
        self.fit_button.hide()
        self.fit_note_label = QLabel()
        self.fit_note_label.setObjectName("Muted")
        self.fit_note_label.setWordWrap(True)
        self.fit_note_label.hide()
        # The shared plates as last listed; None if they could not be read.
        self._plate_list: tuple[PlateSummary, ...] | None = None
        # Read once, then again only when the list is opened or the record changes.
        self._plate_list_read = False
        # Wells skipped past the rule's first free well, per additive (e.g. a dusty well).
        # Nothing is recorded for them: others start after the last used well anyway.
        self._well_offsets: dict[str, int] = {}
        self.skip_button = QPushButton("Skip well")
        self.skip_button.setObjectName("LinkButton")
        self.previous_button = QPushButton("Use previous well")
        self.previous_button.setObjectName("LinkButton")
        self.record_note_label = QLabel()
        self.record_note_label.setObjectName("Muted")
        self.record_note_label.setWordWrap(True)
        self.record_note_label.hide()

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
        self.source_plate_input.opening.connect(lambda: self._fill_plate_choices(fresh=True))
        self.source_plate_input.activated.connect(self._plate_chosen)
        self.new_plate_cancel_button.clicked.connect(self._end_new_plate)
        self.source_well_input.editingFinished.connect(self._source_wells_edited)
        self.source_type_input.activated.connect(
            lambda _index: self._source_plate_edited(
                source_plate_type=self.source_type_input.currentData()
            )
        )
        self.skip_button.clicked.connect(self.skip_well)
        self.fit_button.clicked.connect(self.fit_to_selected_wells)
        self.previous_button.clicked.connect(self.use_previous_well)
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

        # The title sits above the fields, so plate names that say what they hold fit in one row.
        source_title = QLabel("Preparation")
        source_title.setStyleSheet("font-weight: 500;")
        # Which plate on one line, which of its wells on the next.
        fields = QHBoxLayout()
        fields.setSpacing(theme.SPACING_M)
        fields.addWidget(_field_label("Plate"))
        fields.addWidget(self.source_plate_input)
        fields.addWidget(self.new_plate_cancel_button)
        fields.addSpacing(theme.SPACING_L)
        fields.addWidget(_field_label("Plate type"))
        fields.addWidget(self.source_type_input)
        fields.addStretch()
        wells = QHBoxLayout()
        wells.setSpacing(theme.SPACING_M)
        wells.addWidget(_field_label("Wells"))
        wells.addWidget(self.source_well_input)
        wells.addWidget(self.skip_button)
        wells.addWidget(self.previous_button)
        wells.addStretch()
        source = QVBoxLayout()
        source.setContentsMargins(theme.SPACING_L, theme.SPACING_M, theme.SPACING_L, theme.SPACING_M)
        source.setSpacing(theme.SPACING_S)
        source.addWidget(source_title)
        source.addWidget(self.preparation_label)
        source.addSpacing(theme.SPACING_S)
        source.addLayout(fields)
        source.addLayout(wells)
        source.addWidget(self.source_message_label)
        source.addWidget(self.record_note_label)
        self.source_box.setLayout(source)
        # As wide as the page: it lists what the whole test needs.
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_box)

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
        layout.addWidget(self.advanced_button, 0, Qt.AlignLeft)
        layout.addWidget(self.advanced_box)
        layout.addWidget(self.design_error_label)
        layout.addSpacing(theme.SPACING_S)
        layout.addLayout(matrix)
        # Last: what to put in the source plate follows from everything above.
        layout.addSpacing(theme.SPACING_M)
        layout.addLayout(source_row)
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
        # Too few wells selected: an explicit, previewed way to fit the test to them.
        fit = QHBoxLayout()
        fit.addWidget(self.fit_button)
        fit.addWidget(self.fit_note_label, 1)
        fit.addStretch()
        widget.layout().insertLayout(widget.layout().indexOf(self.error_label) + 1, fit)
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
            (self.source_well_input, ", ".join(additive.source_wells)),
            (self.smiles_input, additive.smiles),
        )
        for widget, text in texts:
            if not widget.property("invalid") and widget.text() != text:
                widget.setText(text)
                widget.setCursorPosition(0)
        self.source_type_input.setCurrentIndex(
            max(0, self.source_type_input.findData(additive.source_plate_type))
        )
        self._fill_plate_choices()
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
        self._source_well_error = None
        _set_flag(self.source_well_input, "invalid", False)
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

    # -- Shared source plates ----------------------------------------------------------

    def set_source_record(self, record, username: str) -> None:
        """The shared record of source plate wells; None where no ECHO folder is set."""
        self._source_record = record
        self._username = username
        self._plate_records = {}
        self._plate_list_read = False
        self.refresh_plan()

    def refresh_plate_records(self) -> None:
        """Read the shared records again, e.g. after this experiment's wells were recorded."""
        self._plate_records = {}
        self._plate_list_read = False
        self.refresh_plan()

    def _plate_entries(self, plate: str, fresh: bool = False) -> tuple[PlateRecordEntry, ...] | None:
        """A plate's record, read once per plate unless ``fresh``; None if it cannot be read."""
        if self._source_record is None or not plate:
            return None
        if fresh or plate not in self._plate_records:
            try:
                self._plate_records[plate] = self._source_record.entries(plate)
                if self.record_note_label.text().endswith(RECHECK_NOTE):
                    _show_message(self.record_note_label, "")
            except SourcePlateRecordUnavailable as error:
                self._plate_records[plate] = None
                _show_message(self.record_note_label, f"{_sentence(error)} {RECHECK_NOTE}")
        return self._plate_records[plate]

    def _records_for_checks(self) -> dict[str, tuple[PlateRecordEntry, ...]]:
        """Other experiments' entries for each plate named here, with skips not yet recorded."""
        own = self.assigned_experiment_id
        records = {}
        for additive in self.design.additives:
            plate = additive.source_plate.strip()
            if not plate or not additive.source_plate_type or plate in records:
                continue
            entries = self._plate_entries(plate) or ()
            records[plate] = tuple(
                entry for entry in entries if not own or entry.experiment_id != own
            )
        return records

    def _shared_plates(self) -> tuple[PlateSummary, ...] | None:
        """Plates on the shared record, or None when there is none or it cannot be read."""
        if self._source_record is None:
            return None
        try:
            return self._source_record.plates()
        except SourcePlateRecordUnavailable as error:
            _show_message(self.record_note_label, f"{_sentence(error)} {RECHECK_NOTE}")
            return None

    def _shared_plate_hint(self) -> str:
        """The shared plates' state, said while no plate is chosen."""
        if self._source_record is None:
            return ""
        self._read_plate_list()
        count = len(self._plate_list or ())
        if not count:
            return " No shared plates yet: choose New plate… in Plate to start one."
        return (
            f" Choose one of {count} shared plate{'' if count == 1 else 's'} in Plate, "
            "or start one with New plate…"
        )

    def _read_plate_list(self, fresh: bool = False) -> None:
        if fresh or not self._plate_list_read:
            self._plate_list = self._shared_plates()
            self._plate_list_read = True

    def _fill_plate_choices(self, fresh: bool = False) -> None:
        """Plate list: shared plates with how far they are used, this plate, New plate…."""
        if self._naming_plate:
            return  # Rebuilding the list would wipe the name being typed.
        self._read_plate_list(fresh)
        combo = self.source_plate_input
        current = self.design.additive(self._series.additive_id).source_plate.strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Choose plate", "")
        selected = 0
        for summary in self._plate_list or ():
            combo.addItem(summary.label, summary.name)
            if current and summary.name.casefold() == current.casefold():
                selected = combo.count() - 1
        if self._plate_list is None and self._source_record is not None:
            combo.addItem("Shared plates cannot be read now", None)
            combo.model().item(combo.count() - 1).setEnabled(False)
        if current and not selected:
            shared = self._source_record is not None
            combo.addItem(f"{current} · not a shared plate yet" if shared else current, current)
            selected = combo.count() - 1
        combo.insertSeparator(combo.count())
        combo.addItem("New plate…", NEW_PLATE)
        combo.setCurrentIndex(selected)
        combo.blockSignals(False)

    def _plate_chosen(self, index: int) -> None:
        data = self.source_plate_input.itemData(index)
        if data == NEW_PLATE:
            if not self._naming_plate:
                # Only once the list has closed: changing the combo while it closes
                # leaves the focus in the closing list, where typing is lost.
                QTimer.singleShot(0, self._begin_new_plate)
            return
        if self._naming_plate:
            self._end_new_plate()
        if not data:
            return
        summary = next((item for item in self._plate_list or () if item.name == data), None)
        if summary is not None:
            self.choose_shared_plate(summary)
        else:
            self._source_plate_edited(source_plate=data)

    def choose_shared_plate(self, summary: PlateSummary) -> None:
        """Continue a shared plate: its name, its type, and its next free wells."""
        changes = {"source_plate": summary.name}
        if summary.plate_type is not None:
            changes["source_plate_type"] = summary.plate_type.id
        self._source_plate_edited(**changes)

    def _begin_new_plate(self) -> None:
        if self._naming_plate:
            return
        """The Plate field itself takes the new plate's name.

        Typing goes where the list was, so the focus never has to move to another
        field, from which closing the list could take it away.
        """
        combo = self.source_plate_input
        self._naming_plate = True
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        # No completion: typing "S" must not turn into a shared plate's whole label.
        combo.setCompleter(None)
        line = combo.lineEdit()
        line.setPlaceholderText("New plate name")
        line.setToolTip("e.g. SOLVENT-02. A name already shared continues that plate.")
        line.returnPressed.connect(self._new_plate_named)
        # Clicking elsewhere after typing a name also names the plate.
        line.editingFinished.connect(self._new_plate_named)
        # Escape reaches the combo, which holds the focus, or its text field.
        line.installEventFilter(self)
        combo.installEventFilter(self)
        combo.setEditText("")
        self.new_plate_cancel_button.show()
        _show_message(self.record_note_label, NAMING_NOTE)
        combo.activateWindow()
        combo.setFocus(Qt.OtherFocusReason)
        self._render_source()

    def _end_new_plate(self) -> None:
        combo = self.source_plate_input
        if self._naming_plate:
            self._naming_plate = False
            combo.setEditable(False)
        self.new_plate_cancel_button.hide()
        self._fill_plate_choices()
        if self.record_note_label.text() == NAMING_NOTE:
            _show_message(self.record_note_label, "")

    def enter_new_plate(self, name: str) -> None:
        self._begin_new_plate()
        self.source_plate_input.setEditText(name)
        self._new_plate_named()

    def _new_plate_named(self) -> None:
        """A new plate starts at its first well; a name already shared continues that plate."""
        if not self._naming_plate:
            return
        name = self.source_plate_input.currentText().strip()
        if not name:
            return  # Still to be typed; Cancel or Escape goes back to the list.
        self._end_new_plate()
        self._read_plate_list(fresh=True)
        known = {summary.name.casefold(): summary for summary in self._plate_list or ()}
        if name.casefold() in known:
            self.choose_shared_plate(known[name.casefold()])
            _show_message(
                self.record_note_label,
                f"{known[name.casefold()].name} is already a shared plate; it is continued.",
            )
            return
        self._source_plate_edited(source_plate=name)
        if self._source_record is not None:
            _show_message(self.record_note_label, f"New plate {name}: its wells start at A1.")

    def _source_plate_edited(self, **changes) -> None:
        """A new plate or plate type: its wells are that plate's next free ones.

        Wells belong to a plate, so with a shared record they are picked again; without
        one, wells already typed are kept.
        """
        before = self.design.additive(self._series.additive_id)
        self.update_additive(**changes)
        current = self.design.additive(self._series.additive_id)
        changed = (before.source_plate, before.source_plate_type) != (
            current.source_plate, current.source_plate_type
        )
        if (
            current.source_plate and current.source_plate_type
            and (not current.source_wells or (changed and self._source_record is not None))
        ):
            self.use_next_wells()

    def _next_wells_for(self, additive, fresh: bool = False) -> tuple[str, ...] | None:
        """The wells an additive should use: after the plate's last used one, as many as needed.

        A new plate starts at A1. None when the plate, its type or the volumes are not
        known yet, or the plate has too few wells left.
        """
        plate = additive.source_plate.strip()
        if not plate or not additive.source_plate_type:
            return None
        plate_type = source_plate_type(additive.source_plate_type)
        try:
            transfers = additive_transfers(self.design).get(additive.id, ())
            needed = allocate_source_wells(plate_type, transfers, ()).wells_needed
        except ValueError:
            return None  # The Preparation box already says what to fix.
        if not needed:
            return ()
        if fresh:
            self._plate_entries(plate, fresh=True)
        entries = self._records_for_checks().get(plate, ())
        others = {
            well for item in self.design.additives
            if item.id != additive.id and item.source_plate.strip() == plate
            for well in item.source_wells
        }
        offset = self._well_offsets.get(additive.id, 0)
        wells = next_free_wells(plate_type.well_format, entries, needed + offset, others)[offset:]
        if len(wells) < needed:
            _show_message(
                self.record_note_label,
                f"Only {len(wells)} free wells are left on {plate}; start a new plate with a new name.",
            )
            return None
        return wells

    def use_next_wells(self) -> bool:
        """Enter as many wells as the transfers need, after the last one used on the plate."""
        additive = self.design.additive(self._series.additive_id)
        if not additive.source_plate.strip() or not additive.source_plate_type:
            _show_message(self.record_note_label, "Choose the plate type and enter the plate first.")
            return False
        self._well_offsets.pop(additive.id, None)
        return self._apply_rule_wells(additive)

    def _apply_rule_wells(self, additive) -> bool:
        wells = self._next_wells_for(additive, fresh=True)
        if wells is None:
            return False
        if not wells:
            _show_message(self.record_note_label, "Nothing is transferred from this source.")
            return False
        plate = additive.source_plate.strip()
        if self._source_record is not None and self._plate_records.get(plate) is not None:
            _show_message(self.record_note_label, "")
        self._source_well_error = None
        _set_flag(self.source_well_input, "invalid", False)
        # Filled in by the rule, these wells follow the conditions from now on.
        self._auto_wells.add(additive.id)
        self.update_additive(source_well=wells[0], extra_source_wells=tuple(wells[1:]))
        self._load_design_inputs()
        return True

    def _sync_auto_wells(self) -> None:
        """Wells filled in by the rule keep up with the conditions: as many as now needed."""
        changed = False
        for additive in self.design.additives:
            if (
                not additive.source_wells and additive.source_plate.strip()
                and additive.source_plate_type
            ):
                # A plate and type but no wells yet, e.g. a draft opened again: fill them in.
                self._auto_wells.add(additive.id)
            if additive.id not in self._auto_wells:
                continue
            wells = self._next_wells_for(additive)
            if wells is None or len(wells) == len(additive.source_wells):
                continue
            updated = replace(
                additive, source_well=wells[0] if wells else "", extra_source_wells=tuple(wells[1:])
            )
            self.design = replace(
                self.design,
                additives=tuple(updated if item.id == additive.id else item for item in self.design.additives),
            )
            changed = True
        if changed:
            self._load_design_inputs()

    def skip_well(self) -> bool:
        """Don't use the first well given, e.g. one with dust in it; the next one is used."""
        additive = self.design.additive(self._series.additive_id)
        if not additive.source_wells:
            return False
        skipped = additive.source_wells[0]
        self._well_offsets[additive.id] = self._well_offsets.get(additive.id, 0) + 1
        if not self._apply_rule_wells(additive):
            self._well_offsets[additive.id] -= 1
            return False
        _show_message(self.record_note_label, f"{skipped} skipped.")
        return True

    def use_previous_well(self) -> bool:
        """Take back a skip: start one well earlier, never before the plate's first free well."""
        additive = self.design.additive(self._series.additive_id)
        offset = self._well_offsets.get(additive.id, 0)
        if not offset:
            return False
        self._well_offsets[additive.id] = offset - 1
        if not self._apply_rule_wells(additive):
            self._well_offsets[additive.id] = offset
            return False
        _show_message(self.record_note_label, "")
        return True

    def _source_wells_edited(self) -> None:
        """Wells as typed, "A1" or "A1, A2"; one not on the chosen plate changes nothing."""
        current = self.design.additive(self._series.additive_id)
        text = self.source_well_input.text()
        try:
            if current.source_plate_type:
                plate_type = source_plate_type(current.source_plate_type)
                wells = parse_wells(text, plate_type.well_format)
            else:
                wells = tuple(dict.fromkeys(item.upper() for item in re.split(r"[,\s]+", text) if item))
        except ValueError as error:
            self._source_well_error = _sentence(error)
            _set_flag(self.source_well_input, "invalid", True)
            self._render_source()
            return
        self._source_well_error = None
        _set_flag(self.source_well_input, "invalid", False)
        # Typed by the user: kept as typed, no longer following the conditions.
        self._auto_wells.discard(self._series.additive_id)
        self.update_additive(
            source_well=wells[0] if wells else "", extra_source_wells=tuple(wells[1:])
        )
        self._render_source()

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
        if (
            self._naming_plate
            and watched in (self.source_plate_input, self.source_plate_input.lineEdit())
            and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape
        ):
            self._end_new_plate()
            return True
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
        """What is missing from the source, or how many wells to fill and with how much."""
        additive = self.design.additive(self._series.additive_id)
        absent = {
            "plate type": not additive.source_plate_type,
            "plate": not additive.source_plate,
            "wells": not additive.source_well,
        }
        for field, name in (
            (self.source_type_input, "plate type"), (self.source_plate_input, "plate"),
            (self.source_well_input, "wells"),
        ):
            _set_flag(field, "missing", absent[name])
        missing = [name for name, gone in absent.items() if gone]
        status = None
        statuses: tuple = ()
        try:
            transfers = additive_transfers(self.design)
        except ValueError:
            transfers = None
        else:
            statuses = source_statuses(self.design, transfers, True, self._records_for_checks())
            status = next((item for item in statuses if item.additive.id == additive.id), None)
        self._source_problem = status.problem if status is not None else None
        self._render_preparation(transfers, statuses)
        if self._source_well_error:
            message, attention = self._source_well_error, True
        elif missing:
            names = ", ".join(missing[:-1]) + (" and " if len(missing) > 1 else "") + missing[-1]
            message, attention = f"Enter the source {names}; the ECHO worksheet needs them.", True
            if "plate" in missing and not self._naming_plate:
                message += self._shared_plate_hint()
        elif status is not None and status.problem:
            message, attention = status.problem, True
        else:
            # Ready: which wells to fill, and with how much, is listed under Preparation.
            message, attention = "", False
        _set_flag(self.source_message_label, "attention", attention)
        _set_flag(self.source_box, "missing", attention)
        _show_message(self.source_message_label, message)
        rule_ready = bool(
            additive.source_wells and additive.source_plate.strip() and additive.source_plate_type
        )
        self.skip_button.setEnabled(rule_ready)
        self.skip_button.setToolTip(
            f"Don't use {additive.source_wells[0]}, e.g. if it has dust in it; use the next well."
            if rule_ready else "Choose the plate first."
        )
        self.previous_button.setEnabled(rule_ready and self._well_offsets.get(additive.id, 0) > 0)
        self.previous_button.setToolTip("Go back one well, undoing the last skip.")
        drop_missing = self.design.drop_volume_nl is None
        _set_flag(self.drop_volume_input, "missing", drop_missing)
        _set_flag(self.conditions_drop_input, "missing", drop_missing)

    def _render_preparation(self, transfers, statuses) -> None:
        """Crystal drops to select, and for each solvent the wells to fill and with how much."""
        conditions = self.design.conditions()
        drops = self.design.required_crystals
        replicates = {condition.replicates for condition in conditions}
        if len(replicates) == 1 and conditions:
            count = f"{drops} ({_plural(len(conditions), 'condition')} × {replicates.pop()}), one per well"
        else:
            count = f"{drops} for {_plural(len(conditions), 'condition')}, one per well"
        rows = [("Crystal drops", count)]
        by_additive = {status.additive.id: status for status in statuses}
        for additive in self.design.additives:
            # What the drops actually receive, then the source wells to fill for it,
            # from the chosen plate's dead volume and maximum.
            if transfers is None:
                rows.append((additive.name, "Enter the initial drop volume to see how much is used."))
                continue
            if additive.id not in transfers:
                rows.append((additive.name, "None used: no condition adds it."))
                continue
            volumes = transfers[additive.id]
            total = sum(volumes, Decimal("0"))
            rows.append((
                additive.name,
                f"{ul_text(total)} µL in total, into {_plural(len(volumes), 'crystal drop')}",
            ))
            rows.append(("Source wells", self._fill_text(additive, volumes, by_additive.get(additive.id))))
        cells = "".join(
            f'<tr><td style="color:{theme.TEXT_MUTED}; padding:1px 16px 1px 0">'
            f"{html.escape(label)}</td><td>{html.escape(value)}</td></tr>"
            for label, value in rows
        )
        self.preparation_label.setText(f"<table>{cells}</table>")

    def _fill_text(self, additive, volumes, status) -> str:
        """How many source wells, and how much in each: known from the plate type alone.

        The plate's name and wells only say which wells; the dead volume and maximum
        that decide how many come from its type.
        """
        if status is not None and status.allocation is not None and not status.problem:
            return f"{additive.source_plate.strip()}: {status.allocation.instruction()}"
        if not additive.source_plate_type:
            if additive.id != self._series.additive_id:
                return f"Choose {additive.name} above, then its plate type."
            return "Choose the plate type below to see how many wells to fill, and with how much."
        try:
            plate_type = source_plate_type(additive.source_plate_type)
            return allocate_source_wells(plate_type, volumes, ()).instruction()
        except ValueError as error:
            return _sentence(error)

    def preparation_rows(self) -> list[str]:
        """The preparation list as plain lines, e.g. for checking it."""
        text = self.preparation_label.text()
        return [
            " · ".join(html.unescape(part) for part in re.findall(r"<td[^>]*>(.*?)</td>", row))
            for row in re.findall(r"<tr>(.*?)</tr>", text)
        ]

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
        try:
            transfers = additive_transfers(self.design)
        except ValueError as error:
            return str(error)
        # New tests must say which source plate they use, so its volumes can be checked.
        return next(
            (
                status.problem
                for status in source_statuses(
                    self.design, transfers, True, self._records_for_checks()
                )
                if status.problem
            ),
            None,
        )

    def _selected_well_total(self) -> int | None:
        return len(self.selection.wells) if self.selection is not None else None

    def _update_fit_button(self) -> None:
        """Offered only while fewer wells are selected than the conditions need."""
        wells = self._selected_well_total()
        needed = self.design.required_crystals
        short = wells is not None and 0 < wells < needed
        conditions = len(self.design.conditions())
        self.fit_button.setVisible(short and wells >= conditions)
        if short and wells >= conditions:
            self.fit_button.setText(f"Plan with the {_wells(wells)} selected…")
            self.fit_button.setToolTip(
                "Shows which conditions get fewer crystal drops before anything changes."
            )
        _show_message(
            self.fit_note_label,
            f"{_wells(wells)} cannot hold {_plural(conditions, 'condition')}, one well each: "
            "exclude conditions in Conditions, or select more wells."
            if short and wells < conditions else "",
        )

    def fit_to_selected_wells(self) -> bool:
        """Fewer crystal drops per condition to fit the wells, after showing what changes."""
        wells = self._selected_well_total()
        if wells is None:
            return False
        try:
            fitted, changes = fit_to_wells(self.design, wells)
        except ValueError as error:
            _show_message(self.fit_note_label, _sentence(error))
            return False
        if not changes:
            return False
        lines = "\n".join(
            f"{self.design.label(condition)}: {old} → {new}" for condition, old, new in changes
        )
        if QMessageBox.question(
            self, "Plan with the selected wells",
            f"Plan the test for the {_wells(wells)} selected instead of "
            f"{self.design.required_crystals}? These conditions get fewer crystal drops; "
            f"every condition keeps at least one.\n\n{lines}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return False
        self.design = fitted
        self._load_design_inputs()
        self.refresh_plan()
        return True

    def refresh_plan(self) -> None:
        self._sync_auto_wells()
        self._update_fit_button()
        self._render_source()
        self._render_matrix()
        self._render_condition_panel()
        self.design_error = self._check_design()
        # Problems already marked at their field are not repeated above the table.
        shown_in_place = (self._source_problem, "Enter the initial drop volume.")
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
            allocations = plan.source_allocations()
            totals = ", ".join(
                f"{additive.name} · {additive.source_plate} {allocations[additive.id].fills_text()} "
                f"({allocations[additive.id].fill_note})"
                if additive.id in allocations
                else f"{additive.name} {total.normalize():f} nL from {additive.source_plate} "
                f"{additive.source_well}"
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
