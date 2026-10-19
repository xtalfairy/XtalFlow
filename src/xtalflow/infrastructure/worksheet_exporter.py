from __future__ import annotations

import csv
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from xtalflow.application.worksheet_export import WorksheetDestinationUnavailable
from xtalflow.domain.condition_test import ConditionTestPlan
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.instruments import InstrumentOutput, WorksheetKind
from xtalflow.domain.raw_crystal import RawCrystalPlan
from xtalflow.domain.worksheets import (
    ECHO_HEADER,
    SHIFTER_HEADER,
    build_condition_echo_rounds,
    build_echo_worksheet,
    build_shifter_worksheet,
)
from xtalflow.infrastructure.mounts import is_network_mount
from xtalflow.scratch import private_directory
from xtalflow.settings import ApplicationSettings, InstrumentDestination


WorksheetPlan = Union[FragmentScreenPlan, RawCrystalPlan, ConditionTestPlan]


class InstrumentLocationUnavailable(WorksheetDestinationUnavailable):
    """An instrument's folder cannot be reached or written to.

    Told apart from the other failures because the files themselves are fine:
    another folder will hold them until the instrument's own can be reached,
    and the instruments that are up can be given their worksheets meanwhile.
    """

    def __init__(
        self,
        message: str,
        unreachable: tuple[str, ...] = (),
        reachable: tuple[str, ...] = (),
        reachable_covers_every_worksheet: bool = False,
    ) -> None:
        super().__init__(message)
        self.unreachable = unreachable
        self.reachable = reachable
        # Whether every worksheet this plan makes has somewhere to go without
        # the instruments that are down.
        self.reachable_covers_every_worksheet = reachable_covers_every_worksheet


@dataclass(frozen=True)
class WorksheetExportResult:
    experiment_id: str
    file_stem: str
    outputs: tuple[InstrumentOutput, ...]

    def path_for(self, instrument: str) -> Path:
        return next(
            Path(output.path) for output in self.outputs
            if output.instrument == instrument
        )


@dataclass(frozen=True)
class WorksheetFile:
    """One file for the instruments that read a worksheet kind."""

    suffix: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def worksheets_for(plan: WorksheetPlan) -> dict[WorksheetKind, tuple[WorksheetFile, ...]]:
    """Every worksheet file a plan needs, by worksheet kind.

    Raw crystal plans only harvest, so they need no ECHO dispensing worksheet.
    A condition test dispenses at several times, so it gets one ECHO file per
    time, suffixed _R1, _R2, ... when there is more than one.
    """
    shifter = WorksheetFile(
        "", SHIFTER_HEADER, tuple(row.values() for row in build_shifter_worksheet(plan))
    )
    worksheets = {WorksheetKind.SHIFTER: (shifter,)}
    if isinstance(plan, FragmentScreenPlan):
        worksheets[WorksheetKind.ECHO] = (
            WorksheetFile(
                "", ECHO_HEADER, tuple(row.values() for row in build_echo_worksheet(plan))
            ),
        )
    elif isinstance(plan, ConditionTestPlan):
        rounds = build_condition_echo_rounds(plan)
        if rounds:
            worksheets[WorksheetKind.ECHO] = tuple(
                WorksheetFile(
                    f"_R{index}" if len(rounds) > 1 else "",
                    ECHO_HEADER,
                    tuple(row.values() for row in rows),
                )
                for index, (_minute, rows) in enumerate(rounds, start=1)
            )
    return worksheets


def pending_root(settings: ApplicationSettings) -> Path:
    """Where a worksheet waits for an instrument that cannot be reached.

    It has to outlive a reboot - an instrument can be off for days - so it is
    the account's own home unless the installation says otherwise. Never a
    temporary folder, and never the instrument's own empty mountpoint: a file
    left there would vanish behind the share when it came back.
    """
    configured = settings.pending_worksheet_directory
    return configured if configured is not None else Path.home() / "xtalflow-pending"


