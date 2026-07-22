from datetime import datetime, timezone
from decimal import Decimal

from xtalflow.domain.crystal_workflow import AssignmentOrder, CrystalTarget, SelectedCrystal
from xtalflow.domain.fragment_screening import (
    Fragment, FragmentLibrary, build_fragment_screen_plan,
)
from xtalflow.domain.labwork import build_fragment_labworks, build_raw_crystal_labworks
from xtalflow.domain.plate_format import SWISSCI_MIDI_3_LENS
from xtalflow.domain.raw_crystal import build_raw_crystal_plan


def _crystal(target_count: int = 1) -> SelectedCrystal:
    selected_at = datetime.now(timezone.utc)
    return SelectedCrystal(
        "/rmserver/image.jpg", "2069", "A04a",
        tuple(
            CrystalTarget(
                f"target-{index + 1}", Decimal("0.125") + index,
                Decimal("-0.5") - index, selected_at,
            )
            for index in range(target_count)
        ),
        SWISSCI_MIDI_3_LENS.id,
        "/rmserver/image.jpg",
    )


def test_raw_labwork_uses_account_id_and_empty_soaking_fields() -> None:
    records = build_raw_crystal_labworks(
        build_raw_crystal_plan((_crystal(2),)), experiment_id="RawCrystal-1",
        protein_name="BRD4", username="remote-user", account_id="owner-42",
    )
    payload = records[0].to_payload()

    assert payload["name"] == "remote-user"
    assert payload["project_id"] == "owner-42"
    assert len(records) == 1
    assert payload["plate_x"] == 0.0
    assert payload["plate_y"] == 0.0
    assert payload["plate_imgpath"] == "/rmserver/image.jpg"
    assert payload["protein_name"] == "BRD4"
    assert payload["plate_type"] == "SwissCI-MRC-3d"
    assert payload["plate_code"] == "2069"
    assert payload["plate_well"] == "A04a"
    assert payload["crystal_no"] == 1
    assert payload["soak_id"] == ""
    assert payload["soak_smile"] == ""
    assert payload["soak_vol"] == 0.0


def test_fragment_labwork_has_one_record_per_image_drop() -> None:
    fragment = Fragment(
        "Vendor", "Library", "1", "CMP-1", "C2H6O", Decimal("46.07"),
        "CCO", Decimal("100"), "DMSO", "SOURCE", "A01",
    )
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), (_crystal(2),), Decimal("25"),
        AssignmentOrder.SELECTION,
    )
    records = build_fragment_labworks(
        plan, experiment_id="FragSC-1", protein_name="BRD4",
        username="fbdd", account_id="fbdd",
    )
    payload = records[0].to_payload()

    assert len(records) == 1
    assert payload["crystal_no"] == 1
    assert payload["plate_x"] == 0.0
    assert payload["plate_y"] == 0.0
    assert payload["soak_id"] == "CMP-1"
    assert payload["soak_smile"] == "CCO"
    assert payload["soak_vol"] == 25.0
    assert payload["plate_type"] == "SwissCI-MRC-3d"
    assert payload["project_id"] == "fbdd"
