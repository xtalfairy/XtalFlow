"""What a harvest read from the SHIFTER would mean for each MxLive record.

This decides nothing about sending. It answers, for one crystal at a time,
"may this be recorded, and if not, why not" - so a person can look at the whole
experiment before anything irreversible happens.

Every refusal here is about a property of MxLive's one write endpoint, read
from its source on 2026-11-17:

- it is an upsert. A call that matches no record **creates** one
  (``get_or_create``), and MxLive has no delete. So a well the revision never
  sent must never be sent, and neither must a well whose record has gone.
- its update filter leaves out ``plate_code`` although its lookup includes it.
  Two records sharing an experiment, protein and well address are both written
  by one call, so an experiment that sends one address from two plates is
  refused whole.
- it builds its field dict with ``info.get(...)`` per column, so a column that
  is not sent becomes ``NULL``. Recording a harvest therefore rewrites the
  whole record, and every field we do not carry forward is destroyed.
- it validates nothing, and ``update`` bypasses the model's validation, so a
  value too long for its column is the database's problem. ``puck_name`` is
  ``CharField(max_length=10)`` and the SHIFTER's destination name is free
  text, so a long one is refused here rather than silently cut there.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .mxlive import MxLiveLabwork
from .shifter_results import HarvestReport, HarvestedCrystal

__all__ = [
    "HARVEST_MARK",
    "STAFF_COMMENTS_LIMIT",
    "harvest_payload",
    "harvest_writes",
    "HarvestOutcome",
    "WRITTEN_BLIND",
    "HarvestRow",
    "PUCK_NAME_LIMIT",
    "plan_harvest_records",
]

PUCK_NAME_LIMIT = 10
"""MxLive's ``Labwork.puck_name`` is ``CharField(max_length=10)``."""

STAFF_COMMENTS_LIMIT = 256
"""MxLive's ``Labwork.staff_comments`` is ``CharField(max_length=256)``."""

WRITTEN_BLIND = (
    "MxLive never returns a record's status, so recording a harvest sets it "
    "to Active. A record somebody archived or put in the trash would come "
    "back as Active, and nothing here can see that it had changed.",
    "MxLive never returns a record's attachment either, and recording a "
    "harvest replaces it.",
)
"""What a harvest write changes without being able to read it first.

MxLive's write endpoint builds its column dict with ``info.get(...)`` per
column, so a column that is not sent becomes ``NULL``: every field has to be
carried forward. But its read serializer (``Labwork.json_dict``) returns 21
columns and leaves out ``status`` and ``attachment``, and no other endpoint
returns them. So those two cannot be carried forward - only overwritten with
what XtalFlow sends on an upload, which is ``5`` (Active) and the text
``"null"``.

This is the one thing in the harvest write that cannot be made safe by
checking first, so it is said out loud in the preview instead. The same is
already true of every upload; a harvest write is just the first time it
happens after beamline staff have had the record in their hands.
"""


class HarvestOutcome(Enum):
    """What would happen to one crystal's record, in the person's words.

    Every one of these says "harvest" where it means the harvest, and
    "record" where it means the record the sample list created. The two are
    different deliveries to the same server and they were not distinguished
    here: a row reading "already on MxLive" is true of every record by this
    point - the upload put it there - so it was read as "yes, I uploaded
    that" when it meant "the harvest of it is already recorded".
    """

    WILL_RECORD = "harvest will be recorded"
    ALREADY_RECORDED = "harvest already recorded"
    DIFFERS_ON_MXLIVE = "a different harvest on MxLive; not changed"
    NOT_PICKED = "not picked yet"
    PICK_FAILED = "pick failed; not sent"
    FILES_DISAGREE = "the returned files disagree about this well"
    NO_RECORD = "no record on MxLive to add a harvest to"
    SEVERAL_RECORDS = "more than one record on MxLive"
    NO_PAYLOAD = "this experiment's upload is not in the local audit"
    PUCK_NAME_TOO_LONG = "the puck name is too long for MxLive"
    PUCK_POSITION_NOT_A_NUMBER = "the puck position is not a number"

    @property
    def is_sendable(self) -> bool:
        return self is HarvestOutcome.WILL_RECORD

    @property
    def needs_attention(self) -> bool:
        """Whether something is wrong, as opposed to simply not ready.

        A crystal nobody has reached yet, and a pick that failed, are both the
        normal state of a plate mid-session. The rest are not.
        """
        return self not in (
            HarvestOutcome.WILL_RECORD,
            HarvestOutcome.ALREADY_RECORDED,
            HarvestOutcome.NOT_PICKED,
            HarvestOutcome.PICK_FAILED,
        )


