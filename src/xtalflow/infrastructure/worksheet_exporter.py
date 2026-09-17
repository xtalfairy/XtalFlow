from __future__ import annotations

import csv
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.instruments import ECHO_650, SHIFTER_1, SHIFTER_2, InstrumentOutput
from xtalflow.domain.raw_crystal import RawCrystalPlan
from xtalflow.domain.worksheets import (
    ECHO_HEADER,
    SHIFTER_HEADER,
    build_echo_worksheet,
    build_shifter_worksheet,
)
from xtalflow.infrastructure.mounts import is_network_mount
from xtalflow.settings import ApplicationSettings


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


def _outputs(
    instruments: tuple[str, ...], destinations: tuple[Path, ...]
) -> tuple[InstrumentOutput, ...]:
    return tuple(
        InstrumentOutput(instrument, str(destination))
        for instrument, destination in zip(instruments, destinations)
    )


class WorksheetExporter:
    def __init__(self, settings: ApplicationSettings, username: str) -> None:
        if not username.strip() or "/" in username or "\\" in username:
            raise ValueError("invalid worksheet username")
        self.settings = settings
        self.username = username

    def export(
        self, plan: FragmentScreenPlan, experiment_id: str
    ) -> WorksheetExportResult:
        bases = self._instrument_bases()
        missing = self._unavailable_bases(bases)
        if missing and not self.settings.create_missing_instrument_roots:
            raise WorksheetDestinationUnavailable(
                "instrument output location is unavailable: " + ", ".join(missing)
            )
        try:
            for base in bases:
                (base / self.username).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not prepare instrument output folders: {error}"
            ) from error
        return self._export_to_directories(
            plan,
            experiment_id,
            tuple(base / self.username for base in bases),
        )

    def export_to_alternate_root(
        self, plan: FragmentScreenPlan, experiment_id: str, root: Path
    ) -> WorksheetExportResult:
        directories = tuple(
            root / name / self.username
            for name in (ECHO_650, SHIFTER_1, SHIFTER_2)
        )
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not prepare alternate output folders: {error}"
            ) from error
        return self._export_to_directories(plan, experiment_id, directories)

    def export_shifter(
        self, plan: RawCrystalPlan, experiment_id: str
    ) -> WorksheetExportResult:
        bases = (self.settings.shifter1_output_directory,
                 self.settings.shifter2_output_directory)
        missing = self._unavailable_bases(bases)
        if missing and not self.settings.create_missing_instrument_roots:
            raise WorksheetDestinationUnavailable(
                "instrument output location is unavailable: " + ", ".join(missing)
            )
        directories = tuple(base / self.username for base in bases)
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not prepare instrument output folders: {error}"
            ) from error
        return self._export_shifter_to_directories(plan, experiment_id, directories)

    def export_shifter_to_alternate_root(
        self, plan: RawCrystalPlan, experiment_id: str, root: Path
    ) -> WorksheetExportResult:
        directories = tuple(
            root / name / self.username for name in (SHIFTER_1, SHIFTER_2)
        )
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not prepare alternate output folders: {error}"
            ) from error
        return self._export_shifter_to_directories(plan, experiment_id, directories)

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

    def _export_shifter_to_directories(
        self, plan: RawCrystalPlan, experiment_id: str,
        directories: tuple[Path, Path],
    ) -> WorksheetExportResult:
        shifter_rows = tuple(row.values() for row in build_shifter_worksheet(plan))
        try:
            file_stem = self._available_file_stem(experiment_id, directories)
            destinations = tuple(directory / f"{file_stem}.csv" for directory in directories)
            with self._staging_directory(file_stem) as staging_path:
                staging = Path(staging_path)
                staged = staging / "shifter.csv"
                self._write_csv(staged, SHIFTER_HEADER, shifter_rows)
                self._publish(((staged, destinations[0]), (staged, destinations[1])))
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not save worksheets: {error}"
            ) from error
        return WorksheetExportResult(
            experiment_id, file_stem,
            _outputs((SHIFTER_1, SHIFTER_2), destinations),
        )

    def _export_to_directories(
        self,
        plan: FragmentScreenPlan,
        experiment_id: str,
        directories: tuple[Path, ...],
    ) -> WorksheetExportResult:
        echo_rows = tuple(row.values() for row in build_echo_worksheet(plan))
        shifter_rows = tuple(row.values() for row in build_shifter_worksheet(plan))
        try:
            file_stem = self._available_file_stem(experiment_id, directories)
            destinations = tuple(directory / f"{file_stem}.csv" for directory in directories)
            with self._staging_directory(file_stem) as staging_path:
                staging = Path(staging_path)
                echo_staged = staging / "echo.csv"
                shifter_staged = staging / "shifter.csv"
                self._write_csv(echo_staged, ECHO_HEADER, echo_rows)
                self._write_csv(shifter_staged, SHIFTER_HEADER, shifter_rows)
                self._publish(
                    (
                        (echo_staged, destinations[0]),
                        (shifter_staged, destinations[1]),
                        (shifter_staged, destinations[2]),
                    )
                )
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not save worksheets: {error}"
            ) from error
        return WorksheetExportResult(
            experiment_id, file_stem,
            _outputs((ECHO_650, SHIFTER_1, SHIFTER_2), destinations),
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

    def _instrument_bases(self) -> tuple[Path, Path, Path]:
        return (
            self.settings.echo_output_directory,
            self.settings.shifter1_output_directory,
            self.settings.shifter2_output_directory,
        )

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
