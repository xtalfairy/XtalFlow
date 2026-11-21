"""Our write, against a stand-in that does what MxLive's own view does.

The view was read on 2026-11-20 from the deployment the beamline runs, which
its administrator confirmed is the live code: ``mxlive/remote/views.py`` 633 to
707 of ``mxlive-install-kr-latest-261017.tar.gz``. ``AddLabwork`` is
reimplemented here line for line - not as the endpoint was imagined, but as it
is written - so that what this program sends can be driven through it without
asking a production database to answer the question.

What it reproduces, and why each part matters to us:

- every column comes from ``info.get(name)``, with no default and no
  conversion, so a column we leave out and a column we send as ``None`` are
  the same thing to it, and both land as NULL;
- a record is looked for on five columns: project, experiment, protein, well
  and plate;
- if one is found, the update is filtered on **four** of those - the plate is
  left out - and the dict it applies holds every column, the plate code among
  them;
- if none is found, ``get_or_create(**details)`` is called with every column as
  a lookup, so it always creates;
- the answer is always ``{'id': <project pk>}``, whichever of those happened,
  and nothing is validated.

The read side is ``Labwork.json_dict``'s twenty-one columns: ``status``,
``attachment`` and ``project_id`` are not among them, which is why this program
cannot compare them and keeps them in ``NOT_COMPARED``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import msgpack
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa

from xtalflow.domain.harvest_record import harvest_payload
from xtalflow.domain.mxlive import MxLiveLabwork
from xtalflow.domain.shifter_results import HarvestedCrystal
from xtalflow.infrastructure.mxlive_client import LegacyMxLiveWriteClient

PROJECT_PK = 71

# Labwork.json_dict, in its own order. Nothing else is readable.
READ_COLUMNS = (
    "name", "staff_comments", "expri_id", "protein_name", "plate_type",
    "plate_code", "plate_imgpath", "plate_well", "plate_x", "plate_y",
    "crystal_no", "soak_plate", "soak_well", "soak_vol", "soak_id",
    "soak_smile", "harvest_time", "harvest_order", "harvest_comment",
    "puck_name", "puck_nohole",
)

# The columns AddLabwork fills from info, in the order the source lists them.
WRITTEN_COLUMNS = (
    "name", "staff_comments", "status", "attachment", "expri_id",
    "protein_name", "plate_type", "plate_code", "plate_imgpath", "plate_well",
    "plate_x", "plate_y", "crystal_no", "soak_plate", "soak_well", "soak_vol",
    "soak_id", "soak_smile", "harvest_time", "harvest_order",
    "harvest_comment", "puck_name", "puck_nohole",
)

LOOKUP = ("project_id", "expri_id", "protein_name", "plate_well", "plate_code")
UPDATE_FILTER = ("project_id", "expri_id", "protein_name", "plate_well")

# Below the view sits the storage, and it changes values. The endpoint writes
# through update(), which bypasses the model, so what the column can hold is
# the database's problem - and what comes back is not always what went in.
# Each of these was measured against the real server; the comparison in
# _changed_since_upload forgives exactly these and nothing else, so a
# stand-in without them would never exercise that forgiveness.
INTEGER_COLUMNS = ("status", "crystal_no", "soak_vol", "harvest_order", "puck_nohole")
# Text in a Python 2 CharField: str(float) there keeps 12 significant digits.
AS_TEXT_12_DIGITS = ("plate_x", "plate_y")
# MySQL is not strict here, so anything longer is cut rather than refused.
# The lookup columns are in here now, their widths read from the install on
# 2026-11-21, because a value cut in one of those is the worst case this
# stand-in exists to show: cut once on the way in, missed by every later
# lookup made with the whole value, and so another record each time.
# staff_comments is deliberately absent - the same read found it declared a
# TextField, so it is not cut at 256 or at anything else we know of.
TEXT_LIMITS = {
    "name": 60, "plate_type": 30, "plate_imgpath": 512, "soak_well": 3,
    "soak_id": 60, "soak_smile": 128,
    "expri_id": 256, "protein_name": 128, "plate_well": 4, "plate_code": 60,
    "harvest_comment": 256, "harvest_time": 128, "puck_name": 10,
}


def _as_compared(details: Mapping[str, Any]) -> dict[str, Any]:
    """The conversions Django applies to a value, wherever it is used.

    ``get_prep_value`` runs on a filter's value as well as on one being
    written, so a lookup is made with the converted value too.
    """
    prepared = dict(details)
    for column in INTEGER_COLUMNS:
        if prepared.get(column) is not None:
            prepared[column] = int(prepared[column])
    if prepared.get("soak_vol") is not None:
        # An IntegerField the migration left as a float: truncated on the way
        # in, read back with a decimal point. 17.5 was sent and 17.0 came back.
        prepared["soak_vol"] = float(prepared["soak_vol"])
    for column in AS_TEXT_12_DIGITS:
        if prepared.get(column) is not None:
            prepared[column] = "%.12g" % float(prepared[column])
    return prepared


def _as_stored(details: Mapping[str, Any]) -> dict[str, Any]:
    """And then what the column itself does, which only happens on the way in.

    Cutting belongs here and not in ``_as_compared``: MySQL truncates a value
    being stored, never one in a WHERE clause. So a value longer than its
    column is cut once and every later lookup made with the whole value misses
    the cut one - which for a lookup column means a second record each time,
    and is why the identity check compares exactly. Keeping the cut out of the
    filter is what lets this stand-in show that instead of hiding it.
    """
    stored = _as_compared(details)
    for column, limit in TEXT_LIMITS.items():
        value = stored.get(column)
        if isinstance(value, str) and len(value) > limit:
            stored[column] = value[:limit]
    return stored


class AddLabwork:
    """views.py 642-707, as a table in memory."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []

    def post(self, info: Mapping[str, Any]) -> dict[str, Any]:
        details = {column: info.get(column) for column in WRITTEN_COLUMNS}
        details["project_id"] = PROJECT_PK

        found = self._first(self._matching(details, LOOKUP))
        stored = _as_stored(details)
        if found is not None:
            # The plate is not in this filter, and details carries it.
            for row in self._matching(details, UPDATE_FILTER):
                row.update(stored)
        else:
            # get_or_create over every column: nothing ever matches all of
            # them, so this is a create.
            self.rows.append(dict(stored))

        answer = {"id": PROJECT_PK}
        self.answers.append(answer)
        return answer

    def _matching(
        self, details: Mapping[str, Any], columns: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        wanted = _as_compared(details)
        return [
            row for row in self.rows
            if all(row.get(column) == wanted.get(column) for column in columns)
        ]

    @staticmethod
    def _first(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return rows[0] if rows else None

    def read(self, experiment_id: str) -> tuple[MxLiveLabwork, ...]:
        """What the reader gets back: twenty-one columns and no more."""
        return tuple(
            MxLiveLabwork.from_mapping(
                {column: row.get(column) for column in READ_COLUMNS}
            )
            for row in self.rows
            if experiment_id in str(row.get("expri_id") or "")
        )


class ViewTransport:
    """Carries one posted record into the view, as the real transport does."""

    def __init__(self, view: AddLabwork) -> None:
        self.view = view
        self.posted: list[Mapping[str, Any]] = []

    def post_msgpack(
        self, url: str, payload: object, *, timeout_seconds: float,
        ca_bundle: Path | None,
    ) -> object:
        assert isinstance(payload, Mapping)
        # Through msgpack and back, so a value this program cannot send is
        # found here rather than at the beamline.
        carried = msgpack.unpackb(
            msgpack.packb(dict(payload), use_bin_type=True), raw=False
        )
        self.posted.append(carried)
        return self.view.post(carried)


def _key_file(path: Path) -> Path:
    key = dsa.generate_private_key(key_size=1024)
    path.write_bytes(msgpack.packb({"private": key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )}))
    return path


def _client(view: AddLabwork, tmp_path: Path) -> LegacyMxLiveWriteClient:
    return LegacyMxLiveWriteClient(
        "https://mxlive.example", "BL-5C", "jjh",
        _key_file(tmp_path / "keys.dsa"), transport=ViewTransport(view),
    )


EXPERIMENT = "FragSC-202607-TEST-02"
UPLOAD_COMMENT = "Uploaded by XtalFlow · Fragment Screening"


def _uploaded(well: str = "A01a", plate: str = "2069") -> dict[str, Any]:
    """One record as the sample list upload sends it: nineteen columns."""
    return {
        "name": f"test {plate} {well}",
        # What a real upload puts there: labwork.py sets it, and the history
        # page recognises our records by this prefix.
        "staff_comments": UPLOAD_COMMENT,
        # The values labwork.py really sends, not the words they suggest.
        "status": 5,
        "attachment": "null",
        "expri_id": EXPERIMENT,
        "protein_name": "test",
        "plate_type": "SwissCI-MRC-3d",
        "plate_code": plate,
        "plate_imgpath": f"/share/{plate}/{well}.jpg",
        "plate_well": well,
        # Floats, as labwork.py sends them, with more digits than MxLive
        # keeps - so the 12-digit rule is exercised rather than assumed.
        "plate_x": 0.123456789012345,
        "plate_y": 1.5,
        "crystal_no": 1,
        "soak_plate": "384PP_DMSO2",
        "soak_well": "A1",
        # Whole nanolitres: mxlive_soak_volume rounds before sending.
        "soak_vol": 17,
        "soak_id": "LDV-1",
        "soak_smile": "CS(=O)C",
        "project_id": "jjh",
    }


def _harvest(well: str = "A01a", plate: str = "2069") -> HarvestedCrystal:
    return HarvestedCrystal(
        plate, well, "2026-11-20 09:12:05.0", "ISA-003", "1", comment="",
    )


def test_a_harvest_write_fills_the_harvest_and_leaves_every_other_column(
    tmp_path: Path,
) -> None:
    """The whole chain, through the view that will really receive it.

    The upload goes through the same view, so what is read back afterwards is
    what MxLive would hold - not a fixture written to agree with us.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    original = _uploaded()
    client.upload_labworks((original,))

    before = view.read(EXPERIMENT)
    assert len(before) == 1
    assert before[0].harvest_time is None and before[0].puck_name is None
    # What MxLive holds after the upload, which is what the harvest write must
    # leave alone. Compared against this rather than against the payload,
    # because the storage already changed some of it on the way in - a float
    # coordinate is text now, and a dose is a whole number.
    was = dict(before[0].raw)

    write = harvest_payload(original, before[0], _harvest())
    client.upload_labworks((write,))

    after = view.read(EXPERIMENT)
    # One record still. Nothing was created.
    assert len(after) == 1
    stored = after[0].raw
    assert stored["harvest_time"] == "2026-11-20 09:12:05.0"
    assert stored["puck_name"] == "ISA-003"
    assert stored["puck_nohole"] == 1
    assert stored["harvest_comment"] == ""
    # Not sent, so NULL - which is what this program intends for it.
    assert stored["harvest_order"] is None
    # The mark is appended, not written over: the words that were there stay,
    # and the prefix the history page looks for survives.
    assert stored["staff_comments"] == f"{UPLOAD_COMMENT} · Harvest by XtalFlow"
    assert stored["staff_comments"].startswith("Uploaded by XtalFlow")
    # Every other readable column is exactly as MxLive already held it.
    for column in READ_COLUMNS:
        if column.startswith("harvest") or column.startswith("puck"):
            continue
        if column == "staff_comments":
            continue
        assert stored[column] == was[column], column
    # And the storage really did change two of them on the way in, so the
    # comparison above is not passing by having nothing to compare.
    assert was["soak_vol"] == 17.0 and original["soak_vol"] == 17
    assert was["plate_x"] == "0.123456789012" and original["plate_x"] != was["plate_x"]


def test_leaving_a_column_out_is_the_same_as_sending_it_empty(
    tmp_path: Path,
) -> None:
    """``info.get`` with no default, so both are NULL.

    This is why the payload may drop ``harvest_order`` instead of having to
    send ``None`` for it, and why a column forgotten anywhere else would be
    erased rather than kept.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    without = {
        column: value for column, value in _uploaded().items()
        if column != "soak_smile"
    }
    client.upload_labworks((without,))
    assert view.read(EXPERIMENT)[0].raw["soak_smile"] is None

    view_two = AddLabwork()
    (tmp_path / "two").mkdir()
    client_two = _client(view_two, tmp_path / "two")
    client_two.upload_labworks(({**_uploaded(), "soak_smile": None},))
    assert view_two.read(EXPERIMENT)[0].raw["soak_smile"] is None


def test_one_of_the_five_drifting_creates_a_second_record(tmp_path: Path) -> None:
    """The one failure that cannot be undone, shown happening.

    The lookup is on five columns. If any of them differs from what is stored
    - a protein renamed on MxLive is the easy way - nothing is found, the
    create branch runs, and there are two records where there was one. MxLive
    has no delete. This is what the identity check in ``_changed_since_upload``
    exists to prevent, and it is checked against the record read back rather
    than against the plan.

    This test deliberately goes round that check, to show what the endpoint
    does when nothing stops it. That the check does stop it is
    ``test_a_record_renamed_on_mxlive_is_refused_and_names_both_values`` in
    test_harvest_record.py, and the post-check in ``harvest_writes`` refuses
    the same payload a second time just before it is handed over.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    original = _uploaded()
    client.upload_labworks((original,))

    renamed = dict(view.rows[0])
    renamed["protein_name"] = "test-renamed"
    view.rows[0] = renamed

    read_back = view.read(EXPERIMENT)[0]
    write = harvest_payload(original, read_back, _harvest())
    # The payload still carries the protein the upload sent, because that is
    # what the stored payload says. The lookup therefore misses.
    assert write["protein_name"] == "test"
    client.upload_labworks((write,))

    assert len(view.read(EXPERIMENT)) == 2


def test_writing_one_of_two_records_on_a_shared_well_replaces_the_other(
    tmp_path: Path,
) -> None:
    """The update filter drops the plate while the dict it applies carries it.

    Two plates uploaded under one experiment ID, the same well address on
    each. Writing a harvest onto one does not merely add the harvest to the
    other: the other is overwritten column for column, its own plate code
    last, until the two rows are the same row and the five-column lookup
    cannot separate them again.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    ours = _uploaded(plate="2069")
    theirs = _uploaded(plate="2070")
    client.upload_labworks((ours, theirs))
    assert len(view.read(EXPERIMENT)) == 2

    read_back = next(
        record for record in view.read(EXPERIMENT) if record.plate_code == "2069"
    )
    client.upload_labworks((harvest_payload(ours, read_back, _harvest()),))

    rows = view.read(EXPERIMENT)
    assert len(rows) == 2
    # Both now say 2069, and both carry our harvest and our image path.
    assert {row.plate_code for row in rows} == {"2069"}
    assert {row.puck_name for row in rows} == {"ISA-003"}
    assert {row.raw["plate_imgpath"] for row in rows} == {ours["plate_imgpath"]}
    # So 2070 is gone from MxLive, with no delete to have caused it.
    assert all(row.plate_code != "2070" for row in rows)


def test_the_answer_cannot_tell_an_update_from_a_create(tmp_path: Path) -> None:
    """Always ``{'id': project.pk}``, so the response proves nothing.

    Not the record's own id, and the activity log is written as CREATE either
    way. Reading the records back afterwards is the only thing that says what
    happened, which is why this program verifies by reading and writes no
    audit row of its own on the strength of a response.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    original = _uploaded()

    created = client.upload_labworks((original,))
    updated = client.upload_labworks((original,))

    assert view.answers == [{"id": PROJECT_PK}, {"id": PROJECT_PK}]
    assert created["responses"] == updated["responses"]
    assert len(view.rows) == 1


def test_a_comment_too_long_to_mark_keeps_its_words_through_the_view(
    tmp_path: Path,
) -> None:
    """Somebody's own words are worth more than our mark.

    The column holds 256 characters and MySQL is not strict here, so the
    database would cut whatever is over - silently, and from the end, which is
    where our mark would be. A comment long enough that the mark will not fit
    is therefore sent back as it was found, unmarked, rather than cut.

    Written as a comment the staff added after the upload, because that is how
    a long one gets there, and because the harvest write takes this column
    from what MxLive holds now rather than from what was uploaded.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    original = _uploaded()
    client.upload_labworks((original,))

    # Stripped here because the write strips it too: the one change this
    # column sees is its surrounding whitespace, which we did not author.
    theirs = (
        "crystals drifted overnight; " + "see the log of 2026-11-20. " * 8
    ).strip()
    assert len(theirs) > 234  # Long enough that the mark would not fit.
    view.rows[0]["staff_comments"] = theirs

    read_back = view.read(EXPERIMENT)[0]
    client.upload_labworks((harvest_payload(original, read_back, _harvest()),))

    stored = view.read(EXPERIMENT)[0].raw
    assert stored["staff_comments"] == theirs
    assert "Harvest by XtalFlow" not in stored["staff_comments"]
    # The harvest itself still went in; only the mark was given up.
    assert stored["puck_name"] == "ISA-003"
    assert len(view.read(EXPERIMENT)) == 1


def test_the_mark_is_not_added_twice(tmp_path: Path) -> None:
    """A record written once and read back already carries it.

    This flow refuses a record that reads as harvested, so a second write
    should not arise - but the column is read from MxLive each time, and a
    mark appended on every pass would eat the 256 characters it shares with
    whatever the staff wrote.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    original = _uploaded()
    client.upload_labworks((original,))

    for _ in range(3):
        read_back = view.read(EXPERIMENT)[0]
        client.upload_labworks((harvest_payload(original, read_back, _harvest()),))

    stored = view.read(EXPERIMENT)[0].raw
    assert stored["staff_comments"] == f"{UPLOAD_COMMENT} · Harvest by XtalFlow"
    assert stored["staff_comments"].count("Harvest by XtalFlow") == 1


def test_the_comparison_forgives_what_the_storage_did_and_no_more(
    tmp_path: Path,
) -> None:
    """The post-check inside harvest_writes, against a real round trip.

    ``_changed_since_upload`` runs twice on every harvest: once to decide the
    outcome, and once on the dict about to be sent. It compares the stored
    payload against what MxLive returns, so every difference the storage makes
    has to be forgiven or no plate could ever be recorded - and nothing beyond
    that, or a record changed on MxLive would be overwritten. Both halves are
    checked here through the stand-in rather than against hand-written values.

    The dose is the case that bites hardest: uploads before 947205f sent half
    nanolitres, which the integer column truncated, so the records of real
    fragment screens hold a number this program never sent.
    """
    from xtalflow.domain.harvest_record import (
        HarvestOutcome, HarvestRow, harvest_writes,
    )

    view = AddLabwork()
    client = _client(view, tmp_path)
    # An older upload: the dose as it was dispensed, before rounding.
    original = {**_uploaded(), "soak_vol": 17.5}
    client.upload_labworks((original,))
    read_back = view.read(EXPERIMENT)[0]
    assert read_back.raw["soak_vol"] == 17.0  # Truncated, as measured.

    row = HarvestRow(
        "2069", "A01a", HarvestOutcome.WILL_RECORD,
        harvest=_harvest(), on_mxlive=read_back,
    )
    # Must not raise: every difference here was made by MxLive, not by anybody.
    writes = harvest_writes((row,), {("2069", "A01a"): original})
    assert len(writes) == 1 and writes[0]["harvest_time"] == _harvest().harvest_time

    # And the other direction: a column somebody really changed is refused.
    moved = MxLiveLabwork.from_mapping({**read_back.raw, "plate_x": "9.5"})
    refused = HarvestRow(
        "2069", "A01a", HarvestOutcome.WILL_RECORD,
        harvest=_harvest(), on_mxlive=moved,
    )
    with pytest.raises(ValueError, match="plate_x"):
        harvest_writes((refused,), {("2069", "A01a"): original})


def test_the_stand_ins_text_limits_are_the_ones_the_comparison_uses() -> None:
    """Two records of the same column widths, kept from drifting apart.

    The comparison forgives a returned value that is exactly its column's
    length and a prefix of ours, because MySQL cut it. If these numbers
    disagreed, the stand-in would cut at one length while the comparison
    forgave another, and the tests above would pass on a fiction.
    """
    from xtalflow.domain.harvest_record import (
        LOOKUP_COLUMN_LIMITS, TEXT_COLUMN_LIMITS,
    )

    for column, limit in TEXT_COLUMN_LIMITS.items():
        assert TEXT_LIMITS[column] == limit, column
    for column, limit in LOOKUP_COLUMN_LIMITS.items():
        assert TEXT_LIMITS[column] == limit, column
    # And the one the domain used to claim a width for. The install declares
    # it a TextField, so nothing here may cut it.
    assert "staff_comments" not in TEXT_LIMITS


def test_a_protein_name_too_long_for_its_column_makes_a_record_every_time(
    tmp_path: Path,
) -> None:
    """The failure the lookup widths were read for, driven through the view.

    protein_name is CharField(max_length=128) and is one of the five columns
    MxLive finds a record by. A longer name is cut when it is stored, while
    every later lookup is made with the whole name - so nothing matches, and
    get_or_create creates again. Two uploads of the same well leave two
    records, and a third would leave three. MxLive has no delete.

    Nothing this program builds reaches 128, which is why this is a test of
    the stand-in's storage rather than of a guard: it says what the shape of
    the accident is, so that a future identifier long enough to be cut is
    recognised as this and not as a mystery.
    """
    view = AddLabwork()
    client = _client(view, tmp_path)
    too_long = "B" * (TEXT_LIMITS["protein_name"] + 1)
    payload = {**_uploaded(), "protein_name": too_long}

    client.upload_labworks((payload,))
    client.upload_labworks((payload,))

    assert len(view.rows) == 2
    assert all(len(row["protein_name"]) == 128 for row in view.rows)
    # And a name that fits is found again, so the two uploads leave one row.
    other = AddLabwork()
    fits = {**_uploaded(), "protein_name": "B" * TEXT_LIMITS["protein_name"]}
    second = tmp_path / "second"
    second.mkdir()
    twice = _client(other, second)
    twice.upload_labworks((fits,))
    twice.upload_labworks((fits,))
    assert len(other.rows) == 1
