"""The plate at a glance: one cell per well, what the model made of it.

Opening a plate, the question is "where should I start" and the answer lives
in the shape of the whole plate, not in image 1 of 288. One glance should say
"the bottom right quadrant" and a click should go there.

Three things have to share a cell without becoming a hieroglyph, so each takes
a different channel:

    what the model said    the glyph, in the middle
    whether it is reviewed the cell's fill, a tone rather than a hue
    positions placed       a dot, in the bottom-right corner

The dot says that a well has positions and not how many. The count used to be
drawn in place of the glyph, so marking a well - the ordinary thing to do -
hid whether a crystal was ever seen in it, which is what the map is for. How
many is in the tooltip: a count is a number to read, and this is looked at.

Colour is never the only difference: filled diamond, open ring and dot differ
in how much ink is on the screen, which survives both a monochrome screen and
not being able to tell the two colours apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from xtalflow.domain.plate_format import PlateFormat
from xtalflow.ui import theme

CELL = 28
GAP = 2
MARGIN_LEFT = 24
MARGIN_TOP = 18
# The chosen dot, in the corner where it cannot sit on the class glyph.
DOT = 6

CRYSTAL_GLYPH = "◆"
NO_CRYSTAL_GLYPH = "○"
EMPTY_GLYPH = "·"
UNSCORED_GLYPH = theme.SYMBOL_ERROR
UNREACHED_GLYPH = ""

GLYPHS = {
    "crystal": (CRYSTAL_GLYPH, theme.TEXT),
    "no crystal": (NO_CRYSTAL_GLYPH, theme.TEXT_MUTED),
    "empty": (EMPTY_GLYPH, theme.TEXT_TERTIARY),
}
CHOSEN_GLYPH = "●"
LEGEND = (
    f"One mark per subwell.   {CRYSTAL_GLYPH} crystal   {NO_CRYSTAL_GLYPH} no crystal   "
    f"{EMPTY_GLYPH} empty   {UNSCORED_GLYPH} not scored   "
    f"underlined = positions placed in that subwell   shaded = reviewed"
)


@dataclass(frozen=True)
class DropMark:
    """One subwell of a well: what the model made of it, and whether it is picked."""

    label: str = ""      # "crystal", "no crystal", "empty", or "" for not scored
    positions: int = 0   # positions placed in this drop


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
    # One mark per subwell, in drop order. A well of three drops draws three
    # marks, so "crystal in one of them" and "crystal in all three" stop
    # looking the same - which is the question the map is opened to answer.
    drops: tuple[DropMark, ...] = ()


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
        """What the cell says in words. Numbers live here, not on the map.

        No percentage: this model has never had its accuracy measured, so a
        figure would read as a chance of a crystal and could not be backed.

        How many positions a well holds is here because the map shows only
        that it holds some: a count is a number to read, and the map is
        looked at. The per-drop lines follow, so three drops of one well can
        be told apart.
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
        if cell.positions:
            head = f"{head} · {cell.positions} position"
            head = head if cell.positions == 1 else f"{head}s"
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

        self._paint_drop_marks(painter, cell, x, y)

        if well_number == self._current_well:
            painter.setPen(QPen(QColor(theme.FOCUS), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(x + 1, y + 1, CELL - 2, CELL - 2, 4, 4)


    def _paint_drop_marks(self, painter: QPainter, cell: WellCell, x: int, y: int) -> None:
        """One mark per subwell, left to right in drop order.

        The well used to show its best drop alone, so a well with a crystal
        in one of three drops and a well with a crystal in all three were
        drawn identically - and which drop it was lived only in the tooltip.

        A mark is drawn rather than written. At three to a cell each one has
        about nine pixels, and a font glyph at that size turns to mush while
        a polygon stays the shape it is.
        """
        drops = cell.drops or (DropMark(cell.label, cell.positions),)
        slot = CELL / len(drops)
        size = min(slot - 2.0, 11.0)
        for position, drop in enumerate(drops):
            centre_x = x + slot * (position + 0.5)
            centre_y = y + CELL / 2.0
            self._paint_one_mark(painter, drop, cell.reviewed, centre_x, centre_y, size)
        painter.setBrush(Qt.NoBrush)

    @staticmethod
    def _paint_one_mark(
        painter: QPainter, drop: DropMark, reviewed: bool,
        centre_x: float, centre_y: float, size: float,
    ) -> None:
        half = size / 2.0
        if drop.positions:
            # A picked drop keeps its shape and changes colour, so marking a
            # drop cannot hide whether a crystal was seen in it - and which
            # drop was picked is now on the map, where the corner dot this
            # replaced could only say that some drop was.
            #
            # Colour is not the only difference: a picked mark is underlined.
            # Three marks to a cell leave no width for a ring around one, but
            # a cell is 28 tall against a mark of 11, so the room that is
            # left is below it. The line survives a monochrome screen and a
            # reader who does not separate blue from grey.
            colour = theme.FOCUS
        elif not drop.label:
            # Grey, not the attention colour. One unscored mark per well was
            # an exception worth a colour; three per well across a plate
            # nobody has scored yet is most of the map, and a map that is
            # mostly warning is a map that is shouted at rather than read.
            colour = theme.TEXT_TERTIARY
        elif reviewed:
            colour = theme.TEXT_TERTIARY
        else:
            colour = GLYPHS.get(drop.label, (UNSCORED_GLYPH, theme.ATTENTION))[1]
        pen = QPen(QColor(colour))
        pen.setWidthF(1.4)
        painter.setPen(pen)

        if not drop.label:
            # Not scored: an open triangle, the shape the rest of the window
            # uses for something still to be looked at.
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(
                QPolygonF([
                    QPointF(centre_x, centre_y - half),
                    QPointF(centre_x + half, centre_y + half),
                    QPointF(centre_x - half, centre_y + half),
                ])
            )
        elif drop.label == "crystal":
            painter.setBrush(QColor(colour))
            painter.drawPolygon(
                QPolygonF([
                    QPointF(centre_x, centre_y - half),
                    QPointF(centre_x + half, centre_y),
                    QPointF(centre_x, centre_y + half),
                    QPointF(centre_x - half, centre_y),
                ])
            )
        elif drop.label == "no crystal":
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(centre_x, centre_y), half, half)
        else:
            # Empty: far less ink than either, so a glance passes over it.
            painter.setBrush(QColor(colour))
            painter.drawEllipse(QPointF(centre_x, centre_y), 1.2, 1.2)

        if drop.positions:
            underline = QPen(QColor(colour))
            underline.setWidthF(1.8)
            painter.setPen(underline)
            painter.drawLine(
                QPointF(centre_x - half, centre_y + half + 2.5),
                QPointF(centre_x + half, centre_y + half + 2.5),
            )


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


def plate_map_cells(controller, scores, ranks) -> tuple[WellCell, ...]:
    """One cell per well of the open image set, from what is known right now.

    ``scores`` is keyed by image key, ``ranks`` gives each image's place in the
    crystal-first order counting from one. A well shows its best drop: the one
    the model liked most, or its first if none of them is scored yet.

    No Qt here, so the arithmetic that decides what a glance says can be
    tested without a screen.
    """
    plate_format = controller_plate_format(controller)
    by_well: dict[int, list[int]] = {}
    for index, image in enumerate(controller.plate.images):
        by_well.setdefault(image.well_number, []).append(index)

    cells = []
    for well_number, indices in sorted(by_well.items()):
        images = [controller.plate.images[index] for index in indices]
        best_position = max(
            range(len(images)),
            key=lambda position: (
                scores[images[position].image_key].crystal_probability
                if images[position].image_key in scores else -1.0
            ),
        )
        best = images[best_position]
        score = scores.get(best.image_key)
        positions = sum(controller.session.target_count_for(image) for image in images)
        lines = []
        drops = []
        for image in images:
            suffix = _drop_suffix(plate_format, image)
            scored = scores.get(image.image_key)
            parts = [scored.display_label] if scored is not None else ["not scored"]
            count = controller.session.target_count_for(image)
            if count:
                parts.append(f"{count} position{'' if count == 1 else 's'}")
            elif controller.session.is_reviewed(image):
                parts.append("reviewed")
            lines.append(f"{suffix}  {' · '.join(parts)}")
            drops.append(
                DropMark(scored.display_label if scored is not None else "", count)
            )
        cells.append(
            WellCell(
                well_number=well_number,
                address=_well_address(plate_format, best),
                label=score.display_label if score is not None else "",
                rank=ranks.get(best.image_key),
                positions=positions,
                reviewed=all(controller.session.is_reviewed(image) for image in images),
                detail="\n".join(lines),
                image_index=indices[best_position],
                drops=tuple(drops),
            )
        )
    return tuple(cells)


def controller_plate_format(controller):
    return getattr(controller, "plate_format", None)


def _drop_suffix(plate_format, image) -> str:
    if plate_format is None:
        return f"d{image.drop_number}"
    try:
        return plate_format.address_for(image.well_number, image.drop_number).lens
    except ValueError:
        return f"d{image.drop_number}"


def _well_address(plate_format, image) -> str:
    if plate_format is None:
        return f"Well {image.well_number}"
    try:
        return str(plate_format.address_for(image.well_number, image.drop_number))[:3]
    except ValueError:
        return f"Well {image.well_number}"
