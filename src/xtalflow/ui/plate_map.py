"""The plate at a glance: one cell per well, what the model made of it.

Opening a plate, the question is "where should I start" and the answer lives
in the shape of the whole plate, not in image 1 of 288. One glance should say
"the bottom right quadrant" and a click should go there.

Three things have to share a cell without becoming a hieroglyph, so each takes
a different channel:

    what the model said    the glyph, in the middle
    whether it is reviewed the cell's fill, a tone rather than a hue
    positions placed       the count, in place of the glyph

The count replaces the glyph because once somebody has marked a well, what a
model guessed about it is history.

Colour is never the only difference: filled diamond, open ring and dot differ
in how much ink is on the screen, which survives both a monochrome screen and
not being able to tell the two colours apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from xtalflow.domain.plate_format import PlateFormat
from xtalflow.ui import theme

CELL = 28
GAP = 2
MARGIN_LEFT = 24
MARGIN_TOP = 18

CRYSTAL_GLYPH = "◆"
CLEAR_GLYPH = "○"
EMPTY_GLYPH = "·"
UNSCORED_GLYPH = theme.SYMBOL_ERROR
UNREACHED_GLYPH = ""

GLYPHS = {
    "crystal": (CRYSTAL_GLYPH, theme.TEXT),
    "clear": (CLEAR_GLYPH, theme.TEXT_MUTED),
    "empty": (EMPTY_GLYPH, theme.TEXT_TERTIARY),
}
LEGEND = (
    f"{CRYSTAL_GLYPH} crystal   {CLEAR_GLYPH} clear   {EMPTY_GLYPH} empty   "
    f"{UNSCORED_GLYPH} not scored   2 positions placed   shaded = reviewed"
)


@dataclass(frozen=True)
class WellCell:
    """One well's row in the map, already decided by the window."""

    well_number: int
    address: str
    label: str = ""          # the display class of the well's best drop
    rank: int | None = None  # its place in the crystal-first order, from 1
    positions: int = 0
    reviewed: bool = False
    detail: str = ""         # the tooltip's per-drop lines
    image_index: int | None = None  # where a click goes


