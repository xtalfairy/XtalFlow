"""Line icons drawn from inline SVG so they stay sharp on any display."""

from __future__ import annotations

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

# A drop dispensed into a crystallization well.
FRAGMENT_SCREENING = (
    '<path d="M12 2.8c-2 2.6-3.2 4.4-3.2 5.9a3.2 3.2 0 0 0 6.4 0c0-1.5-1.2-3.3-3.2-5.9z"/>'
    '<ellipse cx="12" cy="16" rx="8" ry="2.6"/>'
    '<path d="M4 16v1.6c0 1.9 3.6 3.6 8 3.6s8-1.7 8-3.6V16"/>'
)

# A single crystal, harvested as it is.
RAW_CRYSTAL = (
    '<path d="M12 3l6.5 4.2v9.6L12 21l-6.5-4.2V7.2z"/>'
    '<path d="M5.5 7.2L12 11.4l6.5-4.2M12 11.4V21"/>'
)


# A window with its side panel, for showing or hiding the workspace panel.
SIDEBAR = (
    '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/>'
    '<path d="M9.5 4.5v15"/>'
)


def svg_pixmap(body: str, size: int, color: str, device_pixel_ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_SVG.format(color=color, body=body).encode()))
    pixels = round(size * device_pixel_ratio)
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap
