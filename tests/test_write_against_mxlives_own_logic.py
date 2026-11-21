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


class AddLabwork:
    """views.py 642-707, as a table in memory."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []

    def post(self, info: Mapping[str, Any]) -> dict[str, Any]:
        details = {column: info.get(column) for column in WRITTEN_COLUMNS}
        details["project_id"] = PROJECT_PK

        found = self._first(self._matching(details, LOOKUP))
        if found is not None:
            # The plate is not in this filter, and details carries it.
            for row in self._matching(details, UPDATE_FILTER):
                row.update(details)
        else:
            # get_or_create over every column: nothing ever matches all of
            # them, so this is a create.
            self.rows.append(dict(details))

        answer = {"id": PROJECT_PK}
        self.answers.append(answer)
        return answer

    def _matching(
        self, details: Mapping[str, Any], columns: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        return [
            row for row in self.rows
            if all(row.get(column) == details.get(column) for column in columns)
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
        "status": "Active",
        "attachment": "",
        "expri_id": EXPERIMENT,
        "protein_name": "test",
        "plate_type": "SwissCI-MRC-3d",
        "plate_code": plate,
        "plate_imgpath": f"/share/{plate}/{well}.jpg",
        "plate_well": well,
        "plate_x": "0.123456789012",
        "plate_y": "1.5",
        "crystal_no": 1,
        "soak_plate": "384PP_DMSO2",
        "soak_well": "A1",
        "soak_vol": 17.5,
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
    # Every other readable column is exactly as uploaded.
    for column in READ_COLUMNS:
        if column.startswith("harvest") or column.startswith("puck"):
            continue
        if column == "staff_comments":
            continue
        assert stored[column] == original[column], column


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
