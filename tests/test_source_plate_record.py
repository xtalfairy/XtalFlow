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
