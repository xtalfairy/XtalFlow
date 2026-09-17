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
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
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
    """Header, plan settings, previews, and the delivery bar shared by plan types."""

    PLAN_TYPE_LABEL = ""

    save_worksheets_requested = pyqtSignal()
    finalize_requested = pyqtSignal()
    draft_changed = pyqtSignal()
    webdb_upload_requested = pyqtSignal()
    adopt_selection_requested = pyqtSignal()

    def __init__(
        self,
        selection: CrystalSelection | tuple[SelectedCrystal, ...],
        transient_project_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.selection = (
            selection
            if isinstance(selection, CrystalSelection)
            else crystal_selection_from_selected_crystals(transient_project_id, selection)
        )
        self.current_plan = None
        self.current_experiment_id: str | None = None
        self.assigned_experiment_id: str | None = None
        self.mxlive_account: MxLiveAccount | None = None
        self.well_usage: dict[str, tuple[SelectedWellUsage, ...]] = {}
        self._experiment_id_provider: Callable[[str], str] | None = None

        # Header: which project this is and where it stands.
        self.title_label = QLabel(self.PLAN_TYPE_LABEL)
        self.title_label.setObjectName("PrimaryHeading")
        title_font = self.title_label.font()
        title_font.setPointSizeF(title_font.pointSizeF() * 1.25)
        self.title_label.setFont(title_font)
        self.plan_type_label = QLabel(self.PLAN_TYPE_LABEL)
        self.plan_type_label.setObjectName("Muted")
        self.lifecycle_label = QLabel("Draft · not saved")
        self.facts_label = QLabel()
        self.facts_label.setObjectName("Muted")
        self.experiment_id_label = QLabel("Experiment ID: —")
        self.experiment_id_label.setObjectName("Muted")
        self.experiment_id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.adopt_selection_button = QPushButton("Adopt Current Selection…")
        self.adopt_selection_button.hide()

        # Plan settings, collapsible once the plan is settled.
        self.protein_input = QLineEdit()
        self.protein_input.setPlaceholderText("Protein name")
        self.protein_input.setMinimumWidth(160)
        self.order_input = QComboBox()
        self.order_input.addItem("Selection order", AssignmentOrder.SELECTION)
        self.order_input.addItem("Plate / well order", AssignmentOrder.PLATE_WELL)
        self.order_input.setToolTip(
            "Changing the order reassigns plan items; previews update immediately."
        )
        self.settings_toggle = QToolButton()
        self.settings_toggle.setText("Plan settings")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(True)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.settings_toggle.setArrowType(Qt.DownArrow)
        self.settings_panel = QWidget()
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(theme.status_style("error"))

        # Previews.
        self.preview_tabs = QTabWidget()
        self.shifter_table = _preview_table(SHIFTER_HEADER)
        self.webdb_status_label = QLabel("MxLive account: not configured")
        self.webdb_status_label.setObjectName("Muted")
        self.webdb_upload_state = ""
        self.webdb_table = _preview_table(LABWORK_COLUMNS)

        # Delivery bar: finalization and every delivery outside XtalFlow.
        self.readiness_label = QLabel()
        self.worksheet_status_label = QLabel("Worksheets: not saved")
        self.webdb_delivery_label = QLabel("WebDB: not uploaded")
        self.finalize_button = QPushButton("Finalize Plan")
        self.save_worksheets_button = QPushButton("Save Worksheets…")
        self.save_worksheets_button.setEnabled(False)
        self.webdb_upload_button = QPushButton("Upload to MxLive…")
        self.webdb_upload_button.setEnabled(False)

        self.finalize_button.clicked.connect(self.finalize_requested.emit)
        self.save_worksheets_button.clicked.connect(self.save_worksheets_requested.emit)
        self.webdb_upload_button.clicked.connect(self.webdb_upload_requested.emit)
        self.adopt_selection_button.clicked.connect(self.adopt_selection_requested.emit)
        self.protein_input.textChanged.connect(self._refresh_experiment_id)
        self.protein_input.textChanged.connect(self.draft_changed.emit)
        self.settings_toggle.toggled.connect(self._toggle_settings)

    def _assemble(self, settings: QFormLayout, previews: tuple[tuple[QWidget, str], ...]) -> None:
        heading = QHBoxLayout()
        heading.addWidget(self.title_label)
        heading.addStretch()
        heading.addWidget(self.adopt_selection_button)
        status = QHBoxLayout()
        status.setSpacing(theme.SPACING_S)
        status.addWidget(self.plan_type_label)
        separator = QLabel("·")
        separator.setObjectName("Muted")
        status.addWidget(separator)
        status.addWidget(self.lifecycle_label)
        status.addStretch()
        status.addWidget(self.experiment_id_label)
        self.settings_panel.setLayout(settings)

        for widget, title in previews:
            self.preview_tabs.addTab(widget, title)
        webdb_widget = QWidget()
        webdb_layout = QVBoxLayout()
        webdb_layout.addWidget(self.webdb_status_label)
        webdb_layout.addWidget(self.webdb_table, 1)
        webdb_widget.setLayout(webdb_layout)
        self.preview_tabs.addTab(webdb_widget, "WebDB")

        delivery_status = QVBoxLayout()
        delivery_status.setSpacing(2)
        delivery_status.addWidget(self.readiness_label)
        delivery_facts = QHBoxLayout()
        delivery_facts.setSpacing(theme.SPACING_L)
        delivery_facts.addWidget(self.worksheet_status_label)
        delivery_facts.addWidget(self.webdb_delivery_label)
        delivery_facts.addStretch()
        delivery_status.addLayout(delivery_facts)
        delivery = QHBoxLayout()
        delivery.setContentsMargins(theme.SPACING_L, theme.SPACING_M, theme.SPACING_M, theme.SPACING_M)
        delivery.addLayout(delivery_status, 1)
        delivery.addWidget(self.finalize_button)
        delivery.addWidget(self.save_worksheets_button)
        delivery.addWidget(self.webdb_upload_button)
        self.delivery_bar = QFrame()
        self.delivery_bar.setObjectName("DeliveryBar")
        self.delivery_bar.setLayout(delivery)

        layout = QVBoxLayout()
        layout.setContentsMargins(theme.SPACING_M, 0, 0, 0)
        layout.setSpacing(theme.SPACING_M)
        layout.addLayout(heading)
        layout.addLayout(status)
        layout.addWidget(self.facts_label)
        layout.addWidget(self.settings_toggle)
        layout.addWidget(self.settings_panel)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_tabs, 1)
        layout.addWidget(self.delivery_bar)
        self.setLayout(layout)

    def set_title(self, name: str) -> None:
        self.title_label.setText(name)

    def show_delivery(
        self, readiness: tuple[str, str], worksheets: tuple[str, str], webdb: tuple[str, str]
    ) -> None:
        """Show plan confirmation and each external delivery as separate states."""
        for label, (text, kind) in (
            (self.readiness_label, readiness),
            (self.worksheet_status_label, worksheets),
            (self.webdb_delivery_label, webdb),
        ):
            label.setText(text)
            label.setStyleSheet(theme.status_style(kind))

    def _toggle_settings(self, visible: bool) -> None:
        self.settings_panel.setVisible(visible)
        self.settings_toggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

    def _refresh_facts(self) -> None:
        wells = self.selection.wells
        positions = sum(len(well.soaking_positions) for well in wells)
        reused = sum(1 for well in wells if self.well_usage.get(well.image_key))
        facts = (
            f"{len(wells)} selected well{'s' if len(wells) != 1 else ''} · "
            f"{positions} position{'s' if positions != 1 else ''}"
        )
        if reused:
            facts += f" · {reused} reused"
        self.facts_label.setText(facts)

    def set_crystals(self, crystals: tuple[SelectedCrystal, ...]) -> None:
        self.selection = crystal_selection_from_selected_crystals(
            self.selection.project_id, crystals
        )
        self.refresh_plan()

    def set_selection(self, selection: CrystalSelection) -> None:
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
                f"Experiment ID: {self.assigned_experiment_id} · fixed for this plan"
            )
        elif self._experiment_id_provider is None:
            self.current_experiment_id = None
            self.experiment_id_label.setText("Experiment ID: —")
        else:
            try:
                self.current_experiment_id = self._experiment_id_provider(
                    self.protein_input.text()
                )
                self.experiment_id_label.setText(
                    f"Experiment ID: {self.current_experiment_id}"
                )
            except (ValueError, ReviewPersistenceError) as error:
                self.current_experiment_id = None
                self.experiment_id_label.setText(f"Experiment ID: {error}")
        self.save_worksheets_button.setEnabled(
            self.current_plan is not None and self.current_experiment_id is not None
        )
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

    def _show_plan_error(self, error: ValueError) -> None:
        self.current_plan = None
        self.error_label.setText(str(error))
        self.error_label.show()
        self.webdb_table.setRowCount(0)
        self.save_worksheets_button.setEnabled(False)
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

    PLAN_TYPE_LABEL = "Fragment Screening"

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
        self.volume_input.setSuffix(" nL / image")
        self.volume_input.setValue(25.0)
        self.table = _preview_table(
            ("Order", "Plate", "Well", "Positions", "Usage", "Fragment", "Source", "Total"),
            stretch_last=True,
        )
        self.echo_table = _preview_table(ECHO_HEADER)

        library_row = QHBoxLayout()
        library_row.addWidget(self.library_input, 1)
        library_row.addWidget(self.refresh_libraries_button)
        amounts_row = QHBoxLayout()
        amounts_row.addWidget(self.rows_input, 1)
        amounts_row.addWidget(QLabel("Volume"))
        amounts_row.addWidget(self.volume_input)
        settings = QFormLayout()
        settings.addRow("Protein", self.protein_input)
        settings.addRow("Library", library_row)
        settings.addRow("", self.library_label)
        settings.addRow("CSV rows", amounts_row)
        settings.addRow("Assignment", self.order_input)
        self._assemble(
            settings,
            (
                (self.table, "Summary"),
                (self.echo_table, "ECHO Worksheet"),
                (self.shifter_table, "SHIFTER Worksheet"),
            ),
        )

        self.rows_input.textChanged.connect(self.refresh_plan)
        self.volume_input.valueChanged.connect(self.refresh_plan)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
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
        self.refresh_plan()

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
                raise ValueError("select or import a fragment library")
            selected_library = self.library.select_rows(self.rows_input.text())
            plan = build_fragment_screen_plan(
                selected_library,
                self.selection,
                Decimal(str(self.volume_input.value())),
                self.order_input.currentData(),
            )
        except ValueError as error:
            self.table.setRowCount(0)
            self.echo_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self._show_plan_error(error)
            return
        self.current_plan = plan
        self.error_label.setText("")
        self.error_label.hide()
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
        self.protein_input.setText(draft.protein)
        self.assigned_experiment_id = draft.experiment_id
        self.volume_input.setValue(float(draft.volume_nl))
        order_index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, order_index))
        for widget in widgets:
            widget.blockSignals(False)
        self.refresh_plan()


