"""Reading a SHIFTER worksheet back after the harvest.

The fixtures are the real thing. These lines were read off
/smbmount/shifter1 at BL-5C on 2026-11-17, from a worksheet XtalFlow wrote on
2026-10-19 and the SHIFTER rewrote in place, with the account, experiment id
and protein name left out. The 2025-06 FAIL line comes from a legacy file in
the same folder.

Two things the real file settled, both of which the first version of this
reader got wrong:
- the SHIFTER **drops the header line**, so a returned file starts with data;
- its row order cannot be assumed to be ours, so wells are matched by key and
  compared as a set, never by position.
"""

from decimal import Decimal

from xtalflow.domain import SWISSCI_MIDI_3_LENS, crystal_selection_from_selected_crystals
from xtalflow.domain.fragment_screening import CrystalTarget, SelectedCrystal
from xtalflow.domain.shifter_results import (
    SHIFTER_HEADER,
    collect_harvest,
    expected_wells,
    file_holds_these_wells,
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
    crystal_id: str = "", duration: str = "",
) -> str:
    """One worksheet line, in the shape the SHIFTER returns."""
    return ",".join((
        "SwissCI-MRC-3d", plate, "AM", well[0], str(int(well[1:3])), well[3],
        comment, crystal_id, arrival, time, duration, puck, position, "", "",
    ))


# Exactly as they came off the instrument.
REAL_PICKED = (
    "SwissCI-MRC-3d,2184,AM,A,1,a,OK: No comment:No comment,XTal-1,"
    "2026-10-19 16:03:53.3,2026-10-19 16:07:29.9,00:03:36.5,ISA-003,1,,"
)
REAL_PICKED_SECOND = (
    "SwissCI-MRC-3d,2184,AM,A,7,a,OK: No comment:No comment,XTal-2,"
    "2026-10-19 16:07:33.1,2026-10-19 16:09:39.4,00:02:06.2,ISA-003,2,,"
)
REAL_FAILED = (
    "SwissCI-MRC-3d,2184,AM,C,10,d,FAIL: No comment:Melted,,"
    "2025-06-26 17:00:23.8,2025-06-26 17:00:39.3,00:00:15.4,,,,"
)
REAL_INTERRUPTED = (
    "SwissCI-MRC-3d,2184,AM,B,1,a,,,2026-10-19 15:38:57.5,,,,,,"
)
REAL_UNPICKED = "SwissCI-MRC-3d,2184,AM,C,1,a,,,,,,,,,"


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
    assert file_holds_these_wells(_expected(wells), written)

    # Compared as a set: the SHIFTER rewrites the file and its row order has
    # not been checked against a copy of what we wrote.
    shuffled = _file(_row("B12c"), _row("A01a"), _row("A02a"))
    assert wells_in_file(shuffled) != _expected(wells)
    assert file_holds_these_wells(_expected(wells), shuffled)

    # A well missing or added means it is not this revision's worksheet.
    assert not file_holds_these_wells(_expected(wells), _file(_row("A01a"), _row("A02a")))
    assert not file_holds_these_wells(
        _expected(wells), _file(*(_row(w) for w in (*wells, "H12a")))
    )

    # A file that is not one of ours gives nothing rather than a guess.
    assert wells_in_file("Plate,Well\n1070,A01a\n") == ()
    assert read_shifter_results("Plate,Well\n1070,A01a\n", "other.csv").notes == (
        "other.csv: not a worksheet XtalFlow wrote.",
    )


def test_the_file_the_shifter_returns_is_read_without_its_header() -> None:
    """The real file starts with data: the SHIFTER rewrote ours and dropped the header."""
    returned = "\r\n".join((
        REAL_PICKED, REAL_PICKED_SECOND, REAL_INTERRUPTED, REAL_UNPICKED
    )) + "\r\n"
    read = read_shifter_results(returned, "/smbmount/shifter1/jjh/FragSC.csv")

    assert [crystal.well_address for crystal in read.harvested] == ["A01a", "A07a"]
    first = read.harvested[0]
    # Carried through as the instrument wrote it, not reformatted.
    assert first.harvest_time == "2026-10-19 16:07:29.9"
    assert first.arrival_time == "2026-10-19 16:03:53.3"
    assert (first.puck_name, first.puck_position) == ("ISA-003", "1")
    assert first.comment == "OK: No comment:No comment"
    assert first.plate_code == "2184"
    assert first.source.endswith("FragSC.csv")
    # Started and not finished is not a harvest, and not a failed pick either.
    assert [crystal.well_address for crystal in read.attempted] == []
    # Unpicked rows are kept and not said: the same worksheet goes to both
    # stations, and the copy nobody worked from reports every well this way.
    assert read.unpicked == (("2184", "B01a"), ("2184", "C01a"))
    assert read.notes == ()

    # A header, when there is one, is read and not taken for data.
    with_header = _file(REAL_PICKED, REAL_UNPICKED)
    assert [c.well_address for c in read_shifter_results(with_header).harvested] == ["A01a"]


