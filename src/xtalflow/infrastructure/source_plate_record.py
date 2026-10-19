"""Shared record of which wells of each ECHO source plate have been used or skipped.

Everyone planning solvent tests draws from the same source plates beside the
ECHO, so the record lives with the ECHO worksheets, where every user already
saves: ``<ECHO root>/source-plates/<plate>.log``, one JSON line per entry.

Lines are only ever appended. A lock file, created exclusively, keeps two users
from recording the same wells at once; SQLite is not used because its locking is
unreliable on network shares. A line cut short by a failed write is ignored.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from xtalflow.domain.echo_source import (
    USED,
    PlateRecordEntry,
    PlateSummary,
    summarize_plate,
    well_history,
)
from xtalflow.application.worksheet_export import (  # noqa: F401  (re-exported)
    SourcePlateRecordUnavailable,
    SourceRecordBusy,
    SourceWellsTaken,
)

T = TypeVar("T")
RECORD_DIRECTORY = "source-plates"


def _file_key(plate: str) -> str:
    key = re.sub(r"[^a-z0-9._-]+", "_", plate.strip().casefold()).strip("._")
    return key or "plate"


def _entry_to_line(plate: str, entry: PlateRecordEntry) -> str:
    return json.dumps(
        {
            "time": entry.time.isoformat(), "plate": plate, "kind": entry.kind,
            "wells": list(entry.wells), "user": entry.user,
            "experiment": entry.experiment_id, "additive": entry.additive,
            "reason": entry.reason, "plate_type": entry.plate_type,
        },
        ensure_ascii=False, sort_keys=True,
    )


def _entry_from_line(line: str) -> PlateRecordEntry | None:
    try:
        data = json.loads(line)
        return PlateRecordEntry(
            str(data["kind"]), tuple(str(well) for well in data["wells"]), str(data["user"]),
            datetime.fromisoformat(data["time"]), str(data.get("experiment", "")),
            str(data.get("additive", "")), str(data.get("reason", "")),
            str(data.get("plate_type", "")),
        )
    except (ValueError, KeyError, TypeError):
        return None


class SourcePlateRecord:
    def __init__(
        self,
        directory: Path,
        *,
        create_root: bool = False,
        lock_timeout: float = 10.0,
        stale_lock_after: float = 120.0,
    ) -> None:
        self.directory = directory
        # Like instrument folders: only local setups may create a missing ECHO folder.
        self.create_root = create_root
        self.lock_timeout = lock_timeout
        self.stale_lock_after = stale_lock_after

    def _path(self, plate: str) -> Path:
        return self.directory / f"{_file_key(plate)}.log"

    def _require_root(self) -> None:
        # The record's folder may be created, but only inside an ECHO folder that exists.
        if not self.directory.parent.is_dir() and not self.create_root:
            raise SourcePlateRecordUnavailable(
                f"the shared source plate record in {self.directory.parent} cannot be reached"
            )

    def reachable(self) -> bool:
        """Whether the record can be read at all, asked before relying on it."""
        try:
            self._require_root()
        except SourcePlateRecordUnavailable:
            return False
        return True

    def entries(self, plate: str) -> tuple[PlateRecordEntry, ...]:
        self._require_root()
        return self._read(self._path(plate), plate)[1]

    def _read(self, path: Path, plate: str) -> tuple[str, tuple[PlateRecordEntry, ...]]:
        """The plate's name as first written, and its entries."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return plate, ()
        except OSError as error:
            raise SourcePlateRecordUnavailable(f"could not read the record of {plate}: {error}") from error
        name = plate
        for line in text.splitlines():
            try:
                name = str(json.loads(line)["plate"]) or plate
                break
            except (ValueError, KeyError, TypeError):
                continue
        return name, tuple(entry for line in text.splitlines() if (entry := _entry_from_line(line)))

    def plates(self) -> tuple[PlateSummary, ...]:
        """Every shared plate with a record, by name."""
        self._require_root()
        try:
            paths = sorted(self.directory.glob("*.log"))
        except OSError as error:
            raise SourcePlateRecordUnavailable(f"could not list {self.directory}: {error}") from error
        summaries = []
        for path in paths:
            name, entries = self._read(path, path.stem)
            summaries.append(summarize_plate(name, entries))
        return tuple(sorted(summaries, key=lambda item: item.name.casefold()))

    def append(self, plate: str, entries: Sequence[PlateRecordEntry]) -> None:
        with self._locked(plate):
            self._append(plate, entries)

    def _append(self, plate: str, entries: Sequence[PlateRecordEntry]) -> None:
        if not entries:
            return
        try:
            with self._path(plate).open("a", encoding="utf-8") as handle:
                handle.write("".join(_entry_to_line(plate, entry) + "\n" for entry in entries))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise SourcePlateRecordUnavailable(f"could not write the record of {plate}: {error}") from error

    @contextmanager
    def _locked(self, plate: str):
        self._require_root()
        try:
            self.directory.mkdir(parents=self.create_root, exist_ok=True)
        except OSError as error:
            raise SourcePlateRecordUnavailable(f"could not create {self.directory}: {error}") from error
        lock = self.directory / f"{_file_key(plate)}.lock"
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    # A lock left behind by a crash would otherwise block everyone.
                    if time.time() - lock.stat().st_mtime > self.stale_lock_after:
                        lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise SourceRecordBusy(
                        f"the record of {plate} is in use by someone else; try again"
                    ) from None
                time.sleep(0.1)
            except OSError as error:
                raise SourcePlateRecordUnavailable(f"could not lock the record of {plate}: {error}") from error
        os.close(descriptor)
        try:
            yield
        finally:
            try:
                lock.unlink()
            except OSError:
                pass

    def commit(
        self,
        new_entries: Mapping[str, Sequence[PlateRecordEntry]],
        experiment_id: str,
        publish: Callable[[], T],
    ) -> T:
        """Check the wells are still free, publish, then record them, all under the plates' locks.

        Wells this experiment recorded before, e.g. when saving again, are its own
        and are not recorded twice.
        """
        plates = sorted(plate for plate, entries in new_entries.items() if entries)
        with ExitStack() as stack:
            for plate in plates:
                stack.enter_context(self._locked(plate))
            pending: dict[str, list[PlateRecordEntry]] = {}
            for plate in plates:
                recorded = self.entries(plate)
                own = {
                    (entry.kind, well) for entry in recorded
                    if entry.experiment_id == experiment_id for well in entry.wells
                }
                history = well_history(
                    entry for entry in recorded if entry.experiment_id != experiment_id
                )
                for entry in new_entries[plate]:
                    if entry.kind == USED:
                        taken = next((well for well in entry.wells if well in history), None)
                        if taken is not None:
                            raise SourceWellsTaken(
                                f"{history[taken].describe(taken, plate)}. Go back to Conditions "
                                "and use the next wells."
                            )
                    if all((entry.kind, well) in own for well in entry.wells):
                        continue
                    pending.setdefault(plate, []).append(entry)
            result = publish()
            for plate, entries in pending.items():
                self._append(plate, entries)
        return result


