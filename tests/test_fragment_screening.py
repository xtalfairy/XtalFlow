from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from dataclasses import replace

from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
    library_locations_that_are_one_well,
    library_values_that_cannot_be_written,
    library_well_key,
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
    now = datetime.now(timezone.utc)
    library = FragmentLibrary("Library", (fragment(1), fragment(2)))

    plan = build_fragment_screen_plan(
        library,
        (crystal("later", now + timedelta(seconds=1), 1), crystal("first", now, 1)),
        Decimal("10"),
    )

    assert [item.selected_well.image_key for item in plan.assignments] == [
        "first", "later"
    ]
    assert [item.fragment.compound_id for item in plan.assignments] == ["CMP-1", "CMP-2"]


def test_plan_splits_each_crystal_volume_equally_across_its_own_targets() -> None:
    now = datetime.now(timezone.utc)
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment(1), fragment(2))),
        (crystal("image", now, 3),),
        Decimal("22.5"),
    )

    volumes = [transfer.volume_nl for transfer in plan.assignments[0].transfers]
    assert volumes == [Decimal("7.5"), Decimal("7.5"), Decimal("7.5")]
    assert plan.assignments[0].total_volume_nl == Decimal("22.5")
    assert [item.compound_id for item in plan.unused_fragments] == ["CMP-2"]


def test_volume_that_cannot_be_shared_equally_is_refused_with_alternatives() -> None:
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError) as captured:
        build_fragment_screen_plan(
            FragmentLibrary("Library", (fragment(1),)),
            (crystal("image", now, 3),),
            Decimal("25"),
        )

    assert "25 nL cannot be split equally between 3 positions" in str(captured.value)
    assert "Use 22.5 nL or 30 nL" in str(captured.value)


@pytest.mark.parametrize("volume", [Decimal("0"), Decimal("6")])
def test_plan_rejects_invalid_transfer_volume(volume: Decimal) -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        build_fragment_screen_plan(
            FragmentLibrary("Library", (fragment(1),)),
            (crystal("image", now, 1),),
            volume,
        )


def test_plan_rejects_zero_volume_transfers() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="cannot be split equally"):
        build_fragment_screen_plan(
            FragmentLibrary("Library", (fragment(1),)),
            (crystal("image", now, 3),),
            Decimal("5"),
        )


def test_crystals_the_fragments_do_not_reach_are_left_out_of_the_plan() -> None:
    """Fewer fragments than crystals is a smaller experiment, not a mistake."""
    now = datetime.now(timezone.utc)

    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment(1),)),
        (crystal("one", now, 1), crystal("two", now, 1)),
        Decimal("10"),
    )

    assert len(plan.assignments) == 1
    assert plan.assignments[0].selected_well.image_key == "one"
    # The second crystal keeps its place in the selection and gets nothing.
    assert [well.image_key for well in plan.unassigned_wells] == ["two"]
    assert plan.unused_fragments == ()
    # Everything downstream reads the assignments, so the crystal that got no
    # fragment is in no worksheet and no upload: not dispensed into, not
    # harvested, not registered.
    assert [item.selected_well.image_key for item in plan.assignments] == ["one"]


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
    now = datetime.now(timezone.utc)
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

    assert [item.selected_well.image_key for item in plan.assignments] == [
        "selected-third",
        "selected-second",
        "selected-first",
    ]
    assert [item.fragment.compound_id for item in plan.assignments] == [
        "CMP-1",
        "CMP-2",
        "CMP-3",
    ]


def test_the_nearest_volume_every_well_can_share() -> None:
    """2.5 nL steps, and one volume that works for all the selected wells."""
    from xtalflow.domain.fragment_screening import volume_that_splits

    # Three positions: 25 nL does not divide, 22.5 does.
    assert volume_that_splits(Decimal("25"), [3]) == (Decimal("22.5"), True)
    # Wells with different position counts need their common multiple.
    assert volume_that_splits(Decimal("25"), [2, 3]) == (Decimal("15.0"), True)
    # A volume that already works is returned unchanged.
    assert volume_that_splits(Decimal("30"), [3]) == (Decimal("30.0"), True)
    # Below one step for each position there is nothing to round down to, so
    # the smallest one above is offered instead, and said to be above.
    assert volume_that_splits(Decimal("5"), [3]) == (Decimal("7.5"), False)


def test_a_library_value_that_becomes_an_identifier_is_named() -> None:
    """Nothing checked these between the file and MxLive.

    The compound ID becomes the soak_id, which is in MxLive for years and
    cannot be corrected; the plate and well travel in the ECHO worksheet as
    well. Nobody types any of them, so refusing a bad name where names are
    typed does not reach this path at all. The shipped libraries are clean,
    so this is about a library somebody made themselves - and those exist.
    """
    clean = fragment(1)

    assert library_values_that_cannot_be_written(clean) == ()

    assert library_values_that_cannot_be_written(
        replace(clean, compound_id="50%/DMSO")
    ) == (("compound ID", "50%/DMSO"),)
    assert library_values_that_cannot_be_written(
        replace(clean, compound_id="PEG 400 (v/v)")
    )[0][0] == "compound ID"
    assert library_values_that_cannot_be_written(
        replace(clean, source_plate="SRC 1")
    ) == (("source plate", "SRC 1"),)
    # Not the shape of a well at all, which no character check would catch.
    assert library_values_that_cannot_be_written(
        replace(clean, source_well="NOTAWELL")
    ) == (("source well", "NOTAWELL"),)
    # SMILES is never checked: a structure is written with dots, equals
    # signs and brackets, and that is what it is for.
    assert library_values_that_cannot_be_written(
        replace(clean, smiles="CC(=O)C=1C=CC.O")
    ) == ()


def test_one_well_spelled_two_ways_is_one_well() -> None:
    """A1 and A01 loaded as two wells, so two compounds could come from one.

    The library's own check compares the spellings, and a file written by
    hand is not consistent about them. Two compounds against one well means
    two records in MxLive naming it, and the second transfer drawing from a
    well the first already emptied.
    """
    first = replace(fragment(1), source_plate="P1", source_well="A1")
    same = replace(fragment(2), source_plate="p1", source_well="A01")
    elsewhere = replace(fragment(3), source_plate="P2", source_well="B2")

    clashes = library_locations_that_are_one_well(
        FragmentLibrary("hand-made", (first, same, elsewhere))
    )

    assert len(clashes) == 1
    assert "CMP-1 at P1 A1 and CMP-2 at p1 A01 are the same well" in clashes[0]
    assert library_locations_that_are_one_well(
        FragmentLibrary("tidy", (first, elsewhere))
    ) == ()

    # Spelled exactly where a finalized revision is rebuilt, though: a rule
    # tightened afterwards must not refuse to record work already done.
    FragmentLibrary("as finalized", (first, same, elsewhere))

    assert library_well_key("a01") == library_well_key("A1") == "A1"
    assert library_well_key("NOT A WELL") is None

