from decimal import Decimal

import pytest

from xtalflow.domain.echo_source import (
    SOURCE_PLATE_TYPES,
    allocate_source_wells,
    normalize_well,
    parse_wells,
    source_plate_type,
    suggest_wells,
)

PP_DMSO = source_plate_type("384PP_DMSO2")
LDV_1536 = source_plate_type("1536LDV_DMSO")


def test_the_two_lab_plates_are_described_with_their_working_ranges() -> None:
    assert {item.part_number for item in SOURCE_PLATE_TYPES} == {
        "001-14555 (PP-0200)", "001-6969 (LP-0400)"
    }
    assert (PP_DMSO.dead_ul, PP_DMSO.max_ul, PP_DMSO.well_format) == (Decimal("15"), Decimal("65"), 384)
    assert (LDV_1536.dead_ul, LDV_1536.max_ul, LDV_1536.well_format) == (
        Decimal("1"), Decimal("5.5"), 1536
    )
    with pytest.raises(ValueError, match="not a known source plate type"):
        source_plate_type("384LDV_DMSO")


def test_one_well_is_filled_with_dead_volume_transfers_and_a_ten_percent_margin() -> None:
    allocation = allocate_source_wells(PP_DMSO, [Decimal("10")] * 32 + [Decimal("25")] * 32, ["A1"])

    (well,) = allocation.wells
    assert well.transferred_nl == Decimal("1120")
    # 15 µL + 1.12 µL × 1.1 = 16.232 µL, rounded up to 16.3 µL.
    assert well.fill_ul == Decimal("16.3")
    assert allocation.missing == 0
    assert allocation.instruction() == (
        "Fill A1 with 16.3 µL — 15 µL dead volume + 1.12 µL to transfer + 10 % margin."
    )


def test_transfers_move_to_the_next_well_before_one_would_go_below_its_dead_volume() -> None:
    # A 1536LDV well can give (5.5 − 1) / 1.1 = 4.09 µL.
    transfers = [Decimal("500")] * 20
    allocation = allocate_source_wells(LDV_1536, transfers, ["A1", "A2"])

    assert allocation.wells_needed == 3
    assert allocation.missing == 1
    assert [well.transferred_nl for well in allocation.wells] == [
        Decimal("4000"), Decimal("4000"), Decimal("2000")
    ]
    # 1 µL + 4 µL × 1.1 = 5.4 µL, within the 5.5 µL maximum.
    assert [well.fill_ul for well in allocation.wells] == [
        Decimal("5.4"), Decimal("5.4"), Decimal("3.2")
    ]
    assert all(well.fill_ul <= LDV_1536.max_ul for well in allocation.wells)
    assert allocation.transfer_wells[:9] == ("A1",) * 8 + ("A2",)
    assert allocation.transfer_wells[-1] is None  # the third well is not entered yet
    assert allocation.instruction() == (
        "Fill A1 with 5.4 µL, A2 with 5.4 µL and ? with 3.2 µL — "
        "each 1 µL dead volume + its transfers + 10 % margin."
    )


def test_a_transfer_larger_than_one_well_can_give_is_refused() -> None:
    with pytest.raises(ValueError, match="more than a 1536LDV · DMSO well can give"):
        allocate_source_wells(LDV_1536, [Decimal("5000")], ["A1"])


def test_wells_are_checked_against_the_plate_they_are_on() -> None:
    assert normalize_well("p24", 384) == "P24"
    assert normalize_well("A01", 384) == "A1"
    assert normalize_well("AF48", 1536) == "AF48"
    for address, well_format in (("Q1", 384), ("A25", 384), ("AG1", 1536), ("A49", 1536), ("1A", 384)):
        with pytest.raises(ValueError, match=f"is not a well on a {well_format}-well plate"):
            normalize_well(address, well_format)
    assert parse_wells("a1, A2 a1", 384) == ("A1", "A2")
    # Wells are used down each column: A1 → P1, then A2.
    assert suggest_wells(384, "O1", 3, taken={"A2"}) == ("P1", "B2", "C2")


def test_wells_entered_beyond_what_is_needed_are_named_as_not_needed() -> None:
    allocation = allocate_source_wells(PP_DMSO, [Decimal("10")], ["A1", "A2", "A3"])
    assert allocation.unused_wells == ("A2", "A3")
    assert allocation.instruction().endswith("10 % margin. A2 and A3 are not needed.")



def test_next_free_wells_continue_after_the_last_used_well_and_skip_recorded_ones() -> None:
    from datetime import datetime, timezone

    from xtalflow.domain.echo_source import PlateRecordEntry, next_free_wells, well_history

    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    entries = (
        PlateRecordEntry("used", ("A1", "B1", "C1"), "kim", now, "PreTest-202609-BRD4-01", "DMSO"),
        PlateRecordEntry("skipped", ("E1",), "lee", now, reason="Dust or debris"),
        PlateRecordEntry("skipped", ("P1",), "lee", now, reason="Damaged"),
    )
    # Gaps before the last used well are not gone back to; skipped wells are passed over.
    assert next_free_wells(384, entries, 4) == ("D1", "F1", "G1", "H1")
    assert next_free_wells(384, entries, 2, exclude={"D1"}) == ("F1", "G1")
    assert next_free_wells(384, (), 2) == ("A1", "B1")
    history = well_history(entries)
    assert history["B1"].describe("B1", "SOLVENT-01") == (
        "SOLVENT-01 B1 was used by kim on 2026-09-18 (PreTest-202609-BRD4-01)"
    )
    assert history["E1"].describe("E1", "SOLVENT-01") == (
        "SOLVENT-01 E1 was skipped by lee on 2026-09-18: Dust or debris"
    )
