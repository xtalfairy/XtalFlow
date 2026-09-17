from __future__ import annotations

import csv
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.instruments import InstrumentOutput, WorksheetKind
from xtalflow.domain.raw_crystal import RawCrystalPlan
from xtalflow.domain.worksheets import (
    ECHO_HEADER,
    SHIFTER_HEADER,
    build_echo_worksheet,
    build_shifter_worksheet,
)
from xtalflow.infrastructure.mounts import is_network_mount
from xtalflow.settings import ApplicationSettings, InstrumentDestination


WorksheetPlan = Union[FragmentScreenPlan, RawCrystalPlan]


class WorksheetDestinationUnavailable(ReviewPersistenceError):
    pass


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


def worksheets_for(
    plan: WorksheetPlan,
) -> dict[WorksheetKind, tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]]:
    """Header and rows of every worksheet a plan needs, by worksheet kind.

    Raw crystal plans only harvest, so they need no ECHO dispensing worksheet.
    """
    worksheets = {
        WorksheetKind.SHIFTER: (
            SHIFTER_HEADER,
            tuple(row.values() for row in build_shifter_worksheet(plan)),
        ),
    }
    if isinstance(plan, FragmentScreenPlan):
        worksheets[WorksheetKind.ECHO] = (
            ECHO_HEADER,
            tuple(row.values() for row in build_echo_worksheet(plan)),
        )
    return worksheets


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
    ) -> WorksheetExportResult:
        """Write to the instruments' folders, or below ``alternate_root/<instrument>``."""
        worksheets = worksheets_for(plan)
        destinations = self._destinations_for(worksheets)
        if alternate_root is None:
            missing = self._unavailable_bases(
                tuple(item.output_directory for item in destinations)
            )
            if missing and not self.settings.create_missing_instrument_roots:
                raise WorksheetDestinationUnavailable(
                    "instrument output location is unavailable: " + ", ".join(missing)
                )
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
            raise WorksheetDestinationUnavailable(f"{failure}: {error}") from error
        return self._export_to_directories(
            experiment_id, worksheets, destinations, directories
        )

    def _destinations_for(
        self, worksheets: dict[WorksheetKind, object]
    ) -> tuple[InstrumentDestination, ...]:
        destinations = tuple(
            item for item in self.settings.instruments if item.worksheet in worksheets
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

    def _unavailable_bases(self, bases: tuple[Path, ...]) -> list[str]:
        unavailable = []
        for base in bases:
            try:
                if not base.is_dir():
                    unavailable.append(str(base))
                elif (
                    self.settings.require_network_instrument_mounts
                    and not is_network_mount(base)
                ):
                    unavailable.append(f"{base} (network share is not mounted)")
            except OSError:
                unavailable.append(str(base))
        return unavailable

    def _export_to_directories(
        self,
        experiment_id: str,
        worksheets: dict[WorksheetKind, tuple],
        destinations: tuple[InstrumentDestination, ...],
        directories: tuple[Path, ...],
    ) -> WorksheetExportResult:
        try:
            file_stem = self._available_file_stem(experiment_id, directories)
            files = tuple(directory / f"{file_stem}.csv" for directory in directories)
            with self._staging_directory(file_stem) as staging_path:
                staged: dict[WorksheetKind, Path] = {}
                for kind, (header, rows) in worksheets.items():
                    staged[kind] = Path(staging_path) / f"{kind.value}.csv"
                    self._write_csv(staged[kind], header, rows)
                self._publish(
                    tuple(
                        (staged[destination.worksheet], file)
                        for destination, file in zip(destinations, files)
                    )
                )
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not save worksheets: {error}"
            ) from error
        return WorksheetExportResult(
            experiment_id,
            file_stem,
            tuple(
                InstrumentOutput(destination.instrument, str(file))
                for destination, file in zip(destinations, files)
            ),
        )

    def _staging_directory(self, file_stem: str) -> tempfile.TemporaryDirectory:
        root = self.settings.worksheet_staging_directory / self.username
        root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix=f"{file_stem}.", dir=root)

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
        experiment_id: str, directories: tuple[Path, ...]
    ) -> str:
        sequence = 0
        while True:
            stem = experiment_id if sequence == 0 else f"{experiment_id}_{sequence:02d}"
            if not any((directory / f"{stem}.csv").exists() for directory in directories):
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
