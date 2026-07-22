from datetime import datetime, timezone
from decimal import Decimal

from xtalflow.domain.crystal_workflow import AssignmentOrder, CrystalTarget, SelectedCrystal
from xtalflow.domain.fragment_screening import (
    Fragment, FragmentLibrary, build_fragment_screen_plan,
)
from xtalflow.domain.labwork import build_fragment_labworks, build_raw_crystal_labworks
from xtalflow.domain.raw_crystal import build_raw_crystal_plan


def _crystal() -> SelectedCrystal:
    return SelectedCrystal(
        "/rmserver/image.jpg", "2069", "A04a",
        (CrystalTarget("target-1", Decimal("0.125"), Decimal("-0.5"),
                       datetime.now(timezone.utc)),),
        "swissci-midi-3-lens",
        "/rmserver/image.jpg",
    )


def test_raw_labwork_uses_account_id_and_empty_soaking_fields() -> None:
    records = build_raw_crystal_labworks(
        build_raw_crystal_plan((_crystal(),)), experiment_id="RawCrystal-1",
        protein_name="BRD4", username="remote-user", account_id="owner-42",
    )
    payload = records[0].to_payload()

    assert payload["name"] == "remote-user"
    assert payload["project_id"] == "owner-42"
    assert payload["plate_x"] == 0.125
    assert payload["plate_y"] == -0.5
    assert payload["plate_imgpath"] == "/rmserver/image.jpg"
    assert payload["soak_id"] == ""
    assert payload["soak_smile"] == ""
    assert payload["soak_vol"] == 0.0


def test_fragment_labwork_has_one_record_per_target_transfer() -> None:
    fragment = Fragment(
        "Vendor", "Library", "1", "CMP-1", "C2H6O", Decimal("46.07"),
        "CCO", Decimal("100"), "DMSO", "SOURCE", "A01",
    )
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), (_crystal(),), Decimal("25"),
        AssignmentOrder.SELECTION,
    )
    payload = build_fragment_labworks(
        plan, experiment_id="FragSC-1", protein_name="BRD4",
        username="fbdd", account_id="fbdd",
    )[0].to_payload()

    assert payload["crystal_no"] == 1
    assert payload["soak_id"] == "CMP-1"
    assert payload["soak_smile"] == "CCO"
    assert payload["soak_vol"] == 25.0
    assert payload["project_id"] == "fbdd"