class WorksheetExporter:
    """Deliver each worksheet to every configured instrument that reads its kind."""

    def __init__(self, settings: ApplicationSettings, username: str) -> None:
        if not username.strip() or "/" in username or "\\" in username:
            raise ValueError("invalid worksheet username")
        self.settings = settings
        self.username = username

    def export(
        self,
        plan: WorksheetPlan,
        experiment_id: str,
        alternate_root: Path | None = None,
        instruments: tuple[str, ...] | None = None,
        hold_unreachable: bool = False,
    ) -> WorksheetExportResult:
        """Write to the instruments' folders, or below ``alternate_root/<instrument>``.

        ``instruments`` limits delivery to those instrument ids, for saving to
        the ones that are up while another is down. Every worksheet the plan
        makes must still have an instrument that reads it.

        ``hold_unreachable`` writes the worksheet of an instrument that cannot
        be reached into a folder that waits for it, so the experiment can run
        on the instruments that are up. Nothing is ever written to an empty
        mountpoint: the file would vanish behind the share when it returns.
        """
        worksheets = worksheets_for(plan)
        destinations = self._destinations_for(worksheets, instruments)
        if alternate_root is None:
            missing = self._unavailable_bases(destinations)
            if missing and not self.settings.create_missing_instrument_roots:
                if not hold_unreachable:
                    reachable = tuple(
                        item for item in destinations if item.instrument not in missing
                    )
                    raise InstrumentLocationUnavailable(
                        "instrument output location is unavailable: "
                        + ", ".join(missing.values()),
                        unreachable=tuple(missing),
                        reachable=tuple(item.instrument for item in reachable),
                        reachable_covers_every_worksheet=(
                            {item.worksheet for item in reachable} == set(worksheets)
                        ),
                    )
                waiting = self._pending_root()
                directories = tuple(
                    waiting / item.instrument if item.instrument in missing
                    else item.output_directory / self.username
                    for item in destinations
                )
                failure = "could not prepare the folders worksheets wait in"
            else:
                directories = tuple(
                    item.output_directory / self.username for item in destinations
                )
                failure = "could not prepare instrument output folders"
        else:
            directories = tuple(
                alternate_root / item.instrument / self.username for item in destinations
            )
            failure = "could not prepare alternate output folders"
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstrumentLocationUnavailable(f"{failure}: {error}") from error
        return self._export_to_directories(
            experiment_id, worksheets, destinations, directories
        )

    def unreachable_instruments(self) -> dict[str, str]:
        """Which configured instrument folders cannot be reached, and why.

        Asked before a save and while the Worksheets step is on screen, so
        that an instrument being away is known before anybody presses save.
        """
        if self.settings.create_missing_instrument_roots:
            return {}
        return self._unavailable_bases(self.settings.instruments)

    def _destinations_for(
        self,
        worksheets: dict[WorksheetKind, object],
        instruments: tuple[str, ...] | None = None,
    ) -> tuple[InstrumentDestination, ...]:
        destinations = tuple(
            item for item in self.settings.instruments
            if item.worksheet in worksheets
            and (instruments is None or item.instrument in instruments)
        )
        configured = {item.worksheet for item in destinations}
        unconfigured = [kind.value for kind in worksheets if kind not in configured]
        if unconfigured:
            raise WorksheetDestinationUnavailable(
                "no instrument is configured for "
                + ", ".join(sorted(unconfigured))
                + " worksheets"
            )
        return destinations

    def _unavailable_bases(
        self, destinations: tuple[InstrumentDestination, ...]
    ) -> dict[str, str]:
        """Why each instrument that cannot be reached cannot be reached."""
        unavailable: dict[str, str] = {}
        for item in destinations:
            base = item.output_directory
            try:
                if not base.is_dir():
                    unavailable[item.instrument] = str(base)
                elif (
                    self.settings.require_network_instrument_mounts
                    and not is_network_mount(base)
                ):
                    unavailable[item.instrument] = (
                        f"{base} (network share is not mounted)"
                    )
            except OSError:
                unavailable[item.instrument] = str(base)
        return unavailable

    def _export_to_directories(
        self,
        experiment_id: str,
        worksheets: dict[WorksheetKind, tuple[WorksheetFile, ...]],
        destinations: tuple[InstrumentDestination, ...],
        directories: tuple[Path, ...],
    ) -> WorksheetExportResult:
        try:
            suffixes = tuple(
                {item.suffix for files in worksheets.values() for item in files}
            )
            file_stem = self._available_file_stem(experiment_id, directories, suffixes)
            with self._staging_directory(file_stem) as staging_path:
                staged: dict[tuple[WorksheetKind, str], Path] = {}
                for kind, files in worksheets.items():
                    for item in files:
                        staged[(kind, item.suffix)] = (
                            Path(staging_path) / f"{kind.value}{item.suffix}.csv"
                        )
                        self._write_csv(staged[(kind, item.suffix)], item.header, item.rows)
                publications = tuple(
                    (
                        destination,
                        staged[(destination.worksheet, item.suffix)],
                        directory / f"{file_stem}{item.suffix}.csv",
                    )
                    for destination, directory in zip(destinations, directories)
                    for item in worksheets[destination.worksheet]
                )
                self._publish(tuple((source, target) for _, source, target in publications))
        except OSError as error:
            raise InstrumentLocationUnavailable(
                f"could not save worksheets: {error}"
            ) from error
        return WorksheetExportResult(
            experiment_id,
            file_stem,
            tuple(
                InstrumentOutput(destination.instrument, str(target))
                for destination, _, target in publications
            ),
        )

    def _pending_root(self) -> Path:
        root = pending_root(self.settings)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _staging_directory(self, file_stem: str) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(
            prefix=f"{file_stem}.", dir=self._staging_root()
        )

    def _staging_root(self) -> Path:
        """A scratch folder of this account's own.

        Worksheets are built here and copied to the instruments, so it only
        has to be local and writable. On a machine many researchers share it
        must not sit below a folder one of them created: that account's umask
        would decide whether the next one can save at all.
        """
        configured = self.settings.worksheet_staging_directory
        if configured is None:
            return private_directory("xtalflow", self.username)
        root = configured / self.username
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    @staticmethod
    def _publish(files: tuple[tuple[Path, Path], ...]) -> None:
        """Copy every worksheet beside its destination, then rename all of them.

        Instruments never see a partially written file, and a failed export
        leaves no worksheet behind that could be run or block a retry.
        """
        pending: list[tuple[Path, Path]] = []
        published: list[Path] = []
        try:
            for staged, destination in files:
                partial = destination.with_name(f".{destination.name}.partial")
                pending.append((partial, destination))
                shutil.copyfile(staged, partial)
            for partial, destination in pending:
                os.replace(partial, destination)
                published.append(destination)
        except OSError:
            for path in (*published, *(partial for partial, _ in pending)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    @staticmethod
    def _available_file_stem(
        experiment_id: str, directories: tuple[Path, ...], suffixes: tuple[str, ...] = ("",)
    ) -> str:
        sequence = 0
        while True:
            stem = experiment_id if sequence == 0 else f"{experiment_id}_{sequence:02d}"
            if not any(
                (directory / f"{stem}{suffix}.csv").exists()
                for directory in directories
                for suffix in suffixes
            ):
                return stem
            sequence += 1

    @staticmethod
    def _write_csv(
        path: Path, header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
    ) -> None:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
