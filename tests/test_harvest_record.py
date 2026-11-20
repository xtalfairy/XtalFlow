"""What a returned harvest would mean for each MxLive record."""

from __future__ import annotations

import json
from typing import Any, Mapping

import pytest

from xtalflow.domain.harvest_record import (
    PUCK_NAME_LIMIT,
    HarvestOutcome,
    harvest_writes,
    plan_harvest_records,
    stray_records,
)
from xtalflow.domain.mxlive import MxLiveLabwork
from xtalflow.domain.shifter_results import (
    HarvestConflict,
    HarvestedCrystal,
    HarvestReport,
)

EXPECTED = (("2069", "A01a"), ("2069", "A01c"), ("2069", "A02a"))


def _crystal(well: str, puck: str = "5C-0001", position: str = "1", **kwargs: Any):
    return HarvestedCrystal(
        "2069", well, kwargs.pop("time", "2026-11-17 17:17:00.0"), puck, position,
        kwargs.pop("comment", "OK: No comment:No comment"), "", "shifter2",
    )


def _record(well: str, **raw: Any) -> MxLiveLabwork:
    data: dict[str, Any] = {
        "expri_id": "PreTest-202611-BRD4-01", "protein_name": "BRD4",
        "plate_code": "2069", "plate_well": well,
        "harvest_time": None, "puck_name": None, "puck_nohole": None,
    }
    data.update(raw)
    return MxLiveLabwork.from_mapping(data)


def _payloads(*wells: str) -> Mapping[tuple[str, str], Mapping[str, Any]]:
    # The columns a record is found by are all here, because a payload that
    # is missing one of them would null it on MxLive - which is a refusal,
    # not a fixture detail. ``_record`` holds the same four.
    return {
        ("2069", well): {
            "expri_id": "PreTest-202611-BRD4-01", "protein_name": "BRD4",
            "plate_code": "2069", "plate_well": well,
        }
        for well in wells
    }


def test_a_well_with_a_harvest_and_an_empty_record_will_be_recorded() -> None:
    rows = plan_harvest_records(
        EXPECTED,
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a"), _record("A01c"), _record("A02a")),
        _payloads("A01a", "A01c", "A02a"),
    )

    assert [row.outcome for row in rows] == [
        HarvestOutcome.WILL_RECORD,
        HarvestOutcome.NOT_PICKED,
        HarvestOutcome.NOT_PICKED,
    ]
    # The rows are the revision's wells in the order it sent them, so a well
    # no file mentions is still a line: a plate half harvested is normal, and
    # "not picked yet" is the answer to the question somebody is asking.
    assert [row.well_address for row in rows] == ["A01a", "A01c", "A02a"]


