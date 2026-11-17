"""What a returned harvest would mean for each MxLive record."""

from __future__ import annotations

from typing import Any, Mapping

from xtalflow.domain.harvest_record import (
    PUCK_NAME_LIMIT,
    HarvestOutcome,
    plan_harvest_records,
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
    return {("2069", well): {"plate_well": well} for well in wells}


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
