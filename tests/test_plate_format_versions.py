"""A new plate format version must not change plans made with an older one."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from xtalflow.application import ProjectController
from xtalflow.application.planning_service import (
    fragment_plan_snapshot,
    raw_crystal_plan_snapshot,
)
from xtalflow.domain import (
    SWISSCI_MIDI_3_LENS,
    ImageCalibration,
    LensDefinition,
    crystal_selection_from_selected_crystals,
    plate_format_by_id,
)
from xtalflow.domain import plate_format as plate_format_module
from xtalflow.domain.crystal_selection import selected_crystals_from_crystal_selection
from xtalflow.domain.fragment_screening import (
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.labwork import build_raw_crystal_labworks
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.domain.worksheets import build_echo_worksheet
from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
from xtalflow.settings import DEFAULT_SETTINGS


FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)

MIDI_V2 = replace(
    SWISSCI_MIDI_3_LENS,
    version=2,
    lenses=(
        SWISSCI_MIDI_3_LENS.lenses[0],
        SWISSCI_MIDI_3_LENS.lenses[1],
        LensDefinition(
            3, "d", 2.77, echo_x_correction_um=-650,
            destination_row_offset=1, destination_column_offset=1,
        ),
    ),
    instrument_name="SwissCI-MRC-3d-v2",
)


@pytest.fixture
def midi_v2_released(monkeypatch):
    """Register v2 as the current format while keeping v1 for existing plans."""
    monkeypatch.setattr(plate_format_module, "PLATE_FORMATS", (MIDI_V2,))
    monkeypatch.setattr(
        plate_format_module, "PLATE_FORMAT_VERSIONS", (SWISSCI_MIDI_3_LENS, MIDI_V2)
    )


def _crystal(version: int) -> SelectedCrystal:
    return SelectedCrystal(
        "1070:5947:13:3:profileID_1", "1070", "B01d",
        (CrystalTarget("target", Decimal("0.1"), Decimal("0"), NOW),),
        SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg", version,
        "image-set", 5947, "profileID_1",
    )


def _fragment_plan(version: int):
    fragment = Fragment(
        "Vendor", "Library", "1", "CMP-1", "C2H6O", Decimal("46.07"), "CCO",
        Decimal("100"), "DMSO", "SRC-1", "A01",
    )
    return build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), (_crystal(version),), Decimal("25")
    )


def test_format_lookup_returns_exact_version_or_current(midi_v2_released) -> None:
    assert plate_format_by_id(SWISSCI_MIDI_3_LENS.id) is MIDI_V2
    assert plate_format_by_id(SWISSCI_MIDI_3_LENS.id, 1) is SWISSCI_MIDI_3_LENS
    assert plate_format_by_id(SWISSCI_MIDI_3_LENS.id, 3) is None


def test_worksheets_and_labworks_use_the_version_each_plan_selected(
    midi_v2_released,
) -> None:
    v1_rows = build_echo_worksheet(_fragment_plan(1))
    v2_rows = build_echo_worksheet(_fragment_plan(2))
    v1_labworks = build_raw_crystal_labworks(
        build_raw_crystal_plan((_crystal(1),)),
        experiment_id="RawCrystal-01", protein_name="BRD4",
        username="fbdd", account_id="fbdd",
    )

    assert v1_rows[0].values()[6] == "-600.0"
    assert v2_rows[0].values()[6] == "-550.0"
    assert v1_labworks[0].plate_type == "SwissCI-MRC-3d"


def test_selection_keeps_version_and_image_source(tmp_path: Path) -> None:
    selection = crystal_selection_from_selected_crystals("plan", (_crystal(2),))
    well = selection.wells[0]

    assert (well.plate_format_version, well.image_set_id, well.batch_id, well.profile) == (
        2, "image-set", 5947, "profileID_1"
    )
    assert selected_crystals_from_crystal_selection(selection)[0] == _crystal(2)


def test_v1_snapshots_are_unchanged_and_later_versions_are_recorded() -> None:
    v1_snapshot = json.loads(raw_crystal_plan_snapshot(
        build_raw_crystal_plan((_crystal(1),)), "BRD4"
    ))
    v2_snapshot = json.loads(fragment_plan_snapshot(_fragment_plan(2), "BRD4", None, "1"))

    assert "plate_format_version" not in v1_snapshot["selections"][0]
    assert v2_snapshot["assignments"][0]["plate_format_version"] == 2


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_existing_v1_image_set_still_opens_and_plans_with_v1(
    tmp_path: Path, midi_v2_released
) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store.workspace)
    project = workspace.create_project("Existing")
    image_set = workspace.add_pinned_image_set(
        "1070", 5947, "profileID_1", SWISSCI_MIDI_3_LENS
    )
    workspace.create_project("Other")

    workspace.open_project(project.id)
    controller = workspace.review_controller
    controller.add_target(512, 512, 1024, 1024)
    store.workspace.scoped_to(image_set.id).save_calibration(
        replace(
            ImageCalibration.automatic(
                controller.current_image.image_key, 512, 512, 500, 0.99, 2.77
            ),
            confirmed=True,
        )
    )
    controller.checkpoint_current()
    crystal = workspace.selected_crystals_for_plan()[0]

    assert image_set.plate_format_version == 1
    assert (crystal.plate_format_version, crystal.image_set_id, crystal.batch_id) == (
        1, image_set.id, 5947
    )
    store.close()