def test_a_record_that_already_holds_this_harvest_is_not_sent_again() -> None:
    """MxLive decides what "already recorded" means, not our memory of it.

    That is what makes pressing again the recovery for a partial failure
    instead of a second write.
    """
    rows = plan_harvest_records(
        EXPECTED,
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record(
            "A01a", harvest_time="2026-11-17 17:17:00.0",
            puck_name="5C-0001", puck_nohole=1,
        ),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.ALREADY_RECORDED
    assert not rows[0].outcome.needs_attention


def test_a_record_holding_different_values_is_shown_and_left_alone() -> None:
    """Not a correction, and not a reason to stop the others.

    One uncorrectable difference in the first harvest group would otherwise
    block every later group from ever being recorded.
    """
    rows = plan_harvest_records(
        EXPECTED,
        HarvestReport(harvested=(_crystal("A01a"), _crystal("A01c", position="2"))),
        (
            _record(
                "A01a", harvest_time="2026-11-17 09:00:00.0",
                puck_name="ISA-003", puck_nohole=7,
            ),
            _record("A01c"),
        ),
        _payloads("A01a", "A01c"),
    )

    assert rows[0].outcome is HarvestOutcome.DIFFERS_ON_MXLIVE
    assert rows[0].on_mxlive is not None and rows[0].on_mxlive.puck_name == "ISA-003"
    assert rows[1].outcome is HarvestOutcome.WILL_RECORD


def test_the_position_compares_as_text_although_mxlive_returns_a_number() -> None:
    """``puck_nohole`` is an IntegerField, so it reads back as 1, not "1".

    A comparison that missed this would call every recorded row "different",
    and the person would be told nothing was ever written.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a", position="12"),)),
        (_record(
            "A01a", harvest_time="2026-11-17 17:17:00.0",
            puck_name="5C-0001", puck_nohole=12,
        ),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.ALREADY_RECORDED


def test_a_failed_pick_is_shown_with_the_instrument_s_words_and_not_sent() -> None:
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(attempted=(
            _crystal("A01a", puck="", position="", comment="FAIL: Clear:Melted"),
        )),
        (_record("A01a"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.PICK_FAILED
    assert rows[0].detail == "FAIL: Clear:Melted"
    assert not rows[0].outcome.is_sendable
    # Not something wrong - a crystal was reached and lost, which happens.
    assert not rows[0].outcome.needs_attention


def test_a_well_the_files_disagree_about_is_not_sendable() -> None:
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(
            harvested=(_crystal("A01a"),),
            conflicts=(HarvestConflict("2069", "A01a", "shifter1 says … shifter2 says …"),),
        ),
        (_record("A01a"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.FILES_DISAGREE
    assert rows[0].outcome.needs_attention


def test_a_well_with_no_record_on_mxlive_is_refused_not_created() -> None:
    """MxLive's write endpoint is an upsert and MxLive has no delete.

    A call that matches nothing creates a record, so the one thing this must
    never do is send a well whose record has gone.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.NO_RECORD
    assert rows[0].outcome.needs_attention


def test_two_records_sharing_a_well_address_are_refused() -> None:
    """MxLive's update filter leaves out the plate, so one call writes both."""
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a"), _record("A01a")),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.SEVERAL_RECORDS
    assert rows[0].detail == "2 records share this well address"
    assert rows[0].on_mxlive is None


def test_a_well_missing_from_the_local_audit_is_refused() -> None:
    """The payload we first sent is the only proof of who owns the record.

    Without it the address fields would have to be invented, and MxLive would
    accept them - it validates nothing.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a"),),
        {},
    )

    assert rows[0].outcome is HarvestOutcome.NO_PAYLOAD


def test_a_puck_name_too_long_for_mxlive_is_refused_here() -> None:
    """``puck_name`` is CharField(max_length=10) and nothing on the server checks.

    The SHIFTER's destination name is free text, and ``update`` bypasses the
    model's validation, so a long name is cut or rejected by the database -
    either way as fact, and MxLive cannot take a record back.
    """
    long_name = "5C-0001-SPARE"
    assert len(long_name) > PUCK_NAME_LIMIT
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a", puck=long_name),)),
        (_record("A01a"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.PUCK_NAME_TOO_LONG
    assert rows[0].detail == f"'{long_name}' is 13 characters"


def test_a_record_with_a_time_but_no_puck_counts_as_filled() -> None:
    """Half written by somebody else is not ours to complete."""
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", harvest_time="2025-04-03 18:00:22.8"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.DIFFERS_ON_MXLIVE


def test_what_a_harvest_write_changes_without_reading_it_is_stated() -> None:
    """Two fields cannot be carried forward, so they have to be disclosed.

    MxLive's write endpoint nulls any column it is not sent, and its read
    serializer does not return status or attachment - so a harvest write
    cannot preserve them, only replace them. 9 is Archived and 10 is Trashed,
    and XtalFlow sends 5, so recording a harvest can quietly bring back a
    record somebody put away. No check can catch it; the preview says it.
    """
    from xtalflow.domain.harvest_record import WRITTEN_BLIND

    assert len(WRITTEN_BLIND) == 2
    assert any("status" in item and "Active" in item for item in WRITTEN_BLIND)
    assert any("attachment" in item for item in WRITTEN_BLIND)


def _harvest(**kwargs: Any):
    return _crystal("A01a", **kwargs)


ORIGINAL = {
    "name": "jjh", "expri_id": "PreTest-202611-BRD4-01", "protein_name": "BRD4",
    "plate_code": "2069", "plate_well": "A01a", "status": 5, "attachment": "null",
    "soak_vol": 33, "soak_id": "DMSO_13%-0min", "project_id": "7",
    "staff_comments": "Uploaded by XtalFlow · Condition Test",
}


def test_the_whole_record_goes_back_with_the_harvest_added() -> None:
    """MxLive has no partial update: a column not sent becomes NULL.

    So each field comes from the place that still knows it - the address and
    the plan's columns from the payload this experiment was uploaded with,
    staff_comments from what MxLive holds now, the harvest from the file.
    """
    from xtalflow.domain.harvest_record import harvest_payload

    payload = harvest_payload(
        ORIGINAL,
        _record("A01a", staff_comments="Uploaded by XtalFlow · Condition Test"),
        _harvest(),
    )

    assert payload["harvest_time"] == "2026-11-17 17:17:00.0"
    assert payload["puck_name"] == "5C-0001"
    # An IntegerField there, so a number, not the file's text.
    assert payload["puck_nohole"] == 1
    # The instrument's own words, as every record on this beamline since 2022.
    assert payload["harvest_comment"] == "OK: No comment:No comment"
    # The plan's columns are the ones first uploaded, never invented.
    assert payload["soak_id"] == "DMSO_13%-0min"
    assert payload["plate_code"] == "2069" and payload["plate_well"] == "A01a"
    assert payload["status"] == 5 and payload["attachment"] == "null"
    # No rank is invented: absent, so NULL.
    assert "harvest_order" not in payload


def test_a_staff_comment_written_since_the_upload_is_kept_and_marked() -> None:
    """MxLive records no author, so a mark is the only way to tell ours apart.

    It goes beside the upload signature the history page already reads, not
    into harvest_comment, which holds the instrument's words.
    """
    from xtalflow.domain.harvest_record import HARVEST_MARK, harvest_payload

    payload = harvest_payload(
        ORIGINAL,
        _record(
            "A01a",
            staff_comments="Uploaded by XtalFlow · Condition Test · re-imaged 11-14",
        ),
        _harvest(),
    )

    assert payload["staff_comments"] == (
        f"Uploaded by XtalFlow · Condition Test · re-imaged 11-14 · {HARVEST_MARK}"
    )
    # Marked once, however many times a harvest is recorded.
    again = harvest_payload(ORIGINAL, _record("A01a", staff_comments=payload["staff_comments"]), _harvest())
    assert again["staff_comments"].count(HARVEST_MARK) == 1


def test_a_staff_comment_too_long_to_mark_keeps_its_words_instead() -> None:
    """The column holds 256 characters and the database cuts the rest.

    Somebody's own words are worth more than our mark.
    """
    from xtalflow.domain.harvest_record import (
        HARVEST_MARK,
        STAFF_COMMENTS_LIMIT,
        harvest_payload,
    )

    long_note = "crystal looked poor after soaking, see logbook. " * 5
    assert len(long_note) <= STAFF_COMMENTS_LIMIT
    payload = harvest_payload(
        ORIGINAL, _record("A01a", staff_comments=long_note), _harvest()
    )

    assert payload["staff_comments"] == long_note.strip()
    assert HARVEST_MARK not in payload["staff_comments"]


def test_a_puck_position_that_is_not_a_number_is_refused() -> None:
    """``puck_nohole`` is an IntegerField and MxLive validates nothing."""
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a", position="top"),)),
        (_record("A01a"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.PUCK_POSITION_NOT_A_NUMBER
    assert rows[0].outcome.needs_attention


def _sendable_rows(*wells: str, payloads=None):
    """Rows the plan says will be recorded, for the wells named."""
    report = HarvestReport(harvested=tuple(_crystal(well) for well in wells))
    return plan_harvest_records(
        tuple(("2069", well) for well in wells),
        report,
        tuple(_record(well) for well in wells),
        payloads if payloads is not None else _payloads(*wells),
    )


def test_a_harvest_write_carries_the_record_it_was_uploaded_with() -> None:
    """The columns MxLive is not sent become NULL, so all of them go.

    And they come from the payload this experiment was uploaded with, which
    is the only proof of who owns the record: MxLive validates nothing, so a
    write that invented them would be accepted and would quietly move the
    record to whatever it claimed.
    """
    owned = {
        ("2069", "A01a"): {
            "plate_code": "2069", "plate_well": "A01a",
            "expri_id": "PreTest-202611-BRD4-01", "protein_name": "BRD4",
            "soak_id": "BRD4_c1_60m", "project_id": "jjh", "status": 5,
        },
    }
    rows = _sendable_rows("A01a", payloads=owned)
    assert rows[0].outcome is HarvestOutcome.WILL_RECORD

    (write,) = harvest_writes(rows, owned)

    for column, value in owned[("2069", "A01a")].items():
        assert write[column] == value
    assert write["harvest_time"] == "2026-11-17 17:17:00.0"
    assert write["puck_name"] == "5C-0001"
    assert write["puck_nohole"] == 1
    # Any rank we invented would have to stay stable across presses, and a
    # file read later with an earlier time would shift ranks already written.
    assert "harvest_order" not in write


def test_nothing_about_a_failed_pick_is_sent() -> None:
    """Not even the instrument's own comment about losing the crystal.

    The comment is the one thing on that row somebody might want on the
    record, which is why it has to be refused deliberately: a record saying
    a crystal was handled when none was is a record this program will not
    correct.
    """
    lost = HarvestedCrystal(
        "2069", "A01c", "", "", "", "FAILED: crystal lost on mount", "", "shifter2",
    )
    report = HarvestReport(
        harvested=(_crystal("A01a"),), attempted=(lost,),
    )
    rows = plan_harvest_records(
        (("2069", "A01a"), ("2069", "A01c")),
        report,
        (_record("A01a"), _record("A01c")),
        _payloads("A01a", "A01c"),
    )
    outcomes = [row.outcome for row in rows]
    assert outcomes == [HarvestOutcome.WILL_RECORD, HarvestOutcome.PICK_FAILED]

    writes = harvest_writes([row for row in rows if row.outcome.is_sendable],
                            _payloads("A01a", "A01c"))

    assert len(writes) == 1
    assert "crystal lost" not in json.dumps(writes)
    assert all(write["plate_well"] != "A01c" for write in writes)


def test_a_row_that_must_not_be_sent_is_refused_rather_than_skipped() -> None:
    """Silently dropping it would send a different set than was agreed to.

    The preview is what the person said yes to. If a row could be dropped
    between the preview and the send, the count in the question would stop
    meaning anything.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", harvest_time="2026-11-17 09:00:00.0",
                 puck_name="5C-0009", puck_nohole=4),),
        _payloads("A01a"),
    )
    assert rows[0].outcome is HarvestOutcome.DIFFERS_ON_MXLIVE

    with pytest.raises(ValueError, match="must not be sent"):
        harvest_writes(rows, _payloads("A01a"))


