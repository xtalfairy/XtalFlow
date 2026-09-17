"""Planning editors: one experiment project's settings, previews, and delivery."""

from __future__ import annotations

import getpass
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain import (
    CrystalSelection,
    SelectedWellUsage,
    crystal_selection_from_selected_crystals,
)
from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    FragmentLibrary,
    FragmentScreenPlan,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.domain.labwork import (
    LABWORK_COLUMNS,
    build_fragment_labworks,
    build_raw_crystal_labworks,
)
from xtalflow.domain.plan_lifecycle import PlanningDraft
from xtalflow.domain.worksheets import (
    ECHO_HEADER,
    SHIFTER_HEADER,
    build_echo_worksheet,
    build_shifter_worksheet,
)
from xtalflow.infrastructure.mxlive_config import MxLiveAccount
from xtalflow.ui import theme


def _preview_table(headers: tuple[str, ...], stretch_last: bool = False) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(stretch_last)
    return table


class PlanEditorBase(QWidget):
    """One experiment's plan: its inputs, previews, and MxLive records.

    The editor holds the plan state; the experiment screen places its widgets
    into the guided steps (Setup, Conditions, Review, Worksheets).
    """

    draft_changed = pyqtSignal()
    webdb_upload_requested = pyqtSignal()

    def __init__(
        self,
        selection: CrystalSelection | tuple[SelectedCrystal, ...] | None,
        transient_project_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._transient_project_id = transient_project_id
        self.selection: CrystalSelection | None = None
        if isinstance(selection, CrystalSelection):
            self.selection = selection
        elif selection:
            self.selection = crystal_selection_from_selected_crystals(
                transient_project_id, selection
            )
        self.current_plan = None
        self.current_experiment_id: str | None = None
        self.assigned_experiment_id: str | None = None
        self.mxlive_account: MxLiveAccount | None = None
        self.well_usage: dict[str, tuple[SelectedWellUsage, ...]] = {}
        self._experiment_id_provider: Callable[[str], str] | None = None
        self.step_pages: dict = {}

        self.name_input = QLineEdit()
        self.name_input.setMaximumWidth(480)
        self.protein_input = QLineEdit()
        self.protein_input.setPlaceholderText("e.g. BRD4")
        self.protein_input.setMaximumWidth(480)
        self.lifecycle_label = QLabel("Draft · not saved")
        self.checklist_label = QLabel()
        self.checklist_label.setWordWrap(True)
        self.checklist_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.destinations_text = ""
        self.experiment_id_label = QLabel("Experiment ID: determined when finalized")
        self.experiment_id_label.setObjectName("Muted")
        self.experiment_id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.order_input = QComboBox()
        self.order_input.addItem("Keep selection order", AssignmentOrder.SELECTION)
        self.order_input.addItem("Plate and well order", AssignmentOrder.PLATE_WELL)
        self.order_input.setMaximumWidth(320)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(theme.status_style("attention"))
        self.error_label.hide()

        self.preview_tabs = QTabWidget()
        self.shifter_table = _preview_table(SHIFTER_HEADER)
        self.webdb_status_label = QLabel("MxLive account: not configured")
        self.webdb_status_label.setObjectName("Muted")
        self.webdb_upload_state = ""
        self.webdb_table = _preview_table(LABWORK_COLUMNS)
        self.webdb_upload_button = QPushButton("Upload to MxLive…")
        self.webdb_upload_button.setEnabled(False)

        self.webdb_upload_button.clicked.connect(self.webdb_upload_requested.emit)
        self.protein_input.textChanged.connect(self._refresh_experiment_id)
        self.protein_input.textChanged.connect(self.draft_changed.emit)
        self.name_input.textEdited.connect(self.draft_changed.emit)

    def mxlive_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.webdb_status_label)
        layout.addWidget(self.webdb_table, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.webdb_upload_button)
        layout.addLayout(actions)
        widget.setLayout(layout)
        return widget

    def conditions_widget(self) -> QWidget | None:
        return None

    def review_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.checklist_label)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_tabs, 1)
        layout.addWidget(self.experiment_id_label)
        widget.setLayout(layout)
        return widget

    def set_title(self, name: str) -> None:
        if self.name_input.text() != name:
            self.name_input.setText(name)

    def _refresh_facts(self) -> None:
        self.checklist_label.setText("\n".join(self._checklist_lines()))

    def _checklist_lines(self) -> list[str]:
        """What finalizing will fix, one checked fact per line."""
        wells = self.selection.wells if self.selection is not None else ()
        positions = sum(len(well.soaking_positions) for well in wells)
        lines = [
            f"{theme.SYMBOL_OK if wells else theme.SYMBOL_ATTENTION} "
            f"{_plural(len(wells), 'well')} · {_plural(positions, 'position')}"
        ]
        reused = sum(1 for well in wells if self.well_usage.get(well.image_key))
        if reused:
            lines.append(
                f"{theme.SYMBOL_ATTENTION} {_plural(reused, 'well')} already used in "
                "another experiment · see Usage"
            )
        if self.destinations_text:
            lines.append(f"{theme.SYMBOL_OK} Worksheets for {self.destinations_text}")
        return lines

    def set_destinations(self, text: str) -> None:
        self.destinations_text = text
        self._refresh_facts()

    def set_crystals(self, crystals: tuple[SelectedCrystal, ...]) -> None:
        project_id = (
            self.selection.project_id if self.selection is not None
            else getattr(self, "plan_id", self._transient_project_id)
        )
        self.selection = (
            crystal_selection_from_selected_crystals(project_id, crystals)
            if crystals else None
        )
        self.refresh_plan()

    def set_selection(self, selection: CrystalSelection | None) -> None:
        self.selection = selection
        self.refresh_plan()

    def set_well_usage(self, usage: dict[str, tuple[SelectedWellUsage, ...]]) -> None:
        self.well_usage = usage
        previous = self.blockSignals(True)
        self.refresh_plan()
        self.blockSignals(previous)

    def set_mxlive_account(self, account: MxLiveAccount) -> None:
        self.mxlive_account = account
        self.refresh_plan()

    def set_experiment_id_provider(self, provider: Callable[[str], str]) -> None:
        self._experiment_id_provider = provider
        self._refresh_experiment_id()

    def _refresh_experiment_id(self) -> None:
        if self.assigned_experiment_id:
            self.current_experiment_id = self.assigned_experiment_id
            self.experiment_id_label.setText(
                f"Experiment ID: {self.assigned_experiment_id} · fixed for this experiment"
            )
        elif self._experiment_id_provider is None:
            self.current_experiment_id = None
            self.experiment_id_label.setText("Experiment ID: determined when finalized")
        else:
            try:
                self.current_experiment_id = self._experiment_id_provider(
                    self.protein_input.text()
                )
                self.experiment_id_label.setText(
                    f"Experiment ID when finalized: {self.current_experiment_id}"
                )
            except (ValueError, ReviewPersistenceError) as error:
                self.current_experiment_id = None
                self.experiment_id_label.setText(f"Experiment ID: {error}")
        self._refresh_webdb_preview()

    def _record_builder(self) -> Callable:
        raise NotImplementedError

    def _refresh_webdb_preview(self) -> None:
        _refresh_labwork_preview(
            self.webdb_table,
            self.webdb_status_label,
            self.current_plan,
            self.current_experiment_id,
            self.protein_input.text(),
            self.mxlive_account,
            self._record_builder(),
        )
        if self.webdb_upload_state:
            self.webdb_status_label.setText(
                f"{self.webdb_status_label.text()} · {self.webdb_upload_state}"
            )

    def _require_selection(self) -> CrystalSelection:
        if self.selection is None:
            raise ValueError("Select at least one well to continue.")
        return self.selection

    def _show_plan_error(self, error: ValueError) -> None:
        self.current_plan = None
        self.error_label.setText(str(error))
        self.error_label.show()
        self.webdb_table.setRowCount(0)
        self._refresh_facts()
        self._refresh_webdb_preview()
        self.draft_changed.emit()

    @staticmethod
    def _set_preview_rows(table: QTableWidget, rows: tuple[tuple[str, ...], ...]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                table.setItem(row_index, column, item)


class FragmentScreeningEditor(PlanEditorBase):
    """Assign library fragments to selected wells and preview ECHO/SHIFTER output."""

    library_refresh_requested = pyqtSignal()

    def __init__(
        self,
        library: FragmentLibrary | None,
        selection: CrystalSelection | tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(selection, "transient-fragment-editor", parent)
        self.library: FragmentLibrary | None = None
        self.library_id: str | None = None
        self.library_input = QComboBox()
        self.library_input.setMinimumWidth(260)
        self.refresh_libraries_button = QPushButton("Refresh")
        self.refresh_libraries_button.setToolTip("Reload fragment library CSV files")
        self.library_label = QLabel("No library imported")
        self.library_label.setObjectName("Muted")
        self.rows_input = QLineEdit()
        self.rows_input.setPlaceholderText("e.g. 1-96, 101, 105-120")
        self.rows_input.setToolTip(
            "One-based CSV data rows. The header and the CSV No column are not counted."
        )
        self.volume_input = QDoubleSpinBox()
        self.volume_input.setRange(2.5, 10000.0)
        self.volume_input.setSingleStep(2.5)
        self.volume_input.setDecimals(1)
        self.volume_input.setSuffix(" nL / well")
        self.volume_input.setValue(25.0)
        self.table = _preview_table(
            (
                "Order", "Plate", "Well", "Positions", "Usage", "Fragment", "Source",
                "Total", "Per position",
            ),
            stretch_last=True,
        )
        self.all_rows_radio = QRadioButton("All rows")
        self.choose_rows_radio = QRadioButton("Choose rows")
        self.all_rows_radio.setChecked(True)
        self.rows_input.setEnabled(False)
        self.conditions_error_label = QLabel()
        self.conditions_error_label.setWordWrap(True)
        self.conditions_error_label.setStyleSheet(theme.status_style("attention"))
        self.conditions_error_label.hide()
        # Changing the order after seeing assignments shows what moves first.
        self.reassignment_label = QLabel()
        self.reassignment_label.setWordWrap(True)
        self.apply_reassignment_button = QPushButton("Apply reassignment")
        self.keep_assignment_button = QPushButton("Keep current order")
        self.reassignment_panel = QWidget()
        reassignment_layout = QVBoxLayout()
        reassignment_layout.setContentsMargins(0, 0, 0, 0)
        reassignment_layout.addWidget(self.reassignment_label)
        reassignment_actions = QHBoxLayout()
        reassignment_actions.addWidget(self.apply_reassignment_button)
        reassignment_actions.addWidget(self.keep_assignment_button)
        reassignment_actions.addStretch()
        reassignment_layout.addLayout(reassignment_actions)
        self.reassignment_panel.setLayout(reassignment_layout)
        self.reassignment_panel.hide()
        self._pending_order: AssignmentOrder | None = None
        self.echo_table = _preview_table(ECHO_HEADER)

        self.count_label = QLabel()
        self.preview_tabs.addTab(self.table, "Assignments")
        self.preview_tabs.addTab(self.echo_table, "ECHO worksheet")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER worksheet")

        self.rows_input.textChanged.connect(self.refresh_plan)
        self.volume_input.valueChanged.connect(self.refresh_plan)
        self.order_input.currentIndexChanged.connect(self._order_changed)
        self.all_rows_radio.toggled.connect(self._row_mode_changed)
        self.apply_reassignment_button.clicked.connect(self._apply_reassignment)
        self.keep_assignment_button.clicked.connect(self._keep_assignment)
        self.library_input.currentIndexChanged.connect(self._library_changed)
        self.refresh_libraries_button.clicked.connect(self.library_refresh_requested.emit)
        if library is not None:
            self.set_library_choices(
                (("direct", f"{library.name} · {len(library.fragments)} rows", library),),
                "direct",
            )
        self.refresh_plan()

    def _record_builder(self) -> Callable:
        return build_fragment_labworks

    def conditions_widget(self) -> QWidget:
        library_row = QHBoxLayout()
        library_row.addWidget(self.library_input, 1)
        library_row.addWidget(self.refresh_libraries_button)
        library_row.addStretch()
        rows_hint = QLabel("Data rows start at 1; the header is not counted.")
        rows_hint.setObjectName("Muted")
        volume_hint = QLabel(
            "Shared equally between the positions in each well, in 2.5 nL steps."
        )
        volume_hint.setObjectName("Muted")
        self.library_input.setMaximumWidth(480)
        self.rows_input.setMaximumWidth(320)
        self.volume_input.setMaximumWidth(200)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        rows_row = QHBoxLayout()
        rows_row.addWidget(self.all_rows_radio)
        rows_row.addWidget(self.choose_rows_radio)
        rows_row.addWidget(self.rows_input)
        rows_row.addStretch()
        form.addRow("Library *", library_row)
        form.addRow("", self.library_label)
        form.addRow("Fragments", rows_row)
        form.addRow("", rows_hint)
        form.addRow("Total volume/well *", self.volume_input)
        form.addRow("", volume_hint)
        form.addRow("Assignment order", self.order_input)
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.count_label)
        layout.addWidget(self.conditions_error_label)
        layout.addWidget(self.reassignment_panel)
        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def set_library_choices(
        self,
        choices: tuple[tuple[str, str, FragmentLibrary], ...],
        selected_id: str | None = None,
    ) -> None:
        previous_id = selected_id or self.library_input.currentData(Qt.UserRole)
        self.library_input.blockSignals(True)
        self.library_input.clear()
        self.library_input.addItem("Select imported library…", None)
        for library_id, label, library in choices:
            self.library_input.addItem(label, library_id)
            self.library_input.setItemData(
                self.library_input.count() - 1, library, Qt.UserRole + 1
            )
        self._select_library_id(previous_id)
        self.library_input.blockSignals(False)
        self._library_changed(self.library_input.currentIndex())

    def _select_library_id(self, library_id: str | None) -> None:
        index = self.library_input.findData(library_id, Qt.UserRole)
        if index < 0 and library_id is not None:
            # Keep the draft's library while its folder is offline instead of
            # saving the draft without one.
            self.library_input.addItem(
                f"Unavailable: {Path(library_id).name}", library_id
            )
            index = self.library_input.count() - 1
        self.library_input.setCurrentIndex(max(0, index))

    def _library_changed(self, index: int) -> None:
        library = self.library_input.itemData(index, Qt.UserRole + 1)
        library_id = self.library_input.itemData(index, Qt.UserRole)
        # Refreshing reloads every CSV; keep the chosen rows unless the path changed.
        changed = library_id != self.library_id
        self.library = library
        self.library_id = library_id
        self._update_library_label()
        if changed:
            if library is None:
                self.rows_input.clear()
            else:
                self.rows_input.setText(f"1-{len(library.fragments)}")
            self._show_row_mode()
        self.refresh_plan()

    def _all_rows_text(self) -> str | None:
        return f"1-{len(self.library.fragments)}" if self.library is not None else None

    def _show_row_mode(self) -> None:
        all_rows = self.rows_input.text() in ("", self._all_rows_text())
        for radio in (self.all_rows_radio, self.choose_rows_radio):
            radio.blockSignals(True)
        self.all_rows_radio.setChecked(all_rows)
        self.choose_rows_radio.setChecked(not all_rows)
        for radio in (self.all_rows_radio, self.choose_rows_radio):
            radio.blockSignals(False)
        self.rows_input.setEnabled(not all_rows)

    def _row_mode_changed(self, all_rows: bool) -> None:
        self.rows_input.setEnabled(not all_rows)
        if all_rows and self._all_rows_text() is not None:
            self.rows_input.setText(self._all_rows_text())
        elif not all_rows:
            self.rows_input.setFocus(Qt.OtherFocusReason)
            self.rows_input.selectAll()

    def _order_changed(self, _index: int) -> None:
        new_order = self.order_input.currentData()
        plan = self.current_plan
        if plan is None or new_order is plan.assignment_order:
            self._keep_assignment()
            self.refresh_plan()
            return
        try:
            candidate = build_fragment_screen_plan(
                plan.library, plan.selection, plan.volume_per_crystal_nl, new_order
            )
        except ValueError:
            self.refresh_plan()
            return
        before = {
            item.selected_well.image_key: item.fragment.compound_id
            for item in plan.assignments
        }
        moves = [
            (item.selected_well, before.get(item.selected_well.image_key), item.fragment.compound_id)
            for item in candidate.assignments
            if before.get(item.selected_well.image_key) != item.fragment.compound_id
        ]
        if not moves:
            self.refresh_plan()
            return
        self._pending_order = new_order
        self.order_input.blockSignals(True)
        self.order_input.setCurrentIndex(self.order_input.findData(plan.assignment_order))
        self.order_input.blockSignals(False)
        shown = [
            f"{well.plate_code} {well.well_address}: {old} → {new}"
            for well, old, new in moves[:8]
        ]
        if len(moves) > 8:
            shown.append(f"…and {len(moves) - 8} more")
        self.reassignment_label.setText(
            f"{theme.SYMBOL_ATTENTION} This order gives {_plural(len(moves), 'well')} a "
            "different fragment:\n" + "\n".join(shown)
        )
        self.reassignment_panel.show()

    def _apply_reassignment(self) -> None:
        order = self._pending_order
        self._keep_assignment()
        if order is None:
            return
        self.order_input.blockSignals(True)
        self.order_input.setCurrentIndex(self.order_input.findData(order))
        self.order_input.blockSignals(False)
        self.refresh_plan()

    def _keep_assignment(self) -> None:
        self._pending_order = None
        self.reassignment_panel.hide()

    def _update_library_label(self) -> None:
        if self.library is not None:
            self.library_label.setText(
                f"{self.library.name} · {len(self.library.fragments)} imported data rows"
            )
        elif self.library_id:
            self.library_label.setText(f"Library unavailable: {self.library_id}")
        else:
            self.library_label.setText("No library selected")

    def refresh_plan(self) -> None:
        try:
            if self.library is None:
                raise ValueError("Choose a fragment library.")
            selection = self._require_selection()
            selected_library = self.library.select_rows(self.rows_input.text())
            self._show_counts(len(selected_library.fragments), len(selection.wells))
            plan = build_fragment_screen_plan(
                selected_library,
                selection,
                Decimal(str(self.volume_input.value())),
                self.order_input.currentData(),
            )
        except ValueError as error:
            if self.selection is None or self.library is None:
                self.count_label.setText("")
            # Without wells there is nothing to check the conditions against yet.
            conditions_error = "" if self.selection is None and self.library else str(error)
            self.conditions_error_label.setText(conditions_error)
            self.conditions_error_label.setVisible(bool(conditions_error))
            self.table.setRowCount(0)
            self.echo_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self._show_plan_error(error)
            return
        self.current_plan = plan
        self.error_label.setText("")
        self.error_label.hide()
        self.conditions_error_label.hide()
        self.table.setRowCount(len(plan.assignments))
        for row, assignment in enumerate(plan.assignments):
            selected_well = assignment.selected_well
            usage_label, usage_tooltip = _well_usage_display(
                self.well_usage.get(selected_well.image_key, ())
            )
            values = (
                str(row + 1),
                selected_well.plate_code,
                selected_well.well_address,
                str(len(selected_well.soaking_positions)),
                usage_label,
                assignment.fragment.compound_id,
                f"{assignment.fragment.source_plate} / {assignment.fragment.source_well}",
                f"{assignment.total_volume_nl} nL",
                f"{assignment.transfers[0].volume_nl} nL × {len(assignment.transfers)}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setToolTip(usage_tooltip)
                    if usage_tooltip:
                        item.setForeground(QColor(theme.ATTENTION))
                self.table.setItem(row, column, item)
        self._set_preview_rows(
            self.echo_table, tuple(row.values() for row in build_echo_worksheet(plan))
        )
        self._set_preview_rows(
            self.shifter_table, tuple(row.values() for row in build_shifter_worksheet(plan))
        )
        self._refresh_facts()
        self._refresh_experiment_id()
        self.draft_changed.emit()

    def _checklist_lines(self) -> list[str]:
        lines = super()._checklist_lines()
        plan = self.current_plan
        if isinstance(plan, FragmentScreenPlan):
            used = len(plan.assignments)
            line = (
                f"{theme.SYMBOL_OK} {_plural(used, 'fragment')} from {plan.library.name}"
                f" (rows {self.rows_input.text()})"
            )
            if plan.unused_fragments:
                line += f" · last {len(plan.unused_fragments)} not used"
            lines.insert(1, line)
            lines.insert(
                2,
                f"{theme.SYMBOL_OK} {plan.volume_per_crystal_nl} nL per well, shared "
                "equally between its positions",
            )
        return lines

    def _show_counts(self, fragments: int, wells: int) -> None:
        if fragments < wells:
            text = (
                f"{theme.SYMBOL_ATTENTION} {wells} wells, {fragments} fragments — choose "
                f"{wells - fragments} more fragments or edit the selected wells."
            )
            kind = "attention"
        elif fragments > wells:
            text = (
                f"{theme.SYMBOL_OK} {fragments} fragments → {wells} wells · the last "
                f"{fragments - wells} are not used"
            )
            kind = "ok"
        else:
            text = f"{theme.SYMBOL_OK} {fragments} fragments → {wells} wells · counts match"
            kind = "ok"
        self.count_label.setText(text)
        self.count_label.setStyleSheet(theme.status_style(kind))

    def restore_draft(self, draft: PlanningDraft) -> None:
        widgets = (self.library_input, self.rows_input, self.protein_input,
                   self.volume_input, self.order_input)
        for widget in widgets:
            widget.blockSignals(True)
        self._select_library_id(draft.library_id)
        self.library = self.library_input.itemData(
            self.library_input.currentIndex(), Qt.UserRole + 1
        )
        self.library_id = self.library_input.currentData(Qt.UserRole)
        self._update_library_label()
        self.rows_input.setText(draft.library_rows)
        self._show_row_mode()
        self.protein_input.setText(draft.protein)
        self.name_input.setText(draft.name)
        self.assigned_experiment_id = draft.experiment_id
        self.volume_input.setValue(float(draft.volume_nl))
        order_index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, order_index))
        for widget in widgets:
            widget.blockSignals(False)
        self.refresh_plan()


