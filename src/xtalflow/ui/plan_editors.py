"""Planning editors that preview worksheets and MxLive labworks for a plan."""

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
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.raw_crystal import RawCrystalPlan, build_raw_crystal_plan
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


class FragmentScreeningEditor(QWidget):
    """Embedded fragment-to-crystal assignment editor."""

    library_refresh_requested = pyqtSignal()
    save_worksheets_requested = pyqtSignal()
    finalize_requested = pyqtSignal()
    draft_changed = pyqtSignal()
    webdb_upload_requested = pyqtSignal()
    adopt_selection_requested = pyqtSignal()

    def __init__(
        self,
        library: FragmentLibrary | None,
        selection: CrystalSelection | tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        initial_library = library
        self.library: FragmentLibrary | None = None
        self.library_id: str | None = None
        self.selection = (
            selection
            if isinstance(selection, CrystalSelection)
            else crystal_selection_from_selected_crystals(
                "transient-fragment-editor", selection
            )
        )
        self.current_plan = None
        self.current_experiment_id: str | None = None
        self.assigned_experiment_id: str | None = None
        self.mxlive_account: MxLiveAccount | None = None
        self.library_input = QComboBox()
        self.library_input.setMinimumWidth(260)
        self.refresh_libraries_button = QPushButton("Refresh Libraries")
        self.library_label = QLabel("No library imported")
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
        self.order_input = QComboBox()
        self.order_input.addItem("Selection order", AssignmentOrder.SELECTION)
        self.order_input.addItem("Plate / well order", AssignmentOrder.PLATE_WELL)
        self.protein_input = QLineEdit()
        self.protein_input.setPlaceholderText("Protein name")
        self.experiment_id_label = QLabel("Experiment ID: —")
        self._experiment_id_provider: Callable[[str], str] | None = None
        self.save_worksheets_button = QPushButton("Save Worksheets…")
        self.finalize_button = QPushButton("Finalize Plan")
        self.adopt_selection_button = QPushButton("Adopt Current Selection…")
        self.adopt_selection_button.hide()
        self.lifecycle_label = QLabel("Draft · not saved")
        self.save_worksheets_button.setEnabled(False)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #b00020")
        self.well_usage: dict[str, tuple[SelectedWellUsage, ...]] = {}
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            (
                "Order", "Plate", "Selected Well", "Soaking Positions",
                "Usage", "Fragment", "Source", "Total",
            )
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.echo_table = QTableWidget(0, len(ECHO_HEADER))
        self.echo_table.setHorizontalHeaderLabels(ECHO_HEADER)
        self.shifter_table = QTableWidget(0, len(SHIFTER_HEADER))
        self.shifter_table.setHorizontalHeaderLabels(SHIFTER_HEADER)
        for preview_table in (self.echo_table, self.shifter_table):
            preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            preview_table.verticalHeader().setVisible(False)
            preview_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeToContents
            )
        self.preview_tabs = QTabWidget()
        self.preview_tabs.addTab(self.table, "Summary")
        self.preview_tabs.addTab(self.echo_table, "ECHO Worksheet")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER Worksheet")
        self.webdb_status_label = QLabel("MxLive account: not configured")
        self.webdb_upload_state = ""
        self.webdb_table = QTableWidget(0, len(LABWORK_COLUMNS))
        self.webdb_table.setHorizontalHeaderLabels(LABWORK_COLUMNS)
        self.webdb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.webdb_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.webdb_table.verticalHeader().setVisible(False)
        self.webdb_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.webdb_upload_button = QPushButton("Upload Finalized Revision…")
        self.webdb_upload_button.setEnabled(False)
        webdb_widget = QWidget()
        webdb_layout = QVBoxLayout()
        webdb_layout.addWidget(self.webdb_status_label)
        webdb_layout.addWidget(self.webdb_table, 1)
        webdb_layout.addWidget(self.webdb_upload_button)
        webdb_widget.setLayout(webdb_layout)
        self.preview_tabs.addTab(webdb_widget, "WebDB")

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Library rows:"))
        controls.addWidget(self.rows_input, 1)
        controls.addWidget(QLabel("Vol/Well:"))
        controls.addWidget(self.volume_input)
        controls.addWidget(QLabel("Assign:"))
        controls.addWidget(self.order_input)
        experiment_controls = QHBoxLayout()
        experiment_controls.addWidget(QLabel("Protein:"))
        experiment_controls.addWidget(self.protein_input)
        experiment_controls.addWidget(self.experiment_id_label, 1)
        experiment_controls.addWidget(self.lifecycle_label)
        experiment_controls.addWidget(self.adopt_selection_button)
        experiment_controls.addWidget(self.finalize_button)
        experiment_controls.addWidget(self.save_worksheets_button)
        library_controls = QHBoxLayout()
        library_controls.addWidget(QLabel("Library:"))
        library_controls.addWidget(self.library_input, 1)
        library_controls.addWidget(self.refresh_libraries_button)
        layout = QVBoxLayout()
        layout.addLayout(library_controls)
        layout.addWidget(self.library_label)
        layout.addLayout(experiment_controls)
        layout.addLayout(controls)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_tabs, 1)
        self.setLayout(layout)

        self.rows_input.textChanged.connect(self.refresh_plan)
        self.volume_input.valueChanged.connect(self.refresh_plan)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self.library_input.currentIndexChanged.connect(self._library_changed)
        self.refresh_libraries_button.clicked.connect(
            self.library_refresh_requested.emit
        )
        self.protein_input.textChanged.connect(self._refresh_experiment_id)
        self.protein_input.textChanged.connect(self.draft_changed.emit)
        self.save_worksheets_button.clicked.connect(
            self.save_worksheets_requested.emit
        )
        self.finalize_button.clicked.connect(self.finalize_requested.emit)
        self.webdb_upload_button.clicked.connect(self.webdb_upload_requested.emit)
        self.adopt_selection_button.clicked.connect(
            self.adopt_selection_requested.emit
        )
        if initial_library is not None:
            self.set_library_choices(
                ((
                    "direct",
                    f"{initial_library.name} · {len(initial_library.fragments)} rows",
                    initial_library,
                ),),
                "direct",
            )
        self.refresh_plan()

    def set_crystals(self, crystals: tuple[SelectedCrystal, ...]) -> None:
        self.selection = crystal_selection_from_selected_crystals(
            self.selection.project_id, crystals
        )
        self.refresh_plan()

    def set_selection(self, selection: CrystalSelection) -> None:
        self.selection = selection
        self.refresh_plan()

    def set_well_usage(
        self, usage: dict[str, tuple[SelectedWellUsage, ...]]
    ) -> None:
        self.well_usage = usage
        previous = self.blockSignals(True)
        self.refresh_plan()
        self.blockSignals(previous)

    def set_mxlive_account(self, account: MxLiveAccount) -> None:
        self.mxlive_account = account
        self.refresh_plan()

    def set_experiment_id_provider(
        self, provider: Callable[[str], str]
    ) -> None:
        self._experiment_id_provider = provider
        self._refresh_experiment_id()

    def _refresh_experiment_id(self) -> None:
        if self.assigned_experiment_id:
            self.current_experiment_id = self.assigned_experiment_id
            self.experiment_id_label.setText(
                f"Experiment ID: {self.assigned_experiment_id} · fixed for this plan"
            )
            self.save_worksheets_button.setEnabled(self.current_plan is not None)
            self._refresh_webdb_preview()
            return
        if self._experiment_id_provider is None:
            self.current_experiment_id = None
            self.experiment_id_label.setText("Experiment ID: —")
            self.save_worksheets_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        try:
            experiment_id = self._experiment_id_provider(self.protein_input.text())
        except (ValueError, ReviewPersistenceError) as error:
            self.current_experiment_id = None
            self.experiment_id_label.setText(f"Experiment ID: {error}")
            self.save_worksheets_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        self.current_experiment_id = experiment_id
        self.experiment_id_label.setText(f"Experiment ID: {experiment_id}")
        self.save_worksheets_button.setEnabled(self.current_plan is not None)
        self._refresh_webdb_preview()

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
            self.current_plan = None
            self.error_label.setText(str(error))
            self.table.setRowCount(0)
            self.echo_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self.webdb_table.setRowCount(0)
            self.save_worksheets_button.setEnabled(False)
            self._refresh_webdb_preview()
            self.draft_changed.emit()
            return
        self.current_plan = plan
        self.error_label.setText("")
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
                        item.setForeground(QColor("#ef6c00"))
                self.table.setItem(row, column, item)
        self._set_preview_rows(
            self.echo_table,
            tuple(row.values() for row in build_echo_worksheet(plan)),
        )
        self._set_preview_rows(
            self.shifter_table,
            tuple(row.values() for row in build_shifter_worksheet(plan)),
        )
        self._refresh_experiment_id()
        self._refresh_webdb_preview()
        self.draft_changed.emit()

    def _refresh_webdb_preview(self) -> None:
        _refresh_labwork_preview(
            self.webdb_table,
            self.webdb_status_label,
            self.current_plan,
            self.current_experiment_id,
            self.protein_input.text(),
            self.mxlive_account,
            build_fragment_labworks,
        )
        if self.webdb_upload_state:
            self.webdb_status_label.setText(
                f"{self.webdb_status_label.text()} · {self.webdb_upload_state}"
            )

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

    @staticmethod
    def _set_preview_rows(
        table: QTableWidget, rows: tuple[tuple[str, ...], ...]
    ) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))


