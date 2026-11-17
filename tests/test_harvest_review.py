"""Reading where an experiment's harvest stands, without sending anything."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

import pytest

from xtalflow.application.harvest_review import (
    HarvestReviewError,
    review_harvest,
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


def _review(plan, store, files, records, *, username: str = "jjh"):
    return review_harvest(
        EXPID, store, lambda _revision: plan, lambda path: files[path],
        lambda _expid: records, username=username,
    )


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
    assert review.summary() == "1 to record · 1 not picked yet"
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

    review = review_harvest(
        EXPID, store, lambda _r: plan, read, lambda _e: _records(plan), username="jjh",
    )

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
    assert review.summary() == "0 to record · 1 already on MxLive · 1 not picked yet"


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
