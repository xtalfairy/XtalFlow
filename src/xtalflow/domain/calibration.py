from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite


class CalibrationMethod(str, Enum):
    AUTO_CIRCLE = "auto_circle"
    MANUAL_THREE_POINT = "manual_three_point"


@dataclass(frozen=True)
class WellBoundary:
    center_x_px: float
    center_y_px: float
    radius_x_px: float
    radius_y_px: float
    confidence: float

    def __post_init__(self) -> None:
        values = (
            self.center_x_px,
            self.center_y_px,
            self.radius_x_px,
            self.radius_y_px,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("well boundary values must be finite")
        if self.radius_x_px <= 0 or self.radius_y_px <= 0:
            raise ValueError("well boundary radii must be positive")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True)
class ImageCalibration:
    image_key: str
    center_x_px: float
    center_y_px: float
    radius_x_px: float
    radius_y_px: float
    physical_diameter_mm: float
    method: CalibrationMethod
    confidence: float
    confirmed: bool
    updated_at: datetime

    def __post_init__(self) -> None:
        values = (
            self.center_x_px,
            self.center_y_px,
            self.radius_x_px,
            self.radius_y_px,
            self.physical_diameter_mm,
            self.confidence,
        )
        if not self.image_key or not all(isfinite(value) for value in values):
            raise ValueError("calibration values must be finite and identified")
        if self.radius_x_px <= 0 or self.radius_y_px <= 0:
            raise ValueError("calibration radii must be positive")
        if self.physical_diameter_mm <= 0:
            raise ValueError("physical diameter must be positive")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between zero and one")

    @classmethod
    def automatic(
        cls,
        image_key: str,
        center_x_px: float,
        center_y_px: float,
        radius_px: float,
        confidence: float,
        physical_diameter_mm: float,
    ) -> ImageCalibration:
        return cls(
            image_key,
            center_x_px,
            center_y_px,
            radius_px,
            radius_px,
            physical_diameter_mm,
            CalibrationMethod.AUTO_CIRCLE,
            confidence,
            False,
            datetime.now(timezone.utc),
        )

    def pixel_to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Convert image pixels to legacy-compatible well coordinates (downward-positive Y)."""
        x_mm = (x_px - self.center_x_px) * self.physical_diameter_mm / (
            2 * self.radius_x_px
        )
        y_mm = (y_px - self.center_y_px) * self.physical_diameter_mm / (
            2 * self.radius_y_px
        )
        return x_mm, y_mm
