"""Choose the RockMaker batch and profile to load for a plate."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from xtalflow.infrastructure import (
    RockMakerImageRepository,
    latest_image_source,
    natural_name_key,
)


class PlateSourceDialog(QDialog):
    """Choose one RockMaker batch/profile pair without serial pop-up dialogs."""

    def __init__(
        self,
        repository: RockMakerImageRepository,
        plate_code: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.plate_code = plate_code
        self.setWindowTitle(f"Load plate {plate_code}")
        self.batch_input = QComboBox()
        self.profile_input = QComboBox()
        self.load_latest_for_all = QCheckBox("Load latest for all plates")
        self.load_latest_for_all.setToolTip(
            "Use the latest imaged batch and profile for this and all remaining "
            "plate codes without showing another selection window."
        )
        latest_batch, _ = latest_image_source(repository, plate_code)
        for batch_id in reversed(repository.available_batches(plate_code)):
            self.batch_input.addItem(str(batch_id), batch_id)
        self._latest_index = self.batch_input.findData(latest_batch)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.batch_input.setCurrentIndex(self._latest_index)
        self.batch_input.currentIndexChanged.connect(self._refresh_profiles)
        self.load_latest_for_all.toggled.connect(self._latest_mode_changed)
        self._refresh_profiles()
        self.load_latest_for_all.setChecked(True)

        batch_row = QHBoxLayout()
        batch_row.addWidget(QLabel("Batch:"))
        batch_row.addWidget(self.batch_input, 1)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        profile_row.addWidget(self.profile_input, 1)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Plate {plate_code}"))
        layout.addLayout(batch_row)
        layout.addLayout(profile_row)
        layout.addWidget(self.load_latest_for_all)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    @property
    def batch_id(self) -> int:
        return int(self.batch_input.currentData())

    @property
    def profile(self) -> str:
        return str(self.profile_input.currentData())

    def _refresh_profiles(self) -> None:
        batch_id = self.batch_input.currentData()
        self.profile_input.clear()
        profiles = (
            self.repository.available_profiles(self.plate_code, int(batch_id))
            if batch_id is not None
            else ()
        )
        if not profiles:
            # Scheduled inspections exist as folders before images are written.
            self.profile_input.addItem("No images yet", None)
        for profile in sorted(profiles, key=natural_name_key, reverse=True):
            self.profile_input.addItem(profile, profile)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(profiles))

    def _latest_mode_changed(self, enabled: bool) -> None:
        if enabled:
            self.batch_input.setCurrentIndex(self._latest_index)
            self.profile_input.setCurrentIndex(0)
        self.batch_input.setEnabled(not enabled)
        self.profile_input.setEnabled(not enabled)
