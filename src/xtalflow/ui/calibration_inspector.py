"""Well calibration details, opened from the one-line calibration status."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.domain import CalibrationMethod, ImageCalibration
from xtalflow.ui import theme


def calibration_status(
    calibration: ImageCalibration | None, unavailable_reason: str = ""
) -> tuple[str, str]:
    """Short status text and its kind ("ok", "attention", "error")."""
    if calibration is None:
        reason = f" · {unavailable_reason}" if unavailable_reason else ""
        return f"{theme.SYMBOL_ERROR} Well boundary unavailable{reason}", "error"
    if calibration.method is CalibrationMethod.MANUAL_THREE_POINT:
        method = "Manual"
    else:
        method = f"Auto {calibration.confidence:.0%}"
    if calibration.confirmed:
        return f"{theme.SYMBOL_OK} Well aligned · {method}", "ok"
    return f"{theme.SYMBOL_ATTENTION} Check well boundary · {method}", "attention"


class CalibrationInspector(QFrame):
    """Detection details, recalibration actions, and automatic acceptance."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self.setObjectName("CalibrationInspector")
        self.setFrameShape(QFrame.StyledPanel)
        self.method_label = QLabel("—")
        self.score_label = QLabel("—")
        self.diameter_label = QLabel("—")
        self.scale_label = QLabel("—")
        self.detect_button = QPushButton("Detect again")
        self.manual_button = QPushButton("Set 3 points")
        self.accept_button = QPushButton("Accept boundary")
        self.accept_button.setObjectName("Primary")
        self.accept_button.setEnabled(False)
        self.auto_accept_checkbox = QCheckBox("Enabled for this plate")
        self.auto_accept_checkbox.setToolTip(
            "Automatically accept detected wells on this plate when the detection "
            "score meets the minimum. Plate trust lasts for this session."
        )
        self.minimum_score_input = QSpinBox()
        self.minimum_score_input.setRange(0, 100)
        self.minimum_score_input.setSuffix("%")
        self.minimum_score_input.setPrefix("≥ ")
        # Lowering the minimum accepts calibrations permanently, so apply only the
        # finished value rather than intermediate digits such as 8 of 85.
        self.minimum_score_input.setKeyboardTracking(False)
        self.details_toggle = QToolButton()
        self.details_toggle.setText("Advanced details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.details_toggle.setArrowType(Qt.RightArrow)
        self.details_label = QLabel()
        self.details_label.setObjectName("Muted")
        self.details_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_label.hide()

        title = QLabel("Well calibration")
        title.setObjectName("PrimaryHeading")
        facts = QFormLayout()
        facts.addRow("Method", self.method_label)
        facts.addRow("Detection score", self.score_label)
        facts.addRow("Diameter", self.diameter_label)
        facts.addRow("Scale", self.scale_label)
        actions = QHBoxLayout()
        actions.addWidget(self.detect_button)
        actions.addWidget(self.manual_button)
        automatic = QGroupBox("Automatic acceptance")
        automatic_layout = QHBoxLayout()
        automatic_layout.addWidget(self.auto_accept_checkbox)
        automatic_layout.addStretch()
        automatic_layout.addWidget(QLabel("Minimum score"))
        automatic_layout.addWidget(self.minimum_score_input)
        automatic.setLayout(automatic_layout)
        layout = QVBoxLayout()
        layout.setSpacing(theme.SPACING_M)
        layout.addWidget(title)
        layout.addLayout(facts)
        layout.addLayout(actions)
        layout.addWidget(self.accept_button)
        layout.addWidget(automatic)
        layout.addWidget(self.details_toggle)
        layout.addWidget(self.details_label)
        self.setLayout(layout)
        self.details_toggle.toggled.connect(self._toggle_details)

    def show_calibration(
        self, calibration: ImageCalibration | None, lens_diameter_mm: float | None
    ) -> None:
        if calibration is None:
            self.method_label.setText("Not available")
            self.score_label.setText("—")
            self.diameter_label.setText(
                f"{lens_diameter_mm:.2f} mm" if lens_diameter_mm else "—"
            )
            self.scale_label.setText("—")
            self.details_label.setText("")
            self.accept_button.setEnabled(False)
            return
        manual = calibration.method is CalibrationMethod.MANUAL_THREE_POINT
        state = "accepted" if calibration.confirmed else "not accepted"
        self.method_label.setText(f"{'Manual 3 points' if manual else 'Automatic'} · {state}")
        self.score_label.setText("—" if manual else f"{calibration.confidence:.0%}")
        self.diameter_label.setText(f"{calibration.physical_diameter_mm:.2f} mm")
        scale_um = calibration.physical_diameter_mm * 1000 / (
            calibration.radius_x_px + calibration.radius_y_px
        )
        self.scale_label.setText(f"{scale_um:.3f} µm/px")
        self.details_label.setText(
            f"Centre ({calibration.center_x_px:.1f}, {calibration.center_y_px:.1f}) px\n"
            f"Radius {calibration.radius_x_px:.1f} × {calibration.radius_y_px:.1f} px\n"
            f"Updated {calibration.updated_at:%Y-%m-%d %H:%M}"
        )
        self.accept_button.setEnabled(not calibration.confirmed)

    def open_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        position = anchor.mapToGlobal(anchor.rect().topRight())
        self.move(position.x() - self.width(), position.y() - self.height() - 4)
        self.show()
        self.raise_()

    def _toggle_details(self, visible: bool) -> None:
        self.details_label.setVisible(visible)
        self.details_toggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
        self.adjustSize()