class PlateMap(QWidget):
    """A grid of wells; clicking one asks the window to go there."""

    well_clicked = pyqtSignal(int)  # the image index to move to

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._format: PlateFormat | None = None
        self._cells: dict[int, WellCell] = {}
        self._current_well: int | None = None
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAccessibleName("Plate map")

    def show_plate(
        self,
        plate_format: PlateFormat | None,
        cells: tuple[WellCell, ...],
        current_well: int | None = None,
    ) -> None:
        self._format = plate_format
        self._cells = {cell.well_number: cell for cell in cells}
        self._current_well = current_well
        self.updateGeometry()
        self.update()

    def set_current_well(self, well_number: int | None) -> None:
        if well_number != self._current_well:
            self._current_well = well_number
            self.update()

    # -- geometry -----------------------------------------------------------

    @property
    def rows(self) -> int:
        return self._format.rows if self._format is not None else 0

    @property
    def columns(self) -> int:
        return self._format.columns if self._format is not None else 0

    def sizeHint(self) -> QSize:
        if self._format is None:
            return QSize(0, 0)
        return QSize(
            MARGIN_LEFT + self.columns * (CELL + GAP),
            MARGIN_TOP + self.rows * (CELL + GAP),
        )

    def _cell_rect(self, row: int, column: int) -> tuple[int, int]:
        return (
            MARGIN_LEFT + column * (CELL + GAP),
            MARGIN_TOP + row * (CELL + GAP),
        )

    def _well_at(self, x: int, y: int) -> int | None:
        if self._format is None:
            return None
        column = (x - MARGIN_LEFT) // (CELL + GAP)
        row = (y - MARGIN_TOP) // (CELL + GAP)
        if not (0 <= row < self.rows and 0 <= column < self.columns):
            return None
        return row * self.columns + column + 1

    # -- interaction --------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        well = self._well_at(event.x(), event.y())
        cell = self._cells.get(well) if well is not None else None
        if cell is not None and cell.image_index is not None:
            self.well_clicked.emit(cell.image_index)

    def mouseMoveEvent(self, event) -> None:
        well = self._well_at(event.x(), event.y())
        cell = self._cells.get(well) if well is not None else None
        self.setToolTip(self.tooltip_for(cell) if cell is not None else "")

    @staticmethod
    def tooltip_for(cell: WellCell) -> str:
        """What the cell says in words. Rank is the only number.

        No percentage: this model has never had its accuracy measured, so a
        figure would read as a chance of a crystal and could not be backed.
        """
        head = cell.address
        if cell.label:
            head = f"{head} · {cell.label}"
        if cell.rank is not None:
            head = f"{head} rank {cell.rank}"
        elif cell.label:
            head = f"{head}"
        else:
            head = f"{head} · not scored"
        return f"{head}\n{cell.detail}" if cell.detail else head

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._format is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        heading = QFont(self.font())
        heading.setPointSizeF(max(7.0, self.font().pointSizeF() - 2))
        painter.setFont(heading)
        painter.setPen(QColor(theme.TEXT_TERTIARY))
        for column in range(self.columns):
            x, _ = self._cell_rect(0, column)
            painter.drawText(x, 0, CELL, MARGIN_TOP, Qt.AlignCenter, str(column + 1))
        for row in range(self.rows):
            _, y = self._cell_rect(row, 0)
            painter.drawText(
                0, y, MARGIN_LEFT, CELL, Qt.AlignCenter, chr(ord("A") + row)
            )

        for row in range(self.rows):
            for column in range(self.columns):
                self._paint_cell(painter, row, column)
        painter.end()

    def _paint_cell(self, painter: QPainter, row: int, column: int) -> None:
        well_number = row * self.columns + column + 1
        cell = self._cells.get(well_number)
        x, y = self._cell_rect(row, column)

        painter.setPen(Qt.NoPen)
        # Reviewed is a tone, not a hue: it reads as "behind you" and it still
        # reads with the colours turned off.
        painter.setBrush(
            # SELECTED rather than SUBTLE: against white, SUBTLE is a four
            # percent difference and disappears on a beamline monitor.
            QColor(theme.SELECTED) if cell is not None and cell.reviewed
            else QColor("#FFFFFF")
        )
        painter.drawRoundedRect(x, y, CELL, CELL, 4, 4)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawRoundedRect(x, y, CELL, CELL, 4, 4)

        if cell is None:
            return

        glyph_font = QFont(self.font())
        if cell.positions:
            # The user's own mark outranks the model's guess.
            glyph, colour = str(cell.positions), theme.FOCUS
            glyph_font.setBold(True)
        elif not cell.label:
            glyph, colour = UNSCORED_GLYPH, theme.ATTENTION
            glyph_font.setBold(True)
        else:
            glyph, colour = GLYPHS.get(cell.label, (UNSCORED_GLYPH, theme.ATTENTION))
            if cell.reviewed:
                colour = theme.TEXT_TERTIARY
        painter.setFont(glyph_font)
        painter.setPen(QColor(colour))
        painter.drawText(x, y, CELL, CELL, Qt.AlignCenter, glyph)

        if well_number == self._current_well:
            painter.setPen(QPen(QColor(theme.FOCUS), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(x + 1, y + 1, CELL - 2, CELL - 2, 4, 4)


class PlateMapPanel(QWidget):
    """The map with the two lines that explain it."""

    well_clicked = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.heading_label = QLabel("No plate open")
        self.heading_label.setObjectName("Muted")
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.map = PlateMap()
        self.map.well_clicked.connect(self.well_clicked)
        self.legend_label = QLabel(LEGEND)
        self.legend_label.setObjectName("Muted")
        self.legend_label.setWordWrap(True)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACING_S)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.map, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.legend_label)
        layout.addStretch()
        self.setLayout(layout)

    def show_plate(self, heading: str, plate_format, cells, current_well=None) -> None:
        self.heading_label.setText(heading)
        self.map.show_plate(plate_format, cells, current_well)

    def show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