class RawCrystalEditor(PlanEditorBase):
    """Harvest selected crystals with SHIFTER, without a soaking step."""

    PLAN_TYPE_LABEL = "Raw Crystal"

    def __init__(
        self,
        selection: CrystalSelection | tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(selection, "transient-raw-editor", parent)
        self.save_worksheets_button.setText("Save SHIFTER Worksheets…")
        self.summary_table = _preview_table(
            ("Order", "Plate", "Well", "Soaking Position", "Usage", "Selected at"),
            stretch_last=True,
        )
        settings = QFormLayout()
        settings.addRow("Protein", self.protein_input)
        settings.addRow("Assignment", self.order_input)
        self._assemble(
            settings,
            ((self.summary_table, "Summary"), (self.shifter_table, "SHIFTER Worksheet")),
        )
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self.refresh_plan()

    def _record_builder(self) -> Callable:
        return build_raw_crystal_labworks

    def restore_draft(self, draft: PlanningDraft) -> None:
        self.protein_input.blockSignals(True)
        self.order_input.blockSignals(True)
        self.protein_input.setText(draft.protein)
        self.assigned_experiment_id = draft.experiment_id
        index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, index))
        self.protein_input.blockSignals(False)
        self.order_input.blockSignals(False)
        self.refresh_plan()

    def refresh_plan(self) -> None:
        try:
            plan = build_raw_crystal_plan(self.selection, self.order_input.currentData())
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


class FragmentScreeningDialog(QDialog):
    """Compatibility wrapper around the embedded planning editor."""

    def __init__(
        self,
        library: FragmentLibrary,
        crystals: tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fragment Screening Plan")
        self.resize(850, 520)
        self.editor = FragmentScreeningEditor(library, crystals, self)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons)
        self.setLayout(layout)
        for name in (
            "library_label",
            "rows_input",
            "volume_input",
            "order_input",
            "error_label",
            "table",
        ):
            setattr(self, name, getattr(self.editor, name))

    @property
    def current_plan(self):
        return self.editor.current_plan
