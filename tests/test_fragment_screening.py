from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
    parse_library_rows,
)


def fragment(number: int) -> Fragment:
    return Fragment(
        vendor="Vendor",
        library="Library",
        number=str(number),
        compound_id=f"CMP-{number}",
        formula="C2H6O",
        molecular_weight=Decimal("46.07"),
        smiles="CCO",
        concentration_mm=Decimal("100"),
        solvent="DMSO",
        source_plate="SRC-1",
        source_well=f"A{number:02d}",
    )


def crystal(name: str, selected_at: datetime, target_count: int) -> SelectedCrystal:
    return SelectedCrystal(
        image_key=name,
        destination_plate="1070",
        destination_well="A01a",
        targets=tuple(
            CrystalTarget(f"{name}-{index}", Decimal(index), Decimal(index), selected_at)
            for index in range(target_count)
        ),
    )


def test_plan_assigns_fragments_in_crystal_selection_order() -> None:
    now = datetime.now(UTC)
    library = FragmentLibrary("Library", (fragment(1), fragment(2)))

    plan = build_fragment_screen_plan(
        library,
        (crystal("later", now + timedelta(seconds=1), 1), crystal("first", now, 1)),
        Decimal("10"),
    )

    assert [item.crystal.image_key for item in plan.assignments] == ["first", "later"]
    assert [item.fragment.compound_id for item in plan.assignments] == ["CMP-1", "CMP-2"]


def test_plan_splits_each_crystal_volume_across_its_own_targets() -> None:
    now = datetime.now(UTC)
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment(1),)),
        (crystal("image", now, 3),),
        Decimal("20"),
    )

    volumes = [transfer.volume_nl for transfer in plan.assignments[0].transfers]
    assert volumes == [Decimal("5.0"), Decimal("5.0"), Decimal("10.0")]
    assert plan.assignments[0].total_volume_nl == Decimal("20.0")


@pytest.mark.parametrize("volume", [Decimal("0"), Decimal("6")])
def test_plan_rejects_invalid_transfer_volume(volume: Decimal) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        build_fragment_screen_plan(
            FragmentLibrary("Library", (fragment(1),)),
            (crystal("image", now, 1),),
            volume,
        )


def test_plan_rejects_zero_volume_transfers() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="needs at least"):
        build_fragment_screen_plan(
            FragmentLibrary("Library", (fragment(1),)),
            (crystal("image", now, 3),),
            Decimal("5"),
        )


def test_plan_rejects_too_few_fragments() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="enough fragments"):
        build_fragment_screen_plan(
            FragmentLibrary("Library", (fragment(1),)),
            (crystal("one", now, 1), crystal("two", now, 1)),
            Decimal("10"),
        )


def test_library_rows_are_one_based_data_rows_not_fragment_numbers() -> None:
    library = FragmentLibrary("Library", (fragment(8), fragment(15), fragment(20)))

    selected = library.select_rows("1, 3")

    assert [item.number for item in selected.fragments] == ["8", "20"]


def test_library_row_ranges_preserve_requested_file_order() -> None:
    assert parse_library_rows("1-3, 5, 7~8", 8) == (1, 2, 3, 5, 7, 8)


@pytest.mark.parametrize("selection", ["", "0", "3-1", "1,1", "1-4", "x"])
def test_invalid_library_row_selection_is_rejected(selection: str) -> None:
    with pytest.raises(ValueError):
        parse_library_rows(selection, 3)


def test_plan_can_reassign_fragments_in_plate_and_well_order() -> None:
    now = datetime.now(UTC)
    library = FragmentLibrary("Library", (fragment(1), fragment(2), fragment(3)))
    crystals = (
        SelectedCrystal(
            "selected-first",
            "20",
            "A01a",
            (CrystalTarget("t1", Decimal(0), Decimal(0), now),),
        ),
        SelectedCrystal(
            "selected-second",
            "3",
            "A10a",
            (CrystalTarget("t2", Decimal(0), Decimal(0), now + timedelta(seconds=1)),),
        ),
        SelectedCrystal(
            "selected-third",
            "3",
            "A02c",
            (CrystalTarget("t3", Decimal(0), Decimal(0), now + timedelta(seconds=2)),),
        ),
    )

    plan = build_fragment_screen_plan(
        library,
        crystals,
        Decimal("10"),
        AssignmentOrder.PLATE_WELL,
    )

    assert [item.crystal.image_key for item in plan.assignments] == [
        "selected-third",
        "selected-second",
        "selected-first",
    ]
    assert [item.fragment.compound_id for item in plan.assignments] == [
        "CMP-1",
        "CMP-2",
        "CMP-3",
    ]
