from __future__ import annotations

import cv2
import numpy as np

from xtalflow.application.calibration_service import CalibrationDetectionError
from xtalflow.domain import CrystalImage, WellBoundary


class OpenCVWellDetector:
    """Detect a well boundary in pixels, independently of plate dimensions."""

    def detect(self, image: CrystalImage) -> WellBoundary:
        grayscale = cv2.imread(str(image.path), cv2.IMREAD_GRAYSCALE)
        if grayscale is None:
            raise CalibrationDetectionError(f"cannot read calibration image: {image.path}")
        height, width = grayscale.shape
        shorter = min(height, width)
        blurred = cv2.medianBlur(grayscale, 9)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=shorter * 0.4,
            param1=100,
            param2=50,
            minRadius=round(shorter * 0.40),
            maxRadius=round(shorter * 0.50),
        )
        if circles is None:
            raise CalibrationDetectionError("automatic well-circle detection failed")
        candidates = circles[0]
        center_hint = np.array((width / 2, height / 2))
        center_scale = shorter * 0.25

        def candidate_score(circle) -> float:
            center_distance = np.linalg.norm(circle[:2] - center_hint) / center_scale
            radius_distance = abs(circle[2] / shorter - 0.475) / 0.075
            return float(center_distance + radius_distance)

        center_x, center_y, radius = min(candidates, key=candidate_score)
        confidence = self._confidence(grayscale, center_x, center_y, radius)
        if confidence < 0.45:
            raise CalibrationDetectionError(
                f"well-circle confidence is too low ({confidence:.0%})"
            )
        return WellBoundary(
            float(center_x),
            float(center_y),
            float(radius),
            float(radius),
            confidence,
        )

    @staticmethod
    def _confidence(
        grayscale: np.ndarray, center_x: float, center_y: float, radius: float
    ) -> float:
        gradient_x = cv2.Sobel(grayscale, cv2.CV_32F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(grayscale, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = cv2.magnitude(gradient_x, gradient_y)
        angles = np.linspace(0, 2 * np.pi, 360, endpoint=False)
        x = np.clip(np.rint(center_x + radius * np.cos(angles)).astype(int), 0, grayscale.shape[1] - 1)
        y = np.clip(np.rint(center_y + radius * np.sin(angles)).astype(int), 0, grayscale.shape[0] - 1)
        boundary = magnitude[y, x]
        threshold = max(float(np.percentile(magnitude, 75)), 1.0)
        edge_support = float(np.mean(boundary >= threshold))
        margin = min(
            center_x - radius,
            center_y - radius,
            grayscale.shape[1] - center_x - radius,
            grayscale.shape[0] - center_y - radius,
        )
        visibility = float(np.clip((margin + radius * 0.08) / (radius * 0.08), 0, 1))
        return float(np.clip(0.25 + 0.65 * edge_support + 0.10 * visibility, 0, 1))