def test_the_staff_comment_comes_from_what_mxlive_holds_now() -> None:
    """Staff may have written there since the upload.

    Sending the comment the record was uploaded with would undo whatever
    they wrote, and nobody would see it happen.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", staff_comments="looked cracked under the loop"),),
        _payloads("A01a"),
    )

    (write,) = harvest_writes(rows, _payloads("A01a"))

    assert write["staff_comments"].startswith("looked cracked under the loop")


def test_a_record_holding_only_a_comment_is_not_an_empty_one() -> None:
    """The one column a harvest write would overwrite with instrument text.

    Somebody writes "do not shoot" against a crystal; the write carries the
    SHIFTER's own words into the same column. Asking only about the time and
    the puck called this record unharvested, so the warning went.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", harvest_comment="cracked under the loop, do not shoot"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is not HarvestOutcome.WILL_RECORD
    with pytest.raises(ValueError, match="must not be sent"):
        harvest_writes(rows, _payloads("A01a"))


def test_a_record_holding_only_an_order_is_not_an_empty_one() -> None:
    """``harvest_order`` is the beamline's, and the write does not carry it.

    So a record with an order and nothing else was read as empty and the
    order became NULL - not a correction of ours that could be taken back,
    but the loss of somebody else's value.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", harvest_order=4),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is not HarvestOutcome.WILL_RECORD
    with pytest.raises(ValueError, match="must not be sent"):
        harvest_writes(rows, _payloads("A01a"))


def test_a_record_renamed_on_mxlive_is_refused_and_names_both_values() -> None:
    """The protein name is part of the upsert's lookup.

    Sending the old one would not correct the record: it would miss it and
    create a second one, and MxLive has no delete. Both values are shown
    because a refusal nobody can act on is only a dead end.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", protein_name="BRD4-BD1"),),
        _payloads("A01a"),
    )

    assert rows[0].outcome is HarvestOutcome.CHANGED_ON_MXLIVE
    assert "protein_name" in rows[0].detail
    assert "'BRD4-BD1'" in rows[0].detail and "'BRD4'" in rows[0].detail
    with pytest.raises(ValueError, match="must not be sent"):
        harvest_writes(rows, _payloads("A01a"))


