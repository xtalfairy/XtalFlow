from __future__ import annotations

from datetime import UTC, datetime
from math import hypot
from typing import Protocol

from xtalflow.domain import (
    CalibrationMethod,
    CrystalImage,
    ImageCalibration,
    WellBoundary,
)


class CalibrationDetectionError(RuntimeError):
    """A trustworthy well boundary could not be detected."""


class WellDetectorPort(Protocol):
    def detect(self, image: CrystalImage) -> WellBoundary: ...


class CalibrationStorePort(Protocol):
    def load_calibration(self, image_key: str) -> ImageCalibration | None: ...

    def save_calibration(self, calibration: ImageCalibration) -> None: ...


class WellCalibrationService:
    def __init__(
        self,
        detector: WellDetectorPort,
        physical_diameter_mm: float,
        store: CalibrationStorePort | None = None,
    ) -> None:
        if physical_diameter_mm <= 0:
            raise ValueError("physical diameter must be positive")
        self.detector = detector
        self.physical_diameter_mm = physical_diameter_mm
        self.store = store
        self._cache: dict[str, ImageCalibration] = {}

    def calibration_for(
        self, image: CrystalImage, force_detection: bool = False
    ) -> ImageCalibration:
        if not force_detection:
            cached = self._cache.get(image.image_key)
            if cached is not None and self._diameter_matches(cached):
                return cached
            if self.store is not None:
                stored = self.store.load_calibration(image.image_key)
                if stored is not None and self._diameter_matches(stored):
                    self._cache[image.image_key] = stored
                    return stored
        boundary = self.detector.detect(image)
        calibration = ImageCalibration(
            image.image_key,
            boundary.center_x_px,
            boundary.center_y_px,
            boundary.radius_x_px,
            boundary.radius_y_px,
            self.physical_diameter_mm,
            CalibrationMethod.AUTO_CIRCLE,
            boundary.confidence,
            False,
            datetime.now(UTC),
        )
        self.save(calibration)
        return calibration

    def _diameter_matches(self, calibration: ImageCalibration) -> bool:
        return abs(calibration.physical_diameter_mm - self.physical_diameter_mm) < 1e-9

    def save_manual_three_point(
        self,
        image: CrystalImage,
        points: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    ) -> ImageCalibration:
        center_x, center_y, radius = circle_from_three_points(points)
        calibration = ImageCalibration(
            image.image_key,
            center_x,
            center_y,
            radius,
            radius,
            self.physical_diameter_mm,
            CalibrationMethod.MANUAL_THREE_POINT,
            1.0,
            True,
            datetime.now(UTC),
        )
        self.save(calibration)
        return calibration

    def save(self, calibration: ImageCalibration) -> None:
        if self.store is not None:
            self.store.save_calibration(calibration)
        self._cache[calibration.image_key] = calibration


def circle_from_three_points(
    points: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> tuple[float, float, float]:
    (x1, y1), (x2, y2), (x3, y3) = points
    determinant = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(determinant) < 1e-6:
        raise ValueError("the three calibration points must not be collinear")
    x1_sq = x1 * x1 + y1 * y1
    x2_sq = x2 * x2 + y2 * y2
    x3_sq = x3 * x3 + y3 * y3
    center_x = (
        x1_sq * (y2 - y3) + x2_sq * (y3 - y1) + x3_sq * (y1 - y2)
    ) / determinant
    center_y = (
        x1_sq * (x3 - x2) + x2_sq * (x1 - x3) + x3_sq * (x2 - x1)
    ) / determinant
    radius = hypot(x1 - center_x, y1 - center_y)
    return center_x, center_y, radius