@dataclass(frozen=True)
class MergeResult:
    """Which plates were merged, and every well two experiments both took."""

    plates: tuple[str, ...] = ()
    clashes: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.plates or self.clashes)


def merge_held(
    held: SourcePlateRecord, shared: SourcePlateRecord, consume: bool = True
) -> MergeResult:
    """Put entries written somewhere else onto the shared record.

    ``consume`` removes each source file once its lines are on the shared
    record, which is right for entries held while the record was away: they
    were only ever waiting. A record being moved from one place to another is
    copied instead, so the old place stays whole and a client that has not
    been told about the move yet keeps being picked up.


    Entries already there are skipped, so this can be run again after being
    interrupted, and so either way it can be run again at all.

    A well that someone else took meanwhile is reported, not hidden and not
    refused.  ``commit`` can turn a clash away because nothing has happened
    yet; here the liquid has already left the well, and a record that leaves
    that out is further from the truth than one that shows both takes.  What
    it cannot be is silent: this is the collision the record exists to catch.

    Raises SourcePlateRecordUnavailable if the shared record is still away,
    which is the ordinary case and why this is asked again and again.
    """
    try:
        paths = sorted(held.directory.glob("*.log"))
    except OSError:
        return MergeResult()
    if not paths:
        return MergeResult()
    shared._require_root()
    merged: list[str] = []
    clashes: list[str] = []
    for path in paths:
        plate, entries = held._read(path, path.stem)
        if not entries:
            if consume:
                path.unlink(missing_ok=True)
            continue
        with shared._locked(plate):
            recorded = shared.entries(plate)
            already = {_entry_to_line(plate, entry) for entry in recorded}
            new = [
                entry for entry in entries
                if _entry_to_line(plate, entry) not in already
            ]
            ours = {entry.experiment_id for entry in new}
            history = well_history(
                entry for entry in recorded if entry.experiment_id not in ours
            )
            for entry in new:
                if entry.kind != USED:
                    continue
                clashes.extend(
                    history[well].describe(well, plate)
                    for well in entry.wells if well in history
                )
            if new:
                shared._append(plate, new)
        # Only now: until the lines are there, the held file is the only copy.
        if consume:
            path.unlink(missing_ok=True)
        if new:
            merged.append(plate)
    return MergeResult(tuple(merged), tuple(clashes))


def record_directory(root: Path) -> Path:
    """The record's folder under a root: the ECHO folder, or the folder we hold in."""
    return root / RECORD_DIRECTORY
