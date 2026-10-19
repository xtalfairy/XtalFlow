import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xtalflow.domain.echo_source import PlateRecordEntry
from xtalflow.infrastructure.source_plate_record import (
    SourcePlateRecord,
    SourcePlateRecordUnavailable,
    SourceWellsTaken,
    record_directory,
)

NOW = datetime(2026, 9, 18, 14, 2, tzinfo=timezone.utc)


def _record(tmp_path: Path, **options) -> SourcePlateRecord:
    (tmp_path / "echo650").mkdir(exist_ok=True)
    return SourcePlateRecord(record_directory(tmp_path / "echo650"), **options)


def _used(wells, experiment="PreTest-202609-BRD4-01", user="kim"):
    return PlateRecordEntry("used", tuple(wells), user, NOW, experiment, "DMSO")


def test_entries_are_appended_as_lines_and_read_back_by_plate(tmp_path: Path) -> None:
    record = _record(tmp_path)
    assert record.entries("SOLVENT-01") == ()
    record.append("SOLVENT-01", [_used(["A1", "B1"])])
    record.append("solvent-01", [PlateRecordEntry("skipped", ("C1",), "lee", NOW, reason="Dust or debris")])

    # The same plate whatever the case; a line cut short by a failed write is ignored.
    path = tmp_path / "echo650" / "source-plates" / "solvent-01.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "used", "wells": ["D')
    entries = record.entries("Solvent-01")
    assert [(entry.kind, entry.wells, entry.user) for entry in entries] == [
        ("used", ("A1", "B1"), "kim"), ("skipped", ("C1",), "lee"),
    ]
    assert entries[1].reason == "Dust or debris"
    assert not (tmp_path / "echo650" / "source-plates" / "solvent-01.lock").exists()


def test_saving_records_wells_after_publishing_and_refuses_wells_someone_else_took(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path)
    published = []

    first = {"SOLVENT-01": [_used(["A1", "B1"], "PreTest-01")]}
    assert record.commit(first, "PreTest-01", lambda: published.append(1) or "ok") == "ok"
    assert [entry.wells for entry in record.entries("SOLVENT-01")] == [("A1", "B1")]

    # Saving the same experiment again records nothing twice.
    record.commit({"SOLVENT-01": [_used(["A1", "B1"], "PreTest-01")]}, "PreTest-01", lambda: None)
    assert len(record.entries("SOLVENT-01")) == 1

    # Someone else planned B1 meanwhile: nothing is published or recorded.
    with pytest.raises(SourceWellsTaken, match="SOLVENT-01 B1 was used by kim on 2026-09-18"):
        record.commit(
            {"SOLVENT-01": [_used(["B1", "C1"], "PreTest-02", "lee")]}, "PreTest-02",
            lambda: published.append(2),
        )
    assert published == [1]
    assert len(record.entries("SOLVENT-01")) == 1

    # A failed save records nothing either.
    def fail():
        raise OSError("share went away")

    with pytest.raises(OSError):
        record.commit({"SOLVENT-01": [_used(["C1"], "PreTest-03")]}, "PreTest-03", fail)
    assert len(record.entries("SOLVENT-01")) == 1


def test_a_lock_left_by_a_crash_is_cleared_but_a_live_one_is_waited_for(tmp_path: Path) -> None:
    record = _record(tmp_path, lock_timeout=0.3, stale_lock_after=60)
    record.directory.mkdir()
    lock = record.directory / "solvent-01.lock"
    lock.touch()
    with pytest.raises(SourcePlateRecordUnavailable, match="in use by someone else"):
        record.append("SOLVENT-01", [_used(["A1"])])

    old = time.time() - 600
    os.utime(lock, (old, old))
    record.append("SOLVENT-01", [_used(["A1"])])
    assert len(record.entries("SOLVENT-01")) == 1


def test_an_unreachable_echo_folder_is_reported_not_created(tmp_path: Path) -> None:
    record = SourcePlateRecord(record_directory(tmp_path / "smbmount" / "echo650"))
    with pytest.raises(SourcePlateRecordUnavailable, match="cannot be reached"):
        record.entries("SOLVENT-01")
    with pytest.raises(SourcePlateRecordUnavailable):
        record.append("SOLVENT-01", [_used(["A1"])])
    assert not (tmp_path / "smbmount").exists()


def test_shared_plates_are_listed_with_their_type_and_next_well(tmp_path: Path) -> None:
    record = _record(tmp_path)
    assert record.plates() == ()
    record.append("SOLVENT-01", [
        PlateRecordEntry("used", ("A1", "B1", "C1"), "kim", NOW, "PreTest-01", "DMSO", plate_type="1536LDV_DMSO"),
    ])
    record.append("SOLVENT-01", [PlateRecordEntry("skipped", ("D1",), "lee", NOW, reason="Damaged")])
    record.append("glycerol plate", [
        PlateRecordEntry("used", ("A1",), "kim", NOW, "PreTest-02", "Glycerol", plate_type="384PP_AQ_GP3"),
    ])

    assert [summary.label for summary in record.plates()] == [
        "glycerol plate · 384PP · 1 well used, next B1",
        "SOLVENT-01 · 1536LDV · 3 wells used, next E1",
    ]


