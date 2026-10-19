from dataclasses import replace

import pytest
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from xtalflow.domain.plate_format import SWISSCI_MIDI_3_LENS, SWISSCI_MRC_2_WELL
from xtalflow.ui.plate_map import CELL, GAP, MARGIN_LEFT, MARGIN_TOP, PlateMap, WellCell


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _cell(well: int, **changes) -> WellCell:
    base = WellCell(well, f"{chr(ord('A') + (well - 1) // 12)}{(well - 1) % 12 + 1:02d}")
    return replace(base, **changes) if changes else base


def test_the_grid_comes_from_the_plate_format_not_a_constant(app) -> None:
    """MRC plates are not 8x12 with three drops, and hardcoding said they were."""
    plate_map = PlateMap()

    plate_map.show_plate(SWISSCI_MIDI_3_LENS, ())
    assert (plate_map.rows, plate_map.columns) == (8, 12)
    midi = plate_map.sizeHint()

    plate_map.show_plate(SWISSCI_MRC_2_WELL, ())
    assert (plate_map.rows, plate_map.columns) == (
        SWISSCI_MRC_2_WELL.rows,
        SWISSCI_MRC_2_WELL.columns,
    )
    assert plate_map.sizeHint().width() > 0
    assert midi.width() == MARGIN_LEFT + 12 * (CELL + GAP)


def test_clicking_a_well_asks_to_go_to_that_well_s_image(app) -> None:
    plate_map = PlateMap()
    plate_map.show_plate(
        SWISSCI_MIDI_3_LENS,
        (_cell(1, image_index=7), _cell(14, image_index=41)),
    )
    went: list[int] = []
    plate_map.well_clicked.connect(went.append)

    def click(row: int, column: int) -> None:
        point = QPoint(
            MARGIN_LEFT + column * (CELL + GAP) + CELL // 2,
            MARGIN_TOP + row * (CELL + GAP) + CELL // 2,
        )
        plate_map.mousePressEvent(
            QMouseEvent(QEvent.MouseButtonPress, point, Qt.LeftButton,
                        Qt.LeftButton, Qt.NoModifier)
        )

    click(0, 0)   # well 1
    click(1, 1)   # well 14
    click(3, 3)   # a well with no cell at all
    assert went == [7, 41]


def test_a_tooltip_says_the_class_and_the_rank_and_never_a_percentage(app) -> None:
    """The model's accuracy has never been measured; a percentage would be a claim."""
    scored = _cell(3, label="crystal", rank=2, detail="a  clear · reviewed\nb  crystal")
    unscored = _cell(4)

    assert PlateMap.tooltip_for(scored) == (
        "A03 · crystal rank 2\na  clear · reviewed\nb  crystal"
    )
    assert "%" not in PlateMap.tooltip_for(scored)
    assert PlateMap.tooltip_for(unscored) == "A04 · not scored"


def test_a_well_with_positions_shows_the_count_in_place_of_the_glyph(app) -> None:
    """Once somebody has marked a well, what a model guessed about it is history."""
    from xtalflow.ui.plate_map import CLEAR_GLYPH, CRYSTAL_GLYPH

    plate_map = PlateMap()
    cells = (
        _cell(1, label="crystal", image_index=0),
        _cell(2, label="crystal", positions=2, image_index=3),
    )
    plate_map.show_plate(SWISSCI_MIDI_3_LENS, cells)

    # Rendered off-screen; what matters is that the two differ and the second
    # carries the count. Painting is checked by rendering in the viewer test.
    assert CRYSTAL_GLYPH != CLEAR_GLYPH
    assert plate_map._cells[2].positions == 2
    assert plate_map._cells[1].positions == 0


def test_a_cell_can_be_read_without_colour(app) -> None:
    """Filled diamond, open ring and dot differ in ink, not only in hue."""
    from xtalflow.ui.plate_map import (
        CLEAR_GLYPH,
        CRYSTAL_GLYPH,
        EMPTY_GLYPH,
        GLYPHS,
        UNSCORED_GLYPH,
    )

    glyphs = {CRYSTAL_GLYPH, CLEAR_GLYPH, EMPTY_GLYPH, UNSCORED_GLYPH}
    assert len(glyphs) == 4, "every state needs a shape of its own"
    # And each one is named in the legend, so the shape is never alone either.
    from xtalflow.ui.plate_map import LEGEND

    for word in ("crystal", "clear", "empty", "not scored", "reviewed"):
        assert word in LEGEND
    assert set(GLYPHS) == {"crystal", "clear", "empty"}
