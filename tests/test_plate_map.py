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
    from xtalflow.ui.plate_map import NO_CRYSTAL_GLYPH, CRYSTAL_GLYPH

    plate_map = PlateMap()
    cells = (
        _cell(1, label="crystal", image_index=0),
        _cell(2, label="crystal", positions=2, image_index=3),
    )
    plate_map.show_plate(SWISSCI_MIDI_3_LENS, cells)

    # Rendered off-screen; what matters is that the two differ and the second
    # carries the count. Painting is checked by rendering in the viewer test.
    assert CRYSTAL_GLYPH != NO_CRYSTAL_GLYPH
    assert plate_map._cells[2].positions == 2
    assert plate_map._cells[1].positions == 0


def test_a_cell_can_be_read_without_colour(app) -> None:
    """Filled diamond, open ring and dot differ in ink, not only in hue."""
    from xtalflow.ui.plate_map import (
        NO_CRYSTAL_GLYPH,
        CRYSTAL_GLYPH,
        EMPTY_GLYPH,
        GLYPHS,
        UNSCORED_GLYPH,
    )

    glyphs = {CRYSTAL_GLYPH, NO_CRYSTAL_GLYPH, EMPTY_GLYPH, UNSCORED_GLYPH}
    assert len(glyphs) == 4, "every state needs a shape of its own"
    # And each one is named in the legend, so the shape is never alone either.
    from xtalflow.ui.plate_map import LEGEND

    for word in ("crystal", "no crystal", "empty", "not scored", "reviewed"):
        assert word in LEGEND
    assert set(GLYPHS) == {"crystal", "no crystal", "empty"}


def _controller_with(wells: int = 3, drops: int = 3):
    from pathlib import Path

    from xtalflow.application import ReviewController
    from xtalflow.domain import (
        CrystalImage,
        PlateImages,
        ReviewPreferences,
        ReviewProgress,
        ReviewSession,
    )

    images = tuple(
        CrystalImage("2070", 14122, well, drop, "profileID_1", Path(f"{well}-{drop}.jpg"))
        for well in range(1, wells + 1)
        for drop in range(1, drops + 1)
    )
    plate = PlateImages("2070", 14122, "profileID_1", images)
    progress = ReviewProgress.create(
        plate.plate_code, plate.batch_id, plate.profile, images[0].image_key
    )
    controller = ReviewController(
        plate, progress, ReviewPreferences(1), ReviewSession()
    )
    controller.plate_format = SWISSCI_MIDI_3_LENS
    return controller


def _scored(image_key: str, crystal: float):
    from xtalflow.domain.crystal_score import CrystalScore

    rest = (1.0 - crystal) / 2
    return CrystalScore(image_key, ("crystal", "drop", "empty"), (crystal, rest, rest), "m")


def test_a_well_shows_its_best_drop_and_the_click_goes_to_that_drop(app) -> None:
    """A cell that showed the best drop but jumped to drop a would be a lie."""
    from xtalflow.ui.plate_map import plate_map_cells

    controller = _controller_with(wells=2)
    images = controller.plate.images
    scores = {
        images[0].image_key: _scored(images[0].image_key, 0.1),
        images[1].image_key: _scored(images[1].image_key, 0.9),  # well 1, drop 2
        images[2].image_key: _scored(images[2].image_key, 0.4),
    }
    ranks = {images[1].image_key: 1, images[2].image_key: 2, images[0].image_key: 3}

    cells = {cell.well_number: cell for cell in plate_map_cells(controller, scores, ranks)}

    assert cells[1].label == "crystal"
    assert cells[1].rank == 1
    assert cells[1].image_index == 1, "the click must land on the drop the cell shows"
    assert cells[1].address == "A01"
    # Every drop is itemised, by the lens letter the rest of the app uses -
    # which for a MIDI 3-lens plate is a, c, d, not a, b, c.
    assert [line[0] for line in cells[1].detail.splitlines()] == ["a", "c", "d"]
    assert cells[1].detail.splitlines()[1] == "c  crystal"


def test_a_well_nobody_has_scored_yet_still_has_a_cell_to_click(app) -> None:
    from xtalflow.ui.plate_map import plate_map_cells

    controller = _controller_with(wells=2)

    cells = {cell.well_number: cell for cell in plate_map_cells(controller, {}, {})}

    assert len(cells) == 2
    assert cells[2].label == ""
    assert cells[2].rank is None
    assert cells[2].image_index is not None
    assert "not scored" in cells[2].detail


def test_positions_are_counted_across_the_well_s_drops(app) -> None:
    from xtalflow.ui.plate_map import plate_map_cells

    controller = _controller_with(wells=2)
    images = controller.plate.images
    controller.session.add_target(images[0], 10, 10, 100, 100)
    controller.session.add_target(images[0], 20, 20, 100, 100)
    controller.session.add_target(images[2], 30, 30, 100, 100)

    cells = {cell.well_number: cell for cell in plate_map_cells(controller, {}, {})}

    assert cells[1].positions == 3, "a well's count is all of its drops"
    assert cells[2].positions == 0


def test_a_well_counts_as_reviewed_only_when_every_drop_is(app) -> None:
    from xtalflow.ui.plate_map import plate_map_cells

    controller = _controller_with(wells=1)
    images = controller.plate.images
    controller.session.mark_reviewed(images[0])
    controller.session.mark_reviewed(images[1])

    (cell,) = plate_map_cells(controller, {}, {})
    assert not cell.reviewed

    controller.session.mark_reviewed(images[2])
    (cell,) = plate_map_cells(controller, {}, {})
    assert cell.reviewed
