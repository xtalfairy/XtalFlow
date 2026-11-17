"""Reading a SHIFTER worksheet back after the harvest.

The filled format has not been seen yet: no harvested file exists on this
machine, and what these tests feed in follows the one documented example, the
legacy XtalViewer sample record (` 2021-08-27 12:52:56.0`, puck `ISA-020`,
position `8`). Nothing here depends on that format being right - a time and a
puck are carried through as text - but the fixtures say where the guess is.
"""

from decimal import Decimal

from xtalflow.domain import SWISSCI_MIDI_3_LENS, crystal_selection_from_selected_crystals
from xtalflow.domain.fragment_screening import CrystalTarget, SelectedCrystal
from xtalflow.domain.shifter_results import (
    SHIFTER_HEADER,
    collect_harvest,
    expected_wells,
    read_shifter_results,
    wells_in_file,
)
from xtalflow.domain.worksheets import build_shifter_worksheet_for_selected_wells

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
HEADER = ",".join(SHIFTER_HEADER)


def _row(
    well: str, plate: str = "1070", time: str = "", puck: str = "",
    position: str = "", comment: str = "", arrival: str = "",
) -> str:
    """One worksheet line, as the SHIFTER returns it."""
    return ",".join((
        "SwissCI-MRC-3d", plate, "AM", well[0], str(int(well[1:3])), well[3],
        comment, "", arrival, time, "", puck, position, "", "",
    ))


def _file(*rows: str) -> str:
    return "\n".join((HEADER, *rows)) + "\n"


def _selection(wells: tuple[str, ...], plate: str = "1070"):
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", plate, well,
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0), NOW + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index, well in enumerate(wells)
    )
    return crystal_selection_from_selected_crystals("plan", crystals)


def _expected(wells: tuple[str, ...], plate: str = "1070"):
    selection = _selection(wells, plate)
    return expected_wells(build_shifter_worksheet_for_selected_wells(selection.wells))


def test_the_wells_a_worksheet_was_written_with_are_read_back_from_it() -> None:
    """Nothing in the file names the experiment, so the wells are the fingerprint."""
    wells = ("A01a", "A02a", "B12c")
    assert _expected(wells) == (("1070", "A01a"), ("1070", "A02a"), ("1070", "B12c"))
    written = _file(*(_row(well) for well in wells))
    assert wells_in_file(written) == _expected(wells)

    # A file that is not one of ours gives nothing rather than a guess.
    assert wells_in_file("Plate,Well\n1070,A01a\n") == ()


def test_a_row_is_a_harvest_only_with_both_a_time_and_a_puck() -> None:
    text = _file(
        _row("A01a", time=" 2026-11-17 09:12:05.0", puck="5C-0029", position="1",
             comment="OK: No comment:No comment"),
        _row("A02a"),
        _row("A03a", time=" 2026-11-17 09:15:40.0"),
        _row("A04a", puck="5C-0029"),
    )
    (crystal,), notes = read_shifter_results(text, "/smbmount/shifter1/jjh/PreTest.csv")

    assert crystal.well_address == "A01a"
    # Not reformatted: the time keeps the instrument's own shape. Only the
    # space the separator leaves behind is trimmed - the documented sample
    # reads " 2021-08-27 12:52:56.0" because nobody trimmed it.
    assert crystal.harvest_time == "2026-11-17 09:12:05.0"
    assert (crystal.puck_name, crystal.puck_position) == ("5C-0029", "1")
    assert crystal.comment == "OK: No comment:No comment"
    assert crystal.source.endswith("PreTest.csv")
    assert notes == ("PreTest.csv: 3 rows not picked yet.",)


def test_both_stations_and_later_saves_are_one_harvest_when_they_agree() -> None:
    """The same worksheet goes to both SHIFTERs and again on every save."""
    picked = _row("A01a", time=" 2026-11-17 09:12:05.0", puck="5C-0029", position="1")
    expected = _expected(("A01a", "A02a"))
    report = collect_harvest(expected, (
        ("/shifter1/jjh/PreTest.csv", _file(picked, _row("A02a"))),
        ("/shifter2/jjh/PreTest.csv", _file(picked, _row("A02a"))),
        ("/shifter1/jjh/PreTest_01.csv", _file(picked, _row("A02a"))),
    ))

    assert [crystal.well_address for crystal in report.harvested] == ["A01a"]
    assert report.not_harvested == ("1070 A02a",)
    assert report.conflicts == () and report.unexpected == ()
    assert report.can_be_recorded


def test_one_well_reported_two_ways_stops_the_whole_recording() -> None:
    """Neither value can be chosen here, and the wrong puck misdirects a beamtime."""
    expected = _expected(("A01a", "A02a"))
    report = collect_harvest(expected, (
        ("/shifter1/jjh/PreTest.csv", _file(
            _row("A01a", time=" 2026-11-17 09:12:05.0", puck="5C-0029", position="1"),
            _row("A02a", time=" 2026-11-17 09:20:00.0", puck="5C-0029", position="2"),
        )),
        ("/shifter2/jjh/PreTest.csv", _file(
            _row("A01a", time=" 2026-11-17 11:40:00.0", puck="5C-0031", position="4"),
        )),
    ))

    assert len(report.harvested) == 2
    (conflict,) = report.conflicts
    assert conflict == (
        "1070 A01a · PreTest.csv says 2026-11-17 09:12:05.0 5C-0029 position 1 · "
        "PreTest.csv says 2026-11-17 11:40:00.0 5C-0031 position 4"
    )
    assert not report.can_be_recorded


def test_a_row_for_a_well_this_plan_never_sent_is_reported_and_not_used() -> None:
    """A record MxLive does not hold would be created by sending it, and never removed."""
    report = collect_harvest(_expected(("A01a",)), (
        ("/shifter1/jjh/PreTest.csv", _file(
            _row("A01a", time=" 2026-11-17 09:12:05.0", puck="5C-0029", position="1"),
            _row("H12a", time=" 2026-11-17 09:30:00.0", puck="5C-0029", position="7"),
            _row("A01a", plate="2070", time=" 2026-11-17 09:40:00.0", puck="5C-0029",
                 position="8"),
        )),
    ))

    assert [crystal.well_address for crystal in report.harvested] == ["A01a"]
    assert report.unexpected == (
        "1070 H12a · 2026-11-17 09:30:00.0 · 5C-0029 position 7 · PreTest.csv · "
        "not in this plan",
        "2070 A01a · 2026-11-17 09:40:00.0 · 5C-0029 position 8 · PreTest.csv · "
        "not in this plan",
    )
    assert not report.can_be_recorded


def test_an_unreadable_well_is_named_rather_than_skipped_quietly() -> None:
    text = _file(
        "SwissCI-MRC-3d,1070,AM,A,x,a,,,, 2026-11-17 09:00:00.0,,5C-0029,1,,",
        _row("A01a", time=" 2026-11-17 09:12:05.0", puck="5C-0029", position="1"),
    )
    harvested, notes = read_shifter_results(text, "PreTest.csv")

    assert [crystal.well_address for crystal in harvested] == ["A01a"]
    assert notes == ("PreTest.csv: line 2: Axa is not a well address.",)


def test_nothing_harvested_cannot_be_recorded() -> None:
    report = collect_harvest(_expected(("A01a", "A02a")), (
        ("/shifter1/jjh/PreTest.csv", _file(_row("A01a"), _row("A02a"))),
    ))

    assert report.harvested == ()
    assert report.not_harvested == ("1070 A01a", "1070 A02a")
    assert not report.can_be_recorded