def test_a_pick_that_failed_is_kept_apart_from_a_harvest() -> None:
    """FAIL rows have times and no puck: the only record the crystal was lost."""
    read = read_shifter_results("\n".join((REAL_PICKED, REAL_FAILED)), "FragSC.csv")

    assert [crystal.well_address for crystal in read.harvested] == ["A01a"]
    (failed,) = read.attempted
    assert failed.well_address == "C10d"
    assert failed.comment == "FAIL: No comment:Melted"
    assert failed.harvest_time == "2025-06-26 17:00:39.3"
    assert failed.puck_name == ""


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
    assert conflict.key == ("1070", "A01a")
    assert str(conflict) == (
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
    read = read_shifter_results(text, "PreTest.csv")

    assert [crystal.well_address for crystal in read.harvested] == ["A01a"]
    assert read.notes == ("PreTest.csv: line 2: Axa is not a well address.",)


def test_nothing_harvested_cannot_be_recorded() -> None:
    report = collect_harvest(_expected(("A01a", "A02a")), (
        ("/shifter1/jjh/PreTest.csv", _file(_row("A01a"), _row("A02a"))),
    ))

    assert report.harvested == ()
    assert report.not_harvested == ("1070 A01a", "1070 A02a")
    assert not report.can_be_recorded


def test_one_well_address_on_two_plates_refuses_the_whole_recording() -> None:
    """MxLive's update filter leaves the plate out, so writing one writes both.

    Read from the installed source, remote/views.py 686-701: AddLabwork finds
    a record by project, experiment, protein, plate and well, then applies the
    update filtered on everything but the plate - and the dict it applies
    holds every column, the plate code among them.

    So a harvest written onto one of two records sharing a well address does
    not merely land on both. The other record is overwritten with this one,
    its own plate code included, until the two rows are identical and the
    five-column lookup cannot tell them apart again. There is no delete, so
    whichever plate that was is gone.
    """
    expected = _expected(("A01a",)) + _expected(("A01a", "A02a"), plate="2070")
    picked = _row("A01a", time="2026-11-17 09:12:05.0", puck="ISA-003", position="1")
    report = collect_harvest(expected, (("/shifter1/jjh/FragSC.csv", _file(picked)),))

    assert report.shared_addresses == ("A01a is on 1070, 2070",)
    assert not report.can_be_recorded
    # One plate each, and the same wells are fine.
    plain = collect_harvest(
        _expected(("A01a", "A02a")), (("/shifter1/jjh/FragSC.csv", _file(picked)),)
    )
    assert plain.shared_addresses == () and plain.can_be_recorded


def test_the_results_are_taken_from_whichever_file_the_operator_loaded() -> None:
    """At BL-5C the original stayed empty and the harvests were in split files.

    PreTest-202611-TCTP-01 went out as one worksheet and was also split by
    harvest group into _H1_1h00m, _H2_2h00m and _H3_16h00m. The operator
    loaded the split files, so those came back filled while the original kept
    its header and its empty rows. A well therefore appears twice - empty in
    one file, picked in another - and the filled row is the one that counts.
    """
    first = (
        "SwissCI-MRC-3d,2192,AM,A,4,c,OK: No comment:No comment,XTal-1,"
        "2026-11-13 18:25:34.9,2026-11-13 18:26:43.3,00:01:08.3,5C-0051,1,,"
    )
    second = (
        "SwissCI-MRC-3d,2192,AM,B,4,c,OK: No comment:No comment,XTal-1,"
        "2026-11-14 09:48:02.1,2026-11-14 09:49:10.7,00:01:08.6,5C-0051,7,,"
    )
    wells = ("A04c", "B04c", "C04c")
    expected = _expected(wells, plate="2192")
    report = collect_harvest(expected, (
        # The original, as XtalFlow wrote it and as it stayed: header, no picks.
        ("/shifter2/jjh/PreTest.csv", _file(*(_row(w, plate="2192") for w in wells))),
        ("/shifter2/jjh/PreTest_H1_1h00m.csv", first + "\r\n"),
        ("/shifter2/jjh/PreTest_H3_16h00m.csv", second + "\r\n"),
    ))

    assert [(c.well_address, c.puck_position) for c in report.harvested] == [
        ("A04c", "1"), ("B04c", "7")
    ]
    assert [c.source.rsplit("/", 1)[-1] for c in report.harvested] == [
        "PreTest_H1_1h00m.csv", "PreTest_H3_16h00m.csv"
    ]
    # An empty row never covers a filled one, whichever file is read first.
    assert report.not_harvested == ("2192 C04c",)
    assert report.conflicts == () and report.unexpected == ()
    assert report.can_be_recorded

    # The split files hold a third of the wells each, so only their union is
    # this revision's worksheet: no single one of them is.
    assert not file_holds_these_wells(expected, first)


def test_a_split_worksheet_is_recognised_as_one_this_revision_sent() -> None:
    """A condition test harvested at several times goes out as a file per time.

    Each of those holds a fraction of the wells, so the whole-worksheet check
    rejects every one of them. What identifies a file is being either the
    whole worksheet or exactly one of its harvest groups.
    """
    from xtalflow.domain.condition_test import (
        Additive,
        ConditionTestDesign,
        build_condition_test_plan,
        new_series,
    )
    from xtalflow.domain.shifter_results import (
        file_is_one_of,
        worksheets_a_revision_sent,
    )
    from xtalflow.domain.worksheets import shifter_worksheet_groups

    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    design = ConditionTestDesign(
        (dmso,), (new_series(dmso.id, (Decimal("10"),), (0, 60), 1),), Decimal("200")
    )
    plan = build_condition_test_plan(design, _selection(("A01a", "A02a"), plate="2069"))
    groups = shifter_worksheet_groups(plan)
    assert [minute for minute, _rows in groups] == [0, 60]

    candidates = worksheets_a_revision_sent(plan)
    for _minute, rows in groups:
        one_group = _file(*(
            _row(f"{row.plate_row}{int(row.plate_column):02d}{row.subwell}", plate=row.plate_id)
            for row in rows
        ))
        assert not file_holds_these_wells(expected_wells(
            build_shifter_worksheet_for_selected_wells(plan.selection.wells)
        ), one_group)
        assert file_is_one_of(candidates, one_group)

    # The whole worksheet is still recognised: a revision exported before the
    # split went out that way, and the audit records paths, not their shape.
    whole = _file(*(_row(well, plate="2069") for well in ("A01a", "A02a")))
    assert file_is_one_of(candidates, whole)

    # Half a group, or wells from two groups and not a whole worksheet, is not
    # a file XtalFlow wrote.
    assert not file_is_one_of(candidates, _file(_row("A01a", plate="2069"), _row("H12a", plate="2069")))


# A real pre-harvest row from this beamline's 2022 files: the legacy exporter
# wrote only the six columns it filled, where XtalFlow writes all fifteen.
REAL_LEGACY_UNPICKED = "SwissCI-MRC-3d,768,AM,A,1,a"
REAL_LEGACY_PICKED = (
    "SwissCI-MRC-3d,768,AM,A,2,c,OK: No comment:No comment,XTal-1,"
    "2022-03-15 13:00:15.4,2022-03-15 13:02:31.0,00:02:15.5,5C-0006,1,,"
)


def test_a_row_that_stops_after_the_columns_we_write_is_read_as_not_picked() -> None:
    """A short row says nothing happened to that well, not that the file is bad.

    My first rule was "every line has exactly fifteen fields", and the 2022
    files on this beamline break it: the legacy exporter stopped after the six
    columns it filled. Refusing the whole file for that would throw away the
    harvest in the rows the instrument did fill.
    """
    from xtalflow.domain.shifter_results import read_shifter_results

    read = read_shifter_results(
        f"{REAL_LEGACY_UNPICKED}\r\n{REAL_LEGACY_PICKED}\r\n", "legacy"
    )

    assert [(row.plate_code, row.well_address, row.puck_name, row.puck_position)
            for row in read.harvested] == [("768", "A02c", "5C-0006", "1")]
    assert read.unpicked == (("768", "A01a"),)
    assert read.notes == ()


def test_a_row_wider_than_the_header_refuses_the_file() -> None:
    """A layout nobody here has seen is not guessed at.

    Reading it positionally would put somebody else's column where we expect a
    puck name or a time, and that would be recorded on MxLive as fact.
    """
    from xtalflow.domain.shifter_results import read_shifter_results

    read = read_shifter_results(f"{REAL_LEGACY_PICKED},EXTRA\r\n", "wider")

    assert read.harvested == ()
    assert read.notes == ("wider: not a worksheet XtalFlow wrote.",)


# The whole of a file this beamline's SHIFTER 2 returned on 2026-11-17, from a
# worksheet XtalFlow wrote with ExternalComment filled. Four rows, all
# attempted: two picked, two failed. Kept verbatim - every surprise in it was
# a surprise.
REAL_RETURNED_FILE = (
    "SwissCI-MRC-3d,2069,AM,A,1,a,OK: Clear:No comment,XTal-1,"
    "2026-11-17 17:16:55.4,2026-11-17 17:17:00.0,00:00:04.6,5C-0001,1,,"
    "XF_XFTEST-202611-MARK-01_r1_c001\r\n"
    "SwissCI-MRC-3d,2069,AM,A,1,c,FAIL: Clear:Melted,,"
    "2026-11-17 17:17:03.6,2026-11-17 17:17:07.4,00:00:03.8,,,,\r\n"
    "SwissCI-MRC-3d,2069,AM,A,1,d,OK: No comment:No comment,XTal-2,"
    "2026-11-17 17:17:10.6,2026-11-17 17:17:11.2,00:00:00.6,5C-0001,2,,"
    "XF_XFTEST-202611-MARK-01_r1_c003\r\n"
    "SwissCI-MRC-3d,2069,AM,A,2,a,FAIL: No comment:No comment,,"
    "2026-11-17 17:17:14.6,2026-11-17 17:17:17.7,00:00:03.1,,,,"
    "XF_XFTEST-202611-MARK-01_r1_c004_LEN0040\r\n"
)


def test_the_whole_of_a_file_the_instrument_returned() -> None:
    """Read end to end, against the file rather than against my idea of it.

    Three things in here were not what the earlier fixtures showed:

    - the middle field of Comment is not always "No comment". This file has
      ``OK: Clear:No comment`` and ``FAIL: Clear:Melted``, so a reader that
      looked for the literal string would call a picked crystal unpicked;
    - a failed row carries all three times but no puck and no position, so
      time alone does not mean a crystal was stored;
    - ExternalComment came back byte for byte on picked, failed and empty
      rows alike, and nowhere near Comment.
    """
    from xtalflow.domain.shifter_results import read_shifter_results

    read = read_shifter_results(REAL_RETURNED_FILE, "shifter2")

    assert [(row.well_address, row.puck_name, row.puck_position, row.harvest_time)
            for row in read.harvested] == [
        ("A01a", "5C-0001", "1", "2026-11-17 17:17:00.0"),
        ("A01d", "5C-0001", "2", "2026-11-17 17:17:11.2"),
    ]
    # Attempted and failed: shown to the person, never sent to MxLive.
    assert [(row.well_address, row.comment) for row in read.attempted] == [
        ("A01c", "FAIL: Clear:Melted"),
        ("A02a", "FAIL: No comment:No comment"),
    ]
    assert all(not row.puck_name and not row.puck_position for row in read.attempted)
    assert read.notes == ()


def test_the_column_we_fill_for_ourselves_is_not_read_as_data() -> None:
    """A marker in ExternalComment must change nothing about the harvest.

    The instrument preserves that column, so XtalFlow could write a marker
    there to say which experiment and revision a file came from. Whether it
    should is open; that it must never reach a harvest value is not.
    """
    from xtalflow.domain.shifter_results import read_shifter_results

    with_marker = read_shifter_results(REAL_RETURNED_FILE, "shifter2")
    without = read_shifter_results(
        REAL_RETURNED_FILE.replace("XF_XFTEST-202611-MARK-01_r1_c001", "")
        .replace("XF_XFTEST-202611-MARK-01_r1_c003", "")
        .replace("XF_XFTEST-202611-MARK-01_r1_c004_LEN0040", ""),
        "shifter2",
    )

    assert with_marker == without


def test_the_header_is_recognised_whichever_way_the_instrument_spells_it() -> None:
    """Measured on this beamline: both PositionSubWell and PositionSubwell.

    Of 502 result files, 288 carry a header line and the two spellings are
    mixed among them. The column order is identical, so a header the match
    missed was read as a data row and every harvest still landed in the right
    place - nothing was ever lost to this. What it produced was a note saying
    the header "is not a well address", on a line that is the header, and the
    notes are the only place somebody looks to decide whether a harvest needs
    attention. One that cries wolf costs more than it looks.
    """
    header = ";" + ",".join(SHIFTER_HEADER)
    row = "SwissCI-MRC-3d,2069,AM,A,1,a,OK,,arrived,departed,5,5C-0001,1,,"
    for spelling in (
        header,
        header.replace("SubWell", "Subwell"),
        header.upper(),
    ):
        report = collect_harvest(
            (("2069", "A01a"),), (("shifter2/x.csv", f"{spelling}\n{row}"),)
        )
        assert len(report.harvested) == 1, spelling
        assert report.notes == (), spelling
        assert report.harvested[0].comment == "OK"


def test_a_file_with_no_header_at_all_is_still_read() -> None:
    """214 of the 502 files on this beamline have none, and that is not new.

    Kept beside the test above because the two cases are read by the same
    branch for different reasons: a missing header means every line is data,
    while a header spelled another way used to mean the first line was data
    too. Only one of those was ever correct.
    """
    report = collect_harvest(
        (("2069", "A01a"),),
        ((
            "shifter2/x.csv",
            "SwissCI-MRC-3d,2069,AM,A,1,a,OK,,arrived,departed,5,5C-0001,1,,",
        ),),
    )

    assert len(report.harvested) == 1
    assert report.notes == ()