def test_the_record_about_to_be_sent_is_checked_and_not_only_the_judgment() -> None:
    """The outcome was decided when the preview was drawn.

    What goes out is built again at the send, from the payloads handed in
    then. If those disagree with MxLive the write would create a record
    rather than add to one, whatever the row says.
    """
    rows = plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a"),),
        _payloads("A01a"),
    )
    assert rows[0].outcome is HarvestOutcome.WILL_RECORD

    renamed = {
        key: {**payload, "protein_name": "BRD4-BD1"}
        for key, payload in _payloads("A01a").items()
    }
    with pytest.raises(ValueError, match="protein_name"):
        harvest_writes(rows, renamed)


def test_a_record_the_revision_never_sent_is_named_as_a_stray() -> None:
    """Invisible to every per-well check, because it is nobody's well here.

    MxLive's update filter leaves out ``plate_code``, so writing our A01a
    writes any record that shares the address - including one of another
    plate that we never sent and cannot see.
    """
    records = (_record("A01a"), _record("A01a", plate_code="2070"))

    assert stray_records((("2069", "A01a"),), records) == ("2070 A01a",)
    assert stray_records((("2069", "A01a"), ("2070", "A01a")), records) == ()


def _one_row(record_raw: dict[str, Any], payload_extra: dict[str, Any]):
    payloads = {
        ("2069", "A01a"): {**_payloads("A01a")[("2069", "A01a")], **payload_extra}
    }
    return plan_harvest_records(
        (("2069", "A01a"),),
        HarvestReport(harvested=(_crystal("A01a"),)),
        (_record("A01a", **record_raw),),
        payloads,
    )[0]