@dataclass(frozen=True)
class HarvestRow:
    """One crystal, as the preview shows it."""

    plate_code: str
    well_address: str
    outcome: HarvestOutcome
    # The condition as MxLive names it, from the payload this experiment was
    # uploaded with. A table of wells without it says little about a condition
    # test, where the whole point is which well had which treatment.
    condition: str = ""
    # What the files say, when they say anything.
    harvest: HarvestedCrystal | None = None
    # What MxLive holds now, when it holds a record.
    on_mxlive: MxLiveLabwork | None = None
    detail: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.plate_code, self.well_address)

    def describe(self) -> str:
        line = f"{self.plate_code} {self.well_address}: {self.outcome.value}"
        return f"{line} ({self.detail})" if self.detail else line


def plan_harvest_records(
    expected: Sequence[tuple[str, str]],
    report: HarvestReport,
    records: Sequence[MxLiveLabwork],
    payloads: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[HarvestRow, ...]:
    """One row per well the revision sent, in the order it sent them.

    ``expected`` comes from the uploaded revision rather than from the files,
    so a well whose row never came back is still listed - "not picked yet" is
    information, and a plate half harvested is the normal state of a plate
    mid-session. Wells the files hold that the revision did not send are not
    rows here at all; they are in ``report.unexpected`` and they refuse the
    whole read, because sending one would create a record nobody can delete.
    """
    harvested = {row.key: row for row in report.harvested}
    attempted = {row.key: row for row in report.attempted}
    conflicts = {row.key for row in report.conflicts}
    rows: list[HarvestRow] = []
    for key in expected:
        plate_code, well = key
        found = [
            record for record in records
            if (record.plate_code or "", record.plate_well or "") == key
        ]
        harvest = harvested.get(key)
        rows.append(HarvestRow(
            plate_code, well,
            _outcome(key, harvest, attempted, conflicts, found, payloads),
            str(payloads.get(key, {}).get("soak_id") or ""),
            harvest,
            found[0] if len(found) == 1 else None,
            _detail(key, harvest, attempted, found),
        ))
    return tuple(rows)


def _outcome(
    key: tuple[str, str],
    harvest: HarvestedCrystal | None,
    attempted: Mapping[tuple[str, str], HarvestedCrystal],
    conflicts: set[tuple[str, str]],
    found: Sequence[MxLiveLabwork],
    payloads: Mapping[tuple[str, str], Mapping[str, Any]],
) -> HarvestOutcome:
    # Asked in the order the person would ask it: is there anything to say
    # about this well, and only then may it be said.
    if key in conflicts:
        return HarvestOutcome.FILES_DISAGREE
    if harvest is None:
        if key in attempted:
            return HarvestOutcome.PICK_FAILED
        return HarvestOutcome.NOT_PICKED
    if len(found) > 1:
        return HarvestOutcome.SEVERAL_RECORDS
    if not found:
        return HarvestOutcome.NO_RECORD
    record = found[0]
    if _has_harvest(record):
        if _same_harvest(record, harvest):
            return HarvestOutcome.ALREADY_RECORDED
        return HarvestOutcome.DIFFERS_ON_MXLIVE
    if key not in payloads:
        return HarvestOutcome.NO_PAYLOAD
    if len(harvest.puck_name) > PUCK_NAME_LIMIT:
        return HarvestOutcome.PUCK_NAME_TOO_LONG
    if not _position_number(harvest.puck_position):
        return HarvestOutcome.PUCK_POSITION_NOT_A_NUMBER
    return HarvestOutcome.WILL_RECORD


def _position_number(text: str) -> int | None:
    """The puck position as MxLive's ``IntegerField`` needs it.

    The instrument writes it as text and every file seen holds 1 to 12, but
    the column is an integer and MxLive validates nothing: anything else would
    reach the database as it stands. Refused here instead.
    """
    try:
        return int(text.strip())
    except (AttributeError, ValueError):
        return None


def _has_harvest(record: MxLiveLabwork) -> bool:
    """Whether MxLive already holds a harvest for this record.

    An unharvested record comes back with these as ``null``, not as empty
    text, and a record with a time but no puck would be a half-written one
    somebody needs to look at, so either counts as filled.
    """
    return bool(record.harvest_time) or bool(record.puck_name)


def _same_harvest(record: MxLiveLabwork, harvest: HarvestedCrystal) -> bool:
    """Whether what MxLive holds is what this file says.

    ``harvest_time`` is a ``CharField`` that MxLive neither parses nor
    reformats - a 2025 record reads back as ``2025-04-03 18:00:22.8``, the
    SHIFTER's own string - so times compare as text. ``puck_nohole`` is an
    ``IntegerField`` and comes back as a number, so the position compares as
    text on both sides after the number is made text again.
    """
    return (
        (record.harvest_time or "") == harvest.harvest_time
        and (record.puck_name or "") == harvest.puck_name
        and _position_text(record) == harvest.puck_position
    )


def _position_text(record: MxLiveLabwork) -> str:
    value = record.raw.get("puck_nohole")
    return "" if value is None else str(value)


def _detail(
    key: tuple[str, str],
    harvest: HarvestedCrystal | None,
    attempted: Mapping[tuple[str, str], HarvestedCrystal],
    found: Sequence[MxLiveLabwork],
) -> str:
    if harvest is None:
        failed = attempted.get(key)
        # The instrument's own words, shown and never sent.
        return failed.comment if failed else ""
    if len(found) > 1:
        return f"{len(found)} records share this well address"
    if len(harvest.puck_name) > PUCK_NAME_LIMIT:
        return f"{harvest.puck_name!r} is {len(harvest.puck_name)} characters"
    return ""


HARVEST_MARK = "Harvest by XtalFlow"
"""Added to ``staff_comments`` so a harvest can be told from a staff entry.

MxLive records no author for any field, so the only way a reader - or
XtalFlow itself, later, from another computer - can tell who wrote a harvest
is a mark left in a field that reads back. ``staff_comments`` already carries
``Uploaded by XtalFlow · …`` and the history page reads that signature
(``beamline_history.py:98``), so the harvest joins it there rather than in
``harvest_comment``, which belongs to the instrument's own words.
"""


def harvest_payload(
    original: Mapping[str, Any],
    record: MxLiveLabwork,
    harvest: HarvestedCrystal,
) -> dict[str, Any]:
    """The whole record as it must be sent to add one harvest to it.

    MxLive's write endpoint builds its column dict with ``info.get(...)`` per
    column, so every column it is not sent becomes ``NULL``. There is no
    partial update: adding a harvest means sending the record back whole.

    So each field comes from the place that still knows it:

    - the address and the plan's own columns from ``original``, the payload
      this experiment was uploaded with. That is the only proof of who owns
      the record, and MxLive validates nothing, so an invented one would be
      accepted;
    - ``staff_comments`` from what MxLive holds now, because staff may have
      written there since, and sending the original would undo it. The
      harvest mark is appended to it, once;
    - the harvest itself from the returned worksheet. ``harvest_comment``
      carries the instrument's own comment, as every record on this beamline
      since 2022 does; leaving it out would make ours the only ones without
      it.

    ``harvest_order`` is deliberately absent, and so becomes ``NULL``. Any
    rank XtalFlow invented would have to stay stable across presses, and a
    file read later with an earlier time would shift ranks already written -
    which cannot be corrected.

    ``status`` and ``attachment`` come from ``original`` because MxLive does
    not return them at all; see ``WRITTEN_BLIND``.
    """
    payload = dict(original)
    payload["staff_comments"] = _marked(record.raw.get("staff_comments"))
    payload["harvest_time"] = harvest.harvest_time
    payload["harvest_comment"] = harvest.comment
    payload["puck_name"] = harvest.puck_name
    payload["puck_nohole"] = _position_number(harvest.puck_position)
    payload.pop("harvest_order", None)
    return payload


def harvest_writes(
    rows: Sequence[HarvestRow],
    payloads: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """The records to send so that MxLive holds every harvest already reached.

    Only a row that will be recorded, and that is checked here rather than
    assumed. Every other outcome is either nothing to send or something that
    must not be sent, and two of them matter enough to name:

    - a pick that failed. The instrument writes its own comment about what
      happened to the crystal, and that comment is the one thing on this
      screen that a person might want on the record. It is not sent: nothing
      about a failed pick goes to MxLive, because the record would then say
      a crystal was handled when none was, and a record this program has
      written to is one it will not correct.
    - a harvest MxLive already holds with other values. Sending ours would
      overwrite what the beamline wrote, which cannot be undone.

    The order is the order of the plate, so the preview and what is sent
    cannot drift apart.
    """
    writes: list[dict[str, Any]] = []
    for row in rows:
        if not row.outcome.is_sendable:
            raise ValueError(f"{row.describe()} must not be sent")
        original = payloads.get(row.key)
        if original is None or row.on_mxlive is None or row.harvest is None:
            # WILL_RECORD is not reachable without all three, so arriving
            # here is a fault in this program, not a state of the plate.
            raise ValueError(
                f"{row.describe()} is missing what it would have to be sent with"
            )
        writes.append(harvest_payload(original, row.on_mxlive, row.harvest))
    return tuple(writes)


def _marked(staff_comments: Any) -> str:
    """The staff comment with the harvest mark appended, where it fits.

    The column holds 256 characters and the database would cut whatever is
    over. Somebody's own words are worth more than our mark, so a comment
    long enough to be cut keeps its words and goes unmarked.
    """
    text = str(staff_comments or "").strip()
    if HARVEST_MARK in text:
        return text
    if not text:
        return HARVEST_MARK
    marked = f"{text} · {HARVEST_MARK}"
    return marked if len(marked) <= STAFF_COMMENTS_LIMIT else text
