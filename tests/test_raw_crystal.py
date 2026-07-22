from datetime import datetime, timedelta, timezone
from decimal import Decimal

from xtalflow.domain import SWISSCI_MIDI_3_LENS
from xtalflow.domain.crystal_workflow import AssignmentOrder, CrystalTarget, SelectedCrystal
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.domain.worksheets import build_shifter_worksheet


def _crystal(image: str, plate: str, well: str, selected_at: datetime) -> SelectedCrystal:
    return SelectedCrystal(
        image, plate, well,
        (CrystalTarget(f"target-{image}", Decimal("0.1"), Decimal("-0.2"), selected_at),),
        SWISSCI_MIDI_3_LENS.id,
    )


def test_raw_crystal_plan_defaults_to_target_selection_order() -> None:
    now = datetime.now(timezone.utc)
    first = _crystal("first", "2070", "A02c", now)
    second = _crystal("second", "2069", "A01a", now + timedelta(seconds=1))

    plan = build_raw_crystal_plan((second, first))

    assert [item.crystal.image_key for item in plan.selections] == ["first", "second"]
    assert len(build_shifter_worksheet(plan)) == 2


def test_raw_crystal_plan_can_sort_by_plate_and_well() -> None:
    now = datetime.now(timezone.utc)
    later_plate = _crystal("later", "2070", "B02a", now)
    earlier_plate = _crystal("earlier", "2069", "H12d", now + timedelta(seconds=1))

    plan = build_raw_crystal_plan(
        (later_plate, earlier_plate), AssignmentOrder.PLATE_WELL
    )

    assert [item.crystal.image_key for item in plan.selections] == ["earlier", "later"]
    assert build_shifter_worksheet(plan)[0].plate_id == "2069"


def test_multiple_soaking_positions_share_one_raw_crystal_shifter_row() -> None:
    now = datetime.now(timezone.utc)
    crystal = SelectedCrystal(
        "image", "2069", "A01a",
        (
            CrystalTarget("target-1", Decimal("0.1"), Decimal("0.2"), now),
            CrystalTarget("target-2", Decimal("0.3"), Decimal("0.4"),
                          now + timedelta(seconds=1)),
        ),
        SWISSCI_MIDI_3_LENS.id,
    )

    plan = build_raw_crystal_plan((crystal,))

    assert [item.target.target_id for item in plan.selections] == [
        "target-1", "target-2"
    ]
    assert len(plan.crystals) == 1
    assert len(build_shifter_worksheet(plan)) == 1