def test_a_dose_mxlive_itself_cut_to_a_whole_number_is_not_a_change() -> None:
    """Measured: 17.5 nl was sent and 17.0 came back.

    The column is an integer field the endpoint fills with int(), while the
    migration made it a float. Refusing over that would lock every plate
    uploaded before doses were rounded - over a difference MxLive made.
    """
    row = _one_row({"soak_vol": 17.0}, {"soak_vol": 17.5})

    assert row.outcome is HarvestOutcome.WILL_RECORD


def test_a_dose_somebody_changed_on_mxlive_is_a_change() -> None:
    row = _one_row({"soak_vol": 38.0}, {"soak_vol": 33})

    assert row.outcome is HarvestOutcome.CHANGED_ON_MXLIVE
    assert "soak_vol" in row.detail


def test_the_same_number_written_differently_is_the_same_number() -> None:
    """``plate_x`` is sent as a float and stored as text.

    A 2025 record reads back "0", a record XtalFlow uploaded reads back
    "0.0", and ``crystal_no`` reads back as an integer. Comparing the text
    would have called all of those changed.
    """
    assert _one_row(
        {"plate_x": "0.0", "crystal_no": 7}, {"plate_x": 0.0, "crystal_no": 7},
    ).outcome is HarvestOutcome.WILL_RECORD
    assert _one_row(
        {"plate_x": "0", "crystal_no": 7}, {"plate_x": 0.0, "crystal_no": 7},
    ).outcome is HarvestOutcome.WILL_RECORD


def test_a_label_mysql_cut_to_fit_its_column_is_not_a_change() -> None:
    """``soak_id`` holds 60 characters and the write bypasses validation.

    A condition label built from several steps goes over, MySQL cuts it
    without a word, and what comes back is the record as it has been since
    the upload. It counts as the same only when it is exactly as long as the
    column and is how the sent value begins.
    """
    label = "DMSO_13p-0min_" + "x" * 60
    cut = label[:60]

    assert _one_row({"soak_id": cut}, {"soak_id": label}).outcome is (
        HarvestOutcome.WILL_RECORD
    )
    assert _one_row({"soak_id": cut[:-1]}, {"soak_id": label}).outcome is (
        HarvestOutcome.CHANGED_ON_MXLIVE
    )


def test_a_condition_renamed_on_mxlive_is_refused() -> None:
    row = _one_row({"soak_id": "DMSO_15p-0min"}, {"soak_id": "DMSO_13p-0min"})

    assert row.outcome is HarvestOutcome.CHANGED_ON_MXLIVE
    assert "'DMSO_15p-0min'" in row.detail and "'DMSO_13p-0min'" in row.detail


def test_a_column_the_upload_knows_nothing_about_is_refused() -> None:
    """A column added to MxLive after this payload was sent.

    Sending the payload would null it, and nulling what we never knew about
    is the same loss as reverting what we did.
    """
    row = _one_row({"plate_type": "SwissCI-MRC-3d"}, {})

    assert row.outcome is HarvestOutcome.CHANGED_ON_MXLIVE
    assert "plate_type" in row.detail


def test_a_column_empty_on_both_sides_is_not_a_change() -> None:
    """MxLive returns null, never an empty string."""
    row = _one_row({"soak_smile": None}, {"soak_smile": ""})

    assert row.outcome is HarvestOutcome.WILL_RECORD