class RawCrystalEditor(QWidget):
    """Plan selected crystals for SHIFTER without a soaking step."""

    save_worksheet_requested = pyqtSignal()
    finalize_requested = pyqtSignal()
    draft_changed = pyqtSignal()
    webdb_upload_requested = pyqtSignal()
    adopt_selection_requested = pyqtSignal()

    def __init__(
        self,
        selection: CrystalSelection | tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.selection = (
            selection
            if isinstance(selection, CrystalSelection)
            else crystal_selection_from_selected_crystals(
                "transient-raw-editor", selection
            )
        )
        self.current_plan: RawCrystalPlan | None = None
        self.current_experiment_id: str | None = None
        self.assigned_experiment_id: str | None = None
        self.mxlive_account: MxLiveAccount | None = None
        self.protein_input = QLineEdit()
        self.protein_input.setPlaceholderText("Protein name")
        self.order_input = QComboBox()
        self.order_input.addItem("Selection order", AssignmentOrder.SELECTION)
        self.order_input.addItem("Plate / well order", AssignmentOrder.PLATE_WELL)
        self.experiment_id_label = QLabel("Experiment ID: —")
        self.lifecycle_label = QLabel("Draft · not saved")
        self.finalize_button = QPushButton("Finalize Plan")
        self.adopt_selection_button = QPushButton("Adopt Current Selection…")
        self.adopt_selection_button.hide()
        self.save_worksheet_button = QPushButton("Save SHIFTER Worksheet…")
        self.save_worksheet_button.setEnabled(False)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #b00020")
        self.well_usage: dict[str, tuple[SelectedWellUsage, ...]] = {}
        self.summary_table = QTableWidget(0, 6)
        self.summary_table.setHorizontalHeaderLabels(
            (
                "Order", "Plate", "Selected Well", "Soaking Position",
                "Usage", "Selected at",
            )
        )
        self.shifter_table = QTableWidget(0, len(SHIFTER_HEADER))
        self.shifter_table.setHorizontalHeaderLabels(SHIFTER_HEADER)
        for table in (self.summary_table, self.shifter_table):
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
        self.preview_tabs = QTabWidget()
        self.preview_tabs.addTab(self.summary_table, "Summary")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER Worksheet")
        self.webdb_status_label = QLabel("MxLive account: not configured")
        self.webdb_upload_state = ""
        self.webdb_table = QTableWidget(0, len(LABWORK_COLUMNS))
        self.webdb_table.setHorizontalHeaderLabels(LABWORK_COLUMNS)
        self.webdb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.webdb_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.webdb_table.verticalHeader().setVisible(False)
        self.webdb_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.webdb_upload_button = QPushButton("Upload Finalized Revision…")
        self.webdb_upload_button.setEnabled(False)
        webdb_widget = QWidget()
        webdb_layout = QVBoxLayout()
        webdb_layout.addWidget(self.webdb_status_label)
        webdb_layout.addWidget(self.webdb_table, 1)
        webdb_layout.addWidget(self.webdb_upload_button)
        webdb_widget.setLayout(webdb_layout)
        self.preview_tabs.addTab(webdb_widget, "WebDB")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Protein:"))
        controls.addWidget(self.protein_input)
        controls.addWidget(QLabel("Order:"))
        controls.addWidget(self.order_input)
        controls.addWidget(self.experiment_id_label, 1)
        controls.addWidget(self.lifecycle_label)
        controls.addWidget(self.adopt_selection_button)
        controls.addWidget(self.finalize_button)
        controls.addWidget(self.save_worksheet_button)
        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_tabs, 1)
        self.setLayout(layout)
        self._experiment_id_provider: Callable[[str], str] | None = None
        self.protein_input.textChanged.connect(self._refresh_experiment_id)
        self.protein_input.textChanged.connect(self.draft_changed.emit)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self.finalize_button.clicked.connect(self.finalize_requested.emit)
        self.save_worksheet_button.clicked.connect(self.save_worksheet_requested.emit)
        self.webdb_upload_button.clicked.connect(self.webdb_upload_requested.emit)
        self.adopt_selection_button.clicked.connect(
            self.adopt_selection_requested.emit
        )
        self.refresh_plan()

    def set_experiment_id_provider(self, provider: Callable[[str], str]) -> None:
        self._experiment_id_provider = provider
        self._refresh_experiment_id()

    def set_crystals(self, crystals: tuple[SelectedCrystal, ...]) -> None:
        self.selection = crystal_selection_from_selected_crystals(
            self.selection.project_id, crystals
        )
        self.refresh_plan()

    def set_selection(self, selection: CrystalSelection) -> None:
        self.selection = selection
        self.refresh_plan()

    def set_well_usage(
        self, usage: dict[str, tuple[SelectedWellUsage, ...]]
    ) -> None:
        self.well_usage = usage
        previous = self.blockSignals(True)
        self.refresh_plan()
        self.blockSignals(previous)

    def set_mxlive_account(self, account: MxLiveAccount) -> None:
        self.mxlive_account = account
        self.refresh_plan()

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

    def _refresh_experiment_id(self) -> None:
        if self.assigned_experiment_id:
            self.current_experiment_id = self.assigned_experiment_id
            self.experiment_id_label.setText(
                f"Experiment ID: {self.assigned_experiment_id} · fixed for this plan"
            )
            self.save_worksheet_button.setEnabled(self.current_plan is not None)
            self._refresh_webdb_preview()
            return
        if self._experiment_id_provider is None:
            self.current_experiment_id = None
            self.experiment_id_label.setText("Experiment ID: —")
            self.save_worksheet_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        try:
            experiment_id = self._experiment_id_provider(self.protein_input.text())
        except (ValueError, ReviewPersistenceError) as error:
            self.current_experiment_id = None
            self.experiment_id_label.setText(f"Experiment ID: {error}")
            self.save_worksheet_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        self.current_experiment_id = experiment_id
        self.experiment_id_label.setText(f"Experiment ID: {experiment_id}")
        self.save_worksheet_button.setEnabled(self.current_plan is not None)
        self._refresh_webdb_preview()

    def refresh_plan(self) -> None:
        try:
            plan = build_raw_crystal_plan(
                self.selection, self.order_input.currentData()
            )
            rows = build_shifter_worksheet(plan)
        except ValueError as error:
            self.current_plan = None
            self.error_label.setText(str(error))
            self.summary_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self.webdb_table.setRowCount(0)
            self.save_worksheet_button.setEnabled(False)
            self._refresh_webdb_preview()
            self.draft_changed.emit()
            return
        self.current_plan = plan
        self.error_label.setText("")
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
                        item.setForeground(QColor("#ef6c00"))
                self.summary_table.setItem(row, column, item)
        FragmentScreeningEditor._set_preview_rows(
            self.shifter_table, tuple(row.values() for row in rows)
        )
        self._refresh_experiment_id()
        self._refresh_webdb_preview()
        self.draft_changed.emit()

    def _refresh_webdb_preview(self) -> None:
        _refresh_labwork_preview(
            self.webdb_table,
            self.webdb_status_label,
            self.current_plan,
            self.current_experiment_id,
            self.protein_input.text(),
            self.mxlive_account,
            build_raw_crystal_labworks,
        )
        if self.webdb_upload_state:
            self.webdb_status_label.setText(
                f"{self.webdb_status_label.text()} · {self.webdb_upload_state}"
            )


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
