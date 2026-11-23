"""Reading where an experiment's harvest stands, without sending anything."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

import pytest

from xtalflow.application.harvest_review import (
    HarvestReviewError,
    prepare_harvest_review,
    read_harvest,
)
from xtalflow.application.labwork_upload import FAILED, SUCCEEDED
from xtalflow.application.worksheet_export import SUCCEEDED as EXPORT_SUCCEEDED
from xtalflow.domain import crystal_selection_from_selected_crystals
from xtalflow.domain.condition_test import (
    Additive,
    ConditionTestDesign,
    build_condition_test_plan,
    new_series,
)
from xtalflow.domain.crystal_workflow import CrystalTarget, SelectedCrystal
from xtalflow.domain.harvest_record import HarvestOutcome
from xtalflow.domain.instruments import InstrumentOutput
from xtalflow.domain.mxlive import MxLiveLabwork
from xtalflow.domain.plan_lifecycle import WebDBUploadEvent, WorksheetExportEvent
from xtalflow.domain.plate_format import SWISSCI_MIDI_3_LENS
from xtalflow.domain.worksheets import SHIFTER_HEADER, shifter_worksheet_groups

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
EXPID = "PreTest-202611-BRD4-01"


def _plan(harvest_times: tuple[int, ...] = (0, 60)):
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    design = ConditionTestDesign(
        (dmso,), (new_series(dmso.id, (Decimal("10"),), harvest_times, 1),), Decimal("200")
    )
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2069", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal("0"), Decimal("0"),
                           NOW + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(len(harvest_times))
    )
    return build_condition_test_plan(
        design, crystal_selection_from_selected_crystals("plan", crystals)
    )


def _returned(rows, picked: bool = True, puck: str = "5C-0001") -> str:
    """The file as the SHIFTER gives it back: no header, 15 fields a row."""
    lines = []
    for index, row in enumerate(rows, start=1):
        values = list(row.values())
        if picked:
            values[6] = "OK: No comment:No comment"
            values[7] = f"XTal-{index}"
            values[8] = f"2026-11-17 17:0{index}:00.0"
            values[9] = f"2026-11-17 17:0{index}:30.0"
            values[11] = puck
            values[12] = str(index)
        lines.append(",".join(values))
    return "\r\n".join(lines) + "\r\n"


def _payload(plan) -> str:
    return json.dumps([
        {"plate_code": well.plate_code, "plate_well": well.well_address,
         "expri_id": EXPID, "protein_name": "BRD4", "name": "jjh"}
        for well in (a.selected_well for a in plan.assignments)
    ])


class _Store:
    def __init__(self, uploads, exports) -> None:
        self._uploads, self._exports = uploads, exports

    def list_webdb_uploads_for_experiment(self, experiment_id: str):
        return [item for item in self._uploads if item[0] == experiment_id][0][1]

    def list_worksheet_exports(self, revision_id: str):
        return self._exports


def _store(plan, *, status: str = SUCCEEDED, username: str = "jjh",
           paths=("/s1/f.csv",), payload_json: str | None = None):
    upload = WebDBUploadEvent(
        "u1", "rev-1", username, "7", "https://mxlive/upload_labworks/BL-5C/",
        NOW, status, len(plan.assignments),
        _payload(plan) if payload_json is None else payload_json,
    )
    export = WorksheetExportEvent(
        "e1", "rev-1", username, NOW, EXPORT_SUCCEEDED,
        tuple(InstrumentOutput("shifter1", path) for path in paths)
        + (InstrumentOutput("echo650", "/echo/f.csv"),),
    )
    return _Store([(EXPID, [upload])], [export])


def _records(plan, **filled: Any) -> Sequence[MxLiveLabwork]:
    return tuple(
        MxLiveLabwork.from_mapping({
            "expri_id": EXPID, "protein_name": "BRD4",
            "plate_code": assignment.selected_well.plate_code,
            "plate_well": assignment.selected_well.well_address,
            "harvest_time": None, "puck_name": None, "puck_nohole": None,
            **filled.get(assignment.selected_well.well_address, {}),
        })
        for assignment in plan.assignments
    )


def _read(files):
    """Reads what is there and raises for what is not, as the real one does."""
    def read(path: str) -> str:
        if path not in files:
            raise FileNotFoundError(2, "No such file or directory", path)
        return files[path]
    return read


def _review(plan, store, files, records, *, username: str = "jjh"):
    sources = prepare_harvest_review(
        EXPID, store, lambda _revision: plan, username=username
    )
    return read_harvest(sources, _read(files), lambda _expid: records)


def test_a_returned_worksheet_is_read_and_each_crystal_placed() -> None:
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    # One harvest group has come back; the other has not been run yet.
    files = {"/s1/h1.csv": _returned(groups[0][1]), "/s1/h2.csv": _returned(groups[1][1], picked=False)}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert review.revision_id == "rev-1"
    assert [row.outcome for row in review.rows] == [
        HarvestOutcome.WILL_RECORD, HarvestOutcome.NOT_PICKED,
    ]
    assert review.can_record and len(review.sendable) == 1
    assert review.summary() == "1 harvest to record · 1 not picked yet"
    assert dict(review.sources) == {"/s1/h1.csv": "read", "/s1/h2.csv": "read"}


def test_a_file_that_cannot_be_read_is_reported_and_stops_nothing_silently() -> None:
    """An unreadable file may be the one holding the contradiction.

    So it is named rather than skipped, and the person decides whether to go
    on without it.
    """
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {"/s1/h1.csv": _returned(groups[0][1])}
    store = _store(plan, paths=("/s1/h1.csv", "/s1/h2.csv"))

    def read(path: str) -> str:
        if path not in files:
            raise FileNotFoundError(2, "No such file or directory")
        return files[path]

    sources = prepare_harvest_review(EXPID, store, lambda _r: plan, username="jjh")
    review = read_harvest(sources, read, lambda _e: _records(plan))

    assert dict(review.sources)["/s1/h2.csv"] == "cannot be read"
    assert any("cannot be read" in note for note in review.warnings)


def test_a_file_that_is_not_this_revision_s_worksheet_is_set_aside() -> None:
    """The folder holds other experiments, and a legacy file looks the same.

    What identifies a file is its contents against the uploaded revision's
    snapshot, so a stranger is set aside quietly rather than refused loudly -
    the audit path alone is not proof of what is at the path now.
    """
    plan = _plan()
    stranger = "SwissCI-MRC-3d,9999,AM,H,12,d," + ",".join([""] * 9) + "\r\n"
    files = {"/s1/other.csv": stranger}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert dict(review.sources) == {"/s1/other.csv": "not a worksheet of this revision"}
    assert [row.outcome for row in review.rows] == [HarvestOutcome.NOT_PICKED] * 2


def test_an_experiment_with_no_successful_upload_cannot_be_reviewed() -> None:
    plan = _plan()
    store = _store(plan, status=FAILED)

    with pytest.raises(HarvestReviewError, match="no successful upload"):
        _review(plan, store, {}, _records(plan))


def test_only_the_account_that_uploaded_may_add_to_its_records() -> None:
    """A harvest belongs to the researcher's own MxLive records.

    The payload that proves who owns them was signed by that account, and a
    different account's key would write into somebody else's experiment.
    """
    plan = _plan()
    store = _store(plan, username="novorex")

    with pytest.raises(HarvestReviewError, match="novorex"):
        _review(plan, store, {}, _records(plan), username="jjh")


def test_the_two_stations_agreeing_is_not_a_conflict() -> None:
    """The same worksheet goes to both, so the same harvest comes back twice."""
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    same = _returned(groups[0][1])
    files = {"/s1/h1.csv": same, "/s2/h1.csv": same}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert review.report.conflicts == ()
    assert review.can_record


def test_the_two_stations_disagreeing_stops_the_whole_experiment() -> None:
    """Partial sending would leave somebody working out which half went."""
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {
        "/s1/h1.csv": _returned(groups[0][1], puck="5C-0001"),
        "/s2/h1.csv": _returned(groups[0][1], puck="ISA-007"),
    }
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert len(review.report.conflicts) == 1
    assert review.rows[0].outcome is HarvestOutcome.FILES_DISAGREE
    assert not review.can_record


def test_records_mxlive_already_holds_are_counted_and_left_alone() -> None:
    """Somebody else's tool may have recorded this harvest already.

    Then there is nothing to do, and pressing anything must send nothing - not
    because of what XtalFlow remembers, but because of what MxLive holds.
    """
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {"/s1/h1.csv": _returned(groups[0][1])}
    store = _store(plan, paths=tuple(files))
    first = plan.assignments[0].selected_well.well_address
    records = _records(plan, **{first: {
        "harvest_time": "2026-11-17 17:01:30.0", "puck_name": "5C-0001",
        "puck_nohole": 1,
    }})

    review = _review(plan, store, files, records)

    assert review.rows[0].outcome is HarvestOutcome.ALREADY_RECORDED
    assert not review.can_record
    assert review.summary() == (
        "0 harvests to record · 1 already recorded · 1 not picked yet"
    )


def test_a_well_missing_from_the_recorded_payload_is_not_sendable() -> None:
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {"/s1/h1.csv": _returned(groups[0][1])}
    # An upload whose payload cannot be read at all: nothing proves ownership.
    store = _store(plan, paths=tuple(files), payload_json="not json")

    review = _review(plan, store, files, _records(plan))

    assert review.rows[0].outcome is HarvestOutcome.NO_PAYLOAD
    assert not review.can_record


def test_one_disagreement_stops_the_wells_that_are_fine_too() -> None:
    """A conflict is not a problem of its own row.

    If two files disagree about one well, the files themselves are in doubt,
    and sending the wells they happen to agree on would leave somebody to work
    out afterwards which half went. MxLive cannot take a record back, so the
    whole experiment waits for a person to look.
    """
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {
        "/s1/h1.csv": _returned(groups[0][1], puck="5C-0001"),
        "/s2/h1.csv": _returned(groups[0][1], puck="ISA-007"),
        "/s1/h2.csv": _returned(groups[1][1], puck="5C-0002"),
    }
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert [row.outcome for row in review.rows] == [
        HarvestOutcome.FILES_DISAGREE, HarvestOutcome.WILL_RECORD,
    ]
    # The second well is perfectly good, and it still waits.
    assert review.sendable
    assert not review.can_record


class _StoreByRevision:
    """A store that answers for the revision it is asked about, as the real one does."""

    def __init__(self, uploads, exports_by_revision) -> None:
        self._uploads = uploads
        self._exports = exports_by_revision

    def list_webdb_uploads_for_experiment(self, experiment_id: str):
        return [item for item in self._uploads if item[0] == experiment_id][0][1]

    def list_worksheet_exports(self, revision_id: str):
        return self._exports.get(revision_id, ())


def test_a_split_upload_finds_the_worksheets_of_the_whole_revision() -> None:
    """The upload sits on a part; the worksheets were saved from the whole plate.

    A plate whose late harvest went to MxLive under its own experiment ID
    uploads a part of its finalized revision, while the ECHO and SHIFTER
    files were exported once, from the whole revision. Looking for the files
    under the part's own id finds none, and then every crystal reads as not
    picked yet - for ever, however many times the file is returned.
    """
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {
        "/s1/h1.csv": _returned(groups[0][1]),
        "/s1/h2.csv": _returned(groups[1][1]),
    }
    upload = WebDBUploadEvent(
        "u1", "part-1", "jjh", "7", "https://mxlive/upload_labworks/BL-5C/",
        NOW, SUCCEEDED, len(plan.assignments), _payload(plan),
    )
    exported_from_the_whole_plate = WorksheetExportEvent(
        "e1", "rev-1", "jjh", NOW, EXPORT_SUCCEEDED,
        tuple(InstrumentOutput("shifter1", path) for path in files)
        + (InstrumentOutput("echo650", "/echo/f.csv"),),
    )
    store = _StoreByRevision(
        [(EXPID, [upload])], {"rev-1": (exported_from_the_whole_plate,)}
    )

    # Asked for the part alone, nothing is found and nobody is picked.
    alone = read_harvest(
        prepare_harvest_review(
            EXPID, store, lambda _revision: plan, username="jjh"
        ),
        _read(files),
        lambda _expid: _records(plan),
    )

    assert [row.outcome for row in alone.rows] == [HarvestOutcome.NOT_PICKED] * 2

    # Told where the plate's worksheets are, every crystal is placed.
    whole = read_harvest(
        prepare_harvest_review(
            EXPID, store, lambda _revision: plan, username="jjh",
            worksheets_also_from=("rev-1",),
        ),
        _read(files),
        lambda _expid: _records(plan),
    )

    assert [row.outcome for row in whole.rows] == [HarvestOutcome.WILL_RECORD] * 2
    assert whole.can_record


def test_the_other_halfs_file_is_named_as_that_and_not_as_a_problem() -> None:
    """A correct split must not raise an attention note every time it is read.

    Each part reads all of the plate's SHIFTER files, so the other half's
    file always turns up. It is a correct file, in the right place, that
    nobody has to look into - and calling it "not a worksheet of this
    revision" sent somebody to check a file that was never wrong.
    """
    from xtalflow.application.harvest_review import ANOTHER_PART
    from xtalflow.application.planning_service import snapshot_for_part

    whole = _plan(harvest_times=(60, 720))
    groups = shifter_worksheet_groups(whole)
    files = {
        "/s1/soon.csv": _returned(groups[0][1]),
        "/s1/late.csv": _returned(groups[1][1]),
    }
    # This part is the early half; the late file belongs to the other one.
    early = [item for item in whole.assignments if item.harvest_minutes == 60]
    part = _plan(harvest_times=(60,))
    store = _store(part, paths=tuple(files))

    sources = prepare_harvest_review(
        EXPID, store, lambda _revision: part, username="jjh", plate_plan=whole
    )
    review = read_harvest(
        sources, _read(files), lambda _expid: _records(part)
    )
    states = dict(review.sources)

    assert states["/s1/late.csv"] == ANOTHER_PART
    assert states["/s1/soon.csv"] == "read"
    assert len(early) == 1
    # And without the plate it is the old answer, which is what reached the
    # screen as something to look into.
    plain = read_harvest(
        prepare_harvest_review(
            EXPID, store, lambda _revision: part, username="jjh"
        ),
        _read(files),
        lambda _expid: _records(part),
    )

    assert dict(plain.sources)["/s1/late.csv"] == "not a worksheet of this revision"
    assert snapshot_for_part is not None  # the split that makes this happen


def test_only_the_account_that_uploaded_may_add_to_its_records() -> None:
    """Refused before any file is opened, and named so it can be sorted out.

    MxLive ties a record to the account that created it, and this program
    writes with the researcher's own key. Adding a harvest from another
    account would either be rejected by MxLive or - worse, and this is why
    it is stopped here - create a second record for the same well under the
    wrong owner, which nobody can delete.

    It is not an error in the plate: the person is told whose it is, because
    on a beamline the answer is usually to go and ask them.
    """
    plan = _plan()
    store = _store(plan, username="gistjwk")

    with pytest.raises(HarvestReviewError) as raised:
        prepare_harvest_review(
            EXPID, store, lambda _revision: plan, username="jjh"
        )

    assert "gistjwk" in str(raised.value)
    assert "only that account" in str(raised.value)


def test_a_record_on_mxlive_the_revision_never_sent_stops_the_whole_experiment() -> None:
    """No per-well check can see it, which is why it stops everything.

    MxLive's update filter leaves out ``plate_code``, so the call that writes
    our A01a writes any record sharing that address - and a record of another
    plate under the same experiment ID is not one of our wells, so nothing
    asks about it. Whose it is does not matter: this experiment ID is then
    not the set of records this revision uploaded.
    """
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {"/s1/h1.csv": _returned(groups[0][1])}
    store = _store(plan, paths=tuple(files))
    stray = MxLiveLabwork.from_mapping({
        "expri_id": EXPID, "protein_name": "BRD4",
        "plate_code": "2070", "plate_well": "A01a",
        "harvest_time": None, "puck_name": None, "puck_nohole": None,
    })

    review = _review(plan, store, files, (*_records(plan), stray))

    assert review.strays == ("2070 A01a",)
    assert review.sendable and not review.can_record
    assert any("never sent" in note for note in review.warnings)


def test_the_records_the_revision_did_send_are_not_strays() -> None:
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    files = {"/s1/h1.csv": _returned(groups[0][1])}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert review.strays == () and review.can_record


def test_the_copy_nobody_worked_from_raises_no_warning_of_its_own() -> None:
    """The same worksheet goes to both stations, and only one is worked from.

    The other copy comes back as it was written, so it reports every well as
    unpicked. Said per file, that put a warning on a plate harvested to the
    last crystal - telling somebody nobody had reached a well while the row
    above said which puck it was in. Which wells no copy reports as picked is
    counted once, over all of them, and each says so in its own row.
    """
    plan = _plan()
    groups = shifter_worksheet_groups(plan)
    worked_from = "\r\n".join(
        _returned(group[1]).strip() for group in groups
    ) + "\r\n"
    untouched = "\r\n".join(
        _returned(group[1], picked=False).strip() for group in groups
    ) + "\r\n"
    files = {"/s1/h.csv": untouched, "/s2/h.csv": worked_from}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert [row.outcome for row in review.rows] == [
        HarvestOutcome.WILL_RECORD, HarvestOutcome.WILL_RECORD,
    ]
    assert review.summary() == "2 harvests to record"
    assert dict(review.sources) == {"/s1/h.csv": "read", "/s2/h.csv": "read"}
    assert review.warnings == ()


def test_three_harvest_groups_worked_from_one_station_warn_about_nothing() -> None:
    """The plate the beamline was looking at when it said this was wrong.

    Twenty wells in three harvest groups, every one of them picked at the
    second station, and the first station's three copies still as they were
    written. The table was right - it folds the stations together - while the
    warnings counted each file on its own and said twelve, four and four rows
    were not picked yet.
    """
    plan = _plan((0, 60, 960))
    groups = shifter_worksheet_groups(plan)
    files = {}
    for index, (_label, rows) in enumerate(groups, start=1):
        files[f"/s1/h{index}.csv"] = _returned(rows, picked=False)
        files[f"/s2/h{index}.csv"] = _returned(rows)
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert review.summary() == "3 harvests to record"
    assert review.warnings == ()
    assert set(dict(review.sources).values()) == {"read"}


def test_a_plate_split_between_the_two_stations_is_folded_into_one_answer() -> None:
    """One worksheet, one decision per well, either station.

    A plate can be worked from both: the first group at one station while the
    second is still soaking, the rest at the other. What is missing is what no
    station reports, not what each one is missing on its own.
    """
    plan = _plan((0, 60))
    groups = shifter_worksheet_groups(plan)
    files = {
        "/s1/h.csv": _returned(groups[0][1]) + _returned(groups[1][1], picked=False),
        "/s2/h.csv": _returned(groups[0][1], picked=False) + _returned(groups[1][1]),
    }
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert [row.outcome for row in review.rows] == [
        HarvestOutcome.WILL_RECORD, HarvestOutcome.WILL_RECORD,
    ]
    assert review.summary() == "2 harvests to record"
    assert review.warnings == ()


def test_a_file_in_a_layout_nothing_can_read_says_so_and_not_the_other_thing() -> None:
    """One sentence for both cost a whole station's harvest, and silently.

    Told "not a worksheet of this revision", nobody goes looking for a reader
    that cannot read the file - they go looking for the right file, and there
    is no right file: the instrument wrote this one and it is the only copy of
    its 62 harvests.
    """
    plan = _plan()
    header = ",".join(f"Column{index}" for index in range(15))
    row = "SwissCI-MRC-3d,1070,AM,A,1,a,OK,,arrived,departed,5,5C-0001,1,,"
    files = {"/s1/station.csv": f"{header}\r\n{row}\r\n"}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert dict(review.sources) == {
        "/s1/station.csv": "not in a layout XtalFlow can read"
    }


def test_a_harvest_sitting_in_results_beside_an_untouched_file_is_not_silent() -> None:
    """The shape that loses a harvest with nothing said at all.

    One station wrote its answer into a results/ subfolder on 2026-11-23,
    leaving our own worksheet untouched at the path we exported to. Read as it
    stands that is a plate nobody has picked yet, and every crystal of it
    reads as not picked with no note of any kind - which is what somebody
    would have been told had they looked between 15:37 and 16:49. The station
    writes into our worksheet now, confirmed at the beamline, so this is the
    shape and not the station: nothing here chose it, and nothing here can
    promise it will not come back.

    Named and not read: two runs of one revision were measured that day and
    they disagree well by well, so recording the one beside it could put a
    crystal in a puck it was taken out of again.
    """
    plan = _plan((0,))
    rows = shifter_worksheet_groups(plan)[0][1]
    files = {
        "/s1/f.csv": _returned(rows, picked=False),
        "/s1/results/f.csv": _returned(rows),
    }
    store = _store(plan, paths=("/s1/f.csv",))

    review = _review(plan, store, files, _records(plan))

    assert dict(review.sources) == {"/s1/f.csv": "read"}
    assert [row.outcome for row in review.rows] == [HarvestOutcome.NOT_PICKED]
    assert any("/s1/results/f.csv" in note for note in review.warnings), review.warnings


def test_an_unpicked_plate_with_nothing_beside_it_says_nothing_extra() -> None:
    """A plate mid-session is the normal state of a plate, not a warning."""
    plan = _plan((0,))
    rows = shifter_worksheet_groups(plan)[0][1]
    files = {"/s1/f.csv": _returned(rows, picked=False)}
    store = _store(plan, paths=tuple(files))

    review = _review(plan, store, files, _records(plan))

    assert review.warnings == ()


def test_an_address_on_two_plates_says_why_it_is_stopping_everything() -> None:
    """The one blocking reason that reached somebody as a grey button.

    A conflict names its two files and a stray names its records. This named
    nothing: on 2026-11-23 a five-plate experiment had 102 of its 187 well
    addresses on more than one plate, every one of its 250 harvests was held
    back, and the screen said "250 harvests to record" beside a button that
    did nothing. The only way to find out why was to read the module.
    """
    plan = _plan((0,))
    rows = shifter_worksheet_groups(plan)[0][1]
    files = {"/s1/f.csv": _returned(rows)}
    store = _store(plan, paths=tuple(files))
    # The same address on a second plate of the same experiment.
    sources = prepare_harvest_review(
        EXPID, store, lambda _revision: plan, username="jjh"
    )
    sources = replace(
        sources,
        expected=sources.expected + (("2070", sources.expected[0][1]),),
    )

    review = read_harvest(sources, _read(files), lambda _expid: _records(plan))

    assert not review.can_record
    assert any("on more than one plate" in note for note in review.warnings), (
        review.warnings
    )
    assert any(sources.expected[0][1] in note for note in review.warnings)
