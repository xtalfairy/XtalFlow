from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite, sqrt


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


# How far beyond the fitted outline a position still counts as inside the
# well, as a fraction of the radius. The outline is fitted to an image, and
# that fit is not accurate to a fraction of a percent; crystals are also
# picked at the very edge of the drop as a matter of course. Without this the
# warning would fire on ordinary work - a tenth the width of the drop an ECHO
# dispenses - and a warning that fires on ordinary work stops being read.
OUTLINE_TOLERANCE = 0.02


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

    def normalized_distance(self, x_px: float, y_px: float) -> float:
        """How far a point lies from the centre, as a multiple of the radius."""
        return sqrt(
            ((x_px - self.center_x_px) / self.radius_x_px) ** 2
            + ((y_px - self.center_y_px) / self.radius_y_px) ** 2
        )

    def contains(
        self, x_px: float, y_px: float, tolerance: float = OUTLINE_TOLERANCE
    ) -> bool:
        """Whether a point is inside the well, as closely as the outline is known.

        Pass ``tolerance=0`` for the fitted outline exactly.
        """
        return self.normalized_distance(x_px, y_px) <= 1 + tolerance

    def distance_beyond_mm(self, x_px: float, y_px: float) -> float:
        """How far past the outline a point sits, in millimetres.

        In millimetres the outline is a circle of half the lens diameter,
        because each axis is scaled by its own radius, so the normalised
        distance is the millimetre distance over that half-diameter.
        """
        beyond = self.normalized_distance(x_px, y_px) - 1
        return max(0.0, beyond * self.physical_diameter_mm / 2)

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
