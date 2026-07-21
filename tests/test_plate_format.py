from pathlib import Path

import pytest

from xtalflow.application import ProjectController
from xtalflow.domain import (
    SWISSCI_MIDI_3_LENS,
    SWISSCI_MRC_2_WELL,
    CrystalImage,
    PlateImages,
)
from xtalflow.infrastructure import SQLiteReviewStore


def test_legacy_well_address_mapping_is_row_major_and_lens_specific() -> None:
    assert str(SWISSCI_MIDI_3_LENS.address_for(1, 1)) == "A01a"
    assert str(SWISSCI_MIDI_3_LENS.address_for(12, 2)) == "A12c"
    assert str(SWISSCI_MIDI_3_LENS.address_for(13, 3)) == "B01d"
    assert str(SWISSCI_MIDI_3_LENS.address_for(96, 3)) == "H12d"
    assert str(SWISSCI_MRC_2_WELL.address_for(1, 1)) == "A01a"
    assert str(SWISSCI_MRC_2_WELL.address_for(96, 2)) == "H12b"


@pytest.mark.parametrize(
    "plate_format, expected_count",
    ((SWISSCI_MIDI_3_LENS, 288), (SWISSCI_MRC_2_WELL, 192)),
)
def test_every_address_in_a_plate_format_is_unique(
    plate_format, expected_count
) -> None:
    addresses = {
        str(plate_format.address_for(well, lens.drop_number))
        for well in range(1, 97)
        for lens in plate_format.lenses
    }
    assert len(addresses) == expected_count


def test_three_lens_echo_offset_corrects_only_subwell_d_x_axis() -> None:
    assert SWISSCI_MIDI_3_LENS.echo_offset_um("A01a", 0.25, -0.5) == (
        250.0,
        -500.0,
    )
    assert SWISSCI_MIDI_3_LENS.echo_offset_um("A01c", 0.25, -0.5) == (
        250.0,
        -500.0,
    )
    assert SWISSCI_MIDI_3_LENS.echo_offset_um("A01d", 0.25, -0.5) == (
        -450.0,
        -500.0,
    )
    assert SWISSCI_MIDI_3_LENS.echo_destination_well("A01a") == "A01"
    assert SWISSCI_MIDI_3_LENS.echo_destination_well("A01c") == "B01"
    assert SWISSCI_MIDI_3_LENS.echo_destination_well("A01d") == "B02"
    assert SWISSCI_MIDI_3_LENS.echo_destination_well("H12d") == "P24"


def test_selected_format_filters_images_without_inferring_plate_identity() -> None:
    images = tuple(
        CrystalImage(
            "1070", 1, 1, drop, "profileID_1", Path(f"d{drop}.jpg")
        )
        for drop in (1, 2, 3)
    )
    plate = PlateImages("1070", 1, "profileID_1", images)

    class Repository:
        def load_plate(self, plate_code, profile="profileID_1"):
            return plate

    controller = ProjectController(Repository())
    controller.create_project("Validation")

    image_set = controller.add_latest_image_set("1070", SWISSCI_MRC_2_WELL)

    assert image_set.plate_format_id == SWISSCI_MRC_2_WELL.id
    assert [image.drop_number for image in controller.review_controller.plate.images] == [
        1,
        2,
    ]


def test_plate_format_is_persisted_with_project_image_set(tmp_path: Path) -> None:
    image = CrystalImage("1070", 1, 1, 1, "profileID_1", Path("image.jpg"))
    plate = PlateImages("1070", 1, "profileID_1", (image,))

    class Repository:
        def load_plate(self, plate_code, profile="profileID_1"):
            return plate

    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    controller = ProjectController(Repository(), store)
    project = controller.create_project("Persistence")
    controller.add_latest_image_set("1070", SWISSCI_MIDI_3_LENS)
    store.close()

    restored_store = SQLiteReviewStore(database_path)
    restored = next(
        item for item in restored_store.load_projects() if item.id == project.id
    )
    image_set = restored.active_image_sets[0]
    assert image_set.plate_format_id == SWISSCI_MIDI_3_LENS.id
    assert image_set.plate_format_version == SWISSCI_MIDI_3_LENS.version
    restored_store.close()