class RawCrystalEditor(PlanEditorBase):
    """Harvest selected crystals with SHIFTER, without a soaking step."""

    def __init__(
        self,
        selection: CrystalSelection | tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(selection, "transient-raw-editor", parent)
        self.summary_table = _preview_table(
            ("Order", "Plate", "Well", "Position", "Usage", "Selected at"),
            stretch_last=True,
        )
        self.preview_tabs.addTab(self.summary_table, "Harvest list")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER worksheet")
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self.refresh_plan()

    def _record_builder(self) -> Callable:
        return build_raw_crystal_labworks

    def review_widget(self) -> QWidget:
        widget = super().review_widget()
        order = QHBoxLayout()
        order.addWidget(QLabel("Harvest order"))
        order.addWidget(self.order_input)
        order.addStretch()
        widget.layout().insertLayout(1, order)
        return widget

    def restore_draft(self, draft: PlanningDraft) -> None:
        self.protein_input.blockSignals(True)
        self.order_input.blockSignals(True)
        self.protein_input.setText(draft.protein)
        self.name_input.setText(draft.name)
        self.assigned_experiment_id = draft.experiment_id
        index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, index))
        self.protein_input.blockSignals(False)
        self.order_input.blockSignals(False)
        self.refresh_plan()

    def refresh_plan(self) -> None:
        try:
            plan = build_raw_crystal_plan(
                self._require_selection(), self.order_input.currentData()
            )
            rows = build_shifter_worksheet(plan)
        except ValueError as error:
            self.summary_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self._show_plan_error(error)
            return
        self.current_plan = plan
        self.error_label.setText("")
        self.error_label.hide()
        self.summary_table.setRowCount(len(plan.selections))
        for row, selection in enumerate(plan.selections):
            selected_well = selection.selected_well
            position = selection.position
            usage_label, usage_tooltip = _well_usage_display(
                self.well_usage.get(selected_well.image_key, ())
            )
            values = (
                str(row + 1), selected_well.plate_code,
                selected_well.well_address, str(position.position_order),
                usage_label,
                position.selected_at.isoformat(timespec="seconds"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    # Keep the persistence identity available to the UI without
                    # exposing an implementation UUID as an operator-facing value.
                    item.setData(Qt.UserRole, position.source_target_id)
                if column == 4:
                    item.setToolTip(usage_tooltip)
                    if usage_tooltip:
                        item.setForeground(QColor(theme.ATTENTION))
                self.summary_table.setItem(row, column, item)
        self._set_preview_rows(self.shifter_table, tuple(row.values() for row in rows))
        self._refresh_facts()
        self._refresh_experiment_id()
        self.draft_changed.emit()


def _plural(value: int, noun: str) -> str:
    return f"{value} {noun}{'' if value == 1 else 's'}"


def _well_usage_display(
    usages: tuple[SelectedWellUsage, ...],
) -> tuple[str, str]:
    if not usages:
        return "Original", ""
    label = "Reused" if len(usages) == 1 else f"Reused · {len(usages)} projects"
    tooltip = "Previously used by:\n" + "\n".join(
        f"• {usage.project_name} · {usage.plan_type.value} · {usage.status}"
        for usage in usages
    )
    return label, tooltip


def _refresh_labwork_preview(
    table: QTableWidget,
    status_label: QLabel,
    plan,
    experiment_id: str | None,
    protein_name: str,
    account: MxLiveAccount | None,
    record_builder: Callable,
) -> None:
    """Populate a plan's WebDB preview independently of upload readiness."""
    if plan is None:
        table.setRowCount(0)
        if account is None:
            status_label.setText("MxLive account: not configured")
        else:
            _set_webdb_account_status(status_label, account, 0)
        return
    username = account.username if account is not None else getpass.getuser()
    account_id = account.account_id if account is not None else username
    records = record_builder(
        plan,
        experiment_id=experiment_id or "Pending finalization",
        protein_name=protein_name.strip(),
        username=username,
        account_id=account_id,
    )
    _populate_webdb_table(table, records)
    if account is None:
        status_label.setText(
            f"MxLive account: not configured · {len(records)} preview records"
        )
    else:
        _set_webdb_account_status(status_label, account, len(records))


def _populate_webdb_table(table: QTableWidget, records: tuple) -> None:
    table.setRowCount(len(records))
    for row_index, record in enumerate(records):
        payload = record.to_payload()
        values = tuple(str(payload[column]) for column in LABWORK_COLUMNS)
        tooltip = json.dumps(payload, ensure_ascii=False, indent=2)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


def _set_webdb_account_status(
    label: QLabel, account: MxLiveAccount, record_count: int
) -> None:
    state = "Ready" if account.upload_ready else "Preview only"
    label.setText(
        f"MxLive account: {account.username} · API project_id: "
        f"{account.account_id} · {record_count} records · {state}"
    )
    label.setToolTip("\n".join(account.upload_blockers))
