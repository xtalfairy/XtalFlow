"""Choose plate type, plate codes, and the RockMaker batch/profile to load."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from xtalflow.domain import PlateFormat
from xtalflow.infrastructure import (
    PlateImagesNotFoundError,
    latest_image_source,
    natural_name_key,
)
from xtalflow.ui import theme


@dataclass(frozen=True)
class PlateSource:
    plate_code: str
    batch_id: int
    profile: str


def parse_plate_codes(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code.strip() for code in text.split(",") if code.strip()))


class LoadPlatesDialog(QDialog):
    """One dialog for plate type, codes, and optional per-plate batch/profile."""

    def __init__(
        self,
        repository,
        plate_formats: tuple[PlateFormat, ...],
        default_format: PlateFormat | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("Load Plates")
        self.setMinimumWidth(460)
        self.plate_format_input = QComboBox()
        for plate_format in plate_formats:
            self.plate_format_input.addItem(plate_format.display_name, plate_format)
        if default_format is not None:
            self.plate_format_input.setCurrentIndex(
                max(0, self.plate_format_input.findData(default_format))
            )
        self.plate_codes_input = QLineEdit()
        self.plate_codes_input.setPlaceholderText("e.g. 2069, 2070")
        self.use_latest_checkbox = QCheckBox("Use latest batch and profile for all")
        self.use_latest_checkbox.setChecked(True)
        self.sources_table = QTableWidget(0, 3)
        self.sources_table.setHorizontalHeaderLabels(("Plate", "Batch", "Profile"))
        self.sources_table.verticalHeader().setVisible(False)
        self.sources_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sources_table.hide()
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(theme.status_style("error"))
        self.error_label.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.load_button = self.buttons.button(QDialogButtonBox.Ok)
        self.load_button.setText("Load")
        self.load_button.setObjectName("Primary")
        self.load_button.setEnabled(False)

        form = QFormLayout()
        form.addRow("Plate type", self.plate_format_input)
        form.addRow("Plate codes", self.plate_codes_input)
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.use_latest_checkbox)
        layout.addWidget(self.sources_table)
        layout.addWidget(self.error_label)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self.plate_codes_input.textChanged.connect(self._refresh)
        self.use_latest_checkbox.toggled.connect(self._refresh)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)

    @property
    def plate_format(self) -> PlateFormat:
        return self.plate_format_input.currentData()

    def plate_sources(self) -> tuple[PlateSource, ...]:
        """Sources to load; raises PlateImagesNotFoundError for unimaged plates."""
        codes = parse_plate_codes(self.plate_codes_input.text())
        if self.use_latest_checkbox.isChecked():
            return tuple(
                PlateSource(code, *latest_image_source(self.repository, code))
                for code in codes
            )
        sources = []
        for row in range(self.sources_table.rowCount()):
            batch = self.sources_table.cellWidget(row, 1).currentData()
            profile = self.sources_table.cellWidget(row, 2).currentData()
            if batch is None or profile is None:
                code = self.sources_table.item(row, 0).text()
                raise PlateImagesNotFoundError(f"plate {code} has no images in that batch")
            sources.append(PlateSource(self.sources_table.item(row, 0).text(), batch, profile))
        return tuple(sources)

    def _refresh(self) -> None:
        codes = parse_plate_codes(self.plate_codes_input.text())
        manual = not self.use_latest_checkbox.isChecked()
        self.sources_table.setVisible(manual and bool(codes))
        self._set_error("")
        if manual:
            self._rebuild_sources_table(codes)
        self._update_load_button()

    def _rebuild_sources_table(self, codes: tuple[str, ...]) -> None:
        from PyQt5.QtWidgets import QTableWidgetItem

        self.sources_table.setRowCount(0)
        for code in codes:
            row = self.sources_table.rowCount()
            self.sources_table.insertRow(row)
            self.sources_table.setItem(row, 0, QTableWidgetItem(code))
            batch_input = QComboBox()
            profile_input = QComboBox()
            try:
                batches = self.repository.available_batches(code)
                latest_batch, _ = latest_image_source(self.repository, code)
            except (OSError, ValueError) as error:
                self._set_error(str(error))
                batches, latest_batch = (), None
            for batch_id in reversed(batches):
                batch_input.addItem(str(batch_id), batch_id)
            if latest_batch is not None:
                batch_input.setCurrentIndex(batch_input.findData(latest_batch))
            batch_input.currentIndexChanged.connect(
                lambda _, plate=code, batches=batch_input, profiles=profile_input:
                self._refresh_profiles(plate, batches, profiles)
            )
            self.sources_table.setCellWidget(row, 1, batch_input)
            self.sources_table.setCellWidget(row, 2, profile_input)
            self._refresh_profiles(code, batch_input, profile_input)

    def _refresh_profiles(
        self, plate_code: str, batch_input: QComboBox, profile_input: QComboBox
    ) -> None:
        profile_input.clear()
        batch_id = batch_input.currentData()
        try:
            profiles = (
                self.repository.available_profiles(plate_code, batch_id)
                if batch_id is not None else ()
            )
        except (OSError, ValueError):
            profiles = ()
        if not profiles:
            # Scheduled inspections exist as folders before images are written.
            profile_input.addItem("No images yet", None)
        for profile in sorted(profiles, key=natural_name_key, reverse=True):
            profile_input.addItem(profile, profile)
        self._update_load_button()

    def _update_load_button(self) -> None:
        codes = parse_plate_codes(self.plate_codes_input.text())
        ready = bool(codes)
        if ready and not self.use_latest_checkbox.isChecked():
            ready = all(
                self.sources_table.cellWidget(row, 2) is not None
                and self.sources_table.cellWidget(row, 2).currentData() is not None
                for row in range(self.sources_table.rowCount())
            )
        self.load_button.setEnabled(ready)

    def _accept_if_valid(self) -> None:
        try:
            self.plate_sources()
        except (OSError, ValueError) as error:
            self._set_error(str(error))
            return
        self.accept()

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
