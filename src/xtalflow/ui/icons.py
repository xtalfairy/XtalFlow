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


# A flask of solvent: how long crystals survive in it.
SOLVENT_TEST = (
    '<path d="M9.5 3.5h5M10.5 3.5v5.2L5.4 17.6a2 2 0 0 0 1.7 2.9h9.8a2 2 0 0 0 1.7-2.9'
    'L13.5 8.7V3.5"/>'
    '<path d="M7.6 14h8.8"/>'
)

# A snowflake: cryoprotectant before freezing.
CRYO_TEST = (
    '<path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/>'
    '<path d="M9.8 4.8 12 7l2.2-2.2M9.8 19.2 12 17l2.2 2.2"/>'
)

# A window with its side panel, for showing or hiding the workspace panel.
SIDEBAR = (
    '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/>'
    '<path d="M9.5 4.5v15"/>'
)


# A clock turned back: what the beamline already did.
HISTORY = (
    '<path d="M3.6 12a8.4 8.4 0 1 0 2.5-6"/>'
    '<path d="M3.2 4.3v4.2h4.2"/>'
    '<path d="M12 7.4V12l3.2 1.9"/>'
)

# Stage marks for the experiment list. Each has its own shape, so the stage
# is still readable where colour is not: a tick for a stage that is done, a
# warning triangle for one that needs a look, an open ring for a draft.
STAGE_OK = '<path d="M20 6.8 9.8 17 4 11.2"/>'
STAGE_ATTENTION = (
    '<path d="M12 4.2 21.2 20H2.8z"/><path d="M12 10v4M12 16.8h.01"/>'
)
STAGE_DRAFT = '<circle cx="12" cy="12" r="7"/>'

STAGE_MARKS = {"ok": STAGE_OK, "attention": STAGE_ATTENTION, "muted": STAGE_DRAFT}

# Lamps, for a row of them that is read at a glance. Filled against open is a
# difference in how much ink is on the screen, so the odd one out is found
# without telling the colours apart; the glyph inside says which it is at
# reading distance.
LAMP_LIT = (
    '<circle cx="12" cy="12" r="9" fill="{color}" stroke="none"/>'
    '<path d="M16.8 9.2 10.8 15.2 7.6 12" stroke="#FFFFFF" stroke-width="2.4"/>'
)
LAMP_OPEN = (
    '<circle cx="12" cy="12" r="8.5" stroke-width="2.2"/>'
    '<path d="M12 7.3v5.6M12 16.4h.01" stroke-width="2.6"/>'
)


def lamp_pixmap(lit: bool, size: int, color: str) -> QPixmap:
    """A lamp: filled with a tick when lit, an open ring with a mark when not.

    The lit body carries its own fill, because the shared template only colours
    strokes.
    """
    return svg_pixmap(LAMP_LIT.format(color=color) if lit else LAMP_OPEN, size, color)


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