def test_wells_taken_while_the_record_was_away_go_onto_it_when_it_is_back(tmp_path) -> None:
    """The held file is the only copy until the lines are on the shared record."""
    from xtalflow.infrastructure.source_plate_record import (
        SourcePlateRecord,
        merge_held,
        record_directory,
    )

    (tmp_path / "echo650").mkdir()
    shared = SourcePlateRecord(record_directory(tmp_path / "echo650"))
    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    shared.append("SOLVENT-01", [_used(["A1"], experiment="Earlier-01")])
    held.append("SOLVENT-01", [_used(["B1"], experiment="Offline-01")])
    held.append("SOLVENT-02", [_used(["A1"], experiment="Offline-01")])

    merged = merge_held(held, shared)
    assert sorted(merged.plates) == ["SOLVENT-01", "SOLVENT-02"]
    assert merged.clashes == ()

    assert [entry.wells for entry in shared.entries("SOLVENT-01")] == [("A1",), ("B1",)]
    assert [entry.wells for entry in shared.entries("SOLVENT-02")] == [("A1",)]
    # Nothing is held any more, and asking again is a no-op.
    assert not merge_held(held, shared)


def test_a_merge_run_twice_does_not_record_the_same_wells_twice(tmp_path) -> None:
    """A removal that failed must cost a repeat, not a duplicate."""
    from xtalflow.infrastructure.source_plate_record import (
        SourcePlateRecord,
        merge_held,
        record_directory,
    )

    (tmp_path / "echo650").mkdir()
    shared = SourcePlateRecord(record_directory(tmp_path / "echo650"))
    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    entry = _used(["B1"], experiment="Offline-01")
    held.append("SOLVENT-01", [entry])

    merge_held(held, shared)
    held.append("SOLVENT-01", [entry])  # as if the held file had survived
    merge_held(held, shared)

    assert [item.wells for item in shared.entries("SOLVENT-01")] == [("B1",)]


def test_a_merge_leaves_the_entries_held_while_the_record_is_still_away(tmp_path) -> None:
    from xtalflow.infrastructure.source_plate_record import (
        SourcePlateRecord,
        SourcePlateRecordUnavailable,
        merge_held,
        record_directory,
    )

    shared = SourcePlateRecord(record_directory(tmp_path / "missing-echo"))
    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    held.append("SOLVENT-01", [_used(["B1"], experiment="Offline-01")])

    with pytest.raises(SourcePlateRecordUnavailable):
        merge_held(held, shared)

    assert [entry.wells for entry in held.entries("SOLVENT-01")] == [("B1",)]


def test_a_well_two_experiments_both_took_is_reported_rather_than_hidden(tmp_path) -> None:
    """The liquid has already left the well: record both takes, and say so."""
    from xtalflow.infrastructure.source_plate_record import (
        SourcePlateRecord,
        merge_held,
        record_directory,
    )

    (tmp_path / "echo650").mkdir()
    shared = SourcePlateRecord(record_directory(tmp_path / "echo650"))
    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    # Someone else booked A5 while this user was offline and took it too.
    shared.append("SOLVENT-01", [_used(["A5"], experiment="Other-02", user="park")])
    held.append("SOLVENT-01", [_used(["A5", "B5"], experiment="Offline-01")])

    merged = merge_held(held, shared)

    assert merged.plates == ("SOLVENT-01",)
    assert merged.clashes == ("SOLVENT-01 A5 was used by park on 2026-09-18 (Other-02)",)
    # Both takes are on the record: hiding one would make it less true, not more.
    assert [entry.experiment_id for entry in shared.entries("SOLVENT-01")] == [
        "Other-02", "Offline-01"
    ]
    # B5 is nobody else's, so it is not reported.
    assert "B5" not in merged.clashes[0]


def test_a_record_being_moved_is_copied_so_the_old_place_stays_whole(tmp_path) -> None:
    """A client that has not been moved over yet keeps writing to the old place."""
    from xtalflow.infrastructure.source_plate_record import (
        SourcePlateRecord,
        merge_held,
        record_directory,
    )

    old = SourcePlateRecord(record_directory(tmp_path / "echo650"), create_root=True)
    new = SourcePlateRecord(tmp_path / "source-plates", create_root=True)
    old.append("SOLVENT-01", [_used(["A1"], experiment="Before-01")])

    assert merge_held(old, new, consume=False).plates == ("SOLVENT-01",)

    assert [entry.wells for entry in new.entries("SOLVENT-01")] == [("A1",)]
    assert [entry.wells for entry in old.entries("SOLVENT-01")] == [("A1",)]
    # Asked again, nothing has moved, so nothing is reported.
    assert merge_held(old, new, consume=False).plates == ()
    # And a straggler still writing to the old place is picked up next time.
    old.append("SOLVENT-01", [_used(["B1"], experiment="Straggler-02")])
    assert merge_held(old, new, consume=False).plates == ("SOLVENT-01",)
    assert [entry.wells for entry in new.entries("SOLVENT-01")] == [("A1",), ("B1",)]


def test_a_record_file_is_left_as_writable_as_the_folder_it_is_in(tmp_path) -> None:
    """The second person to use a plate must not find it unwritable."""
    import os
    import stat

    directory = tmp_path / "echo650" / "source-plates"
    directory.mkdir(parents=True)
    os.chmod(directory, 0o2775)
    record = SourcePlateRecord(directory)

    record.append("SOLVENT-01", [_used(["A1"])])

    mode = stat.S_IMODE((directory / "solvent-01.log").stat().st_mode)
    assert mode & 0o666 == 0o664, oct(mode)
    # A folder open to the whole machine leaves its lines open too.
    os.chmod(directory, 0o777)
    record.append("SOLVENT-02", [_used(["A1"])])
    assert stat.S_IMODE((directory / "solvent-02.log").stat().st_mode) & 0o666 == 0o666
