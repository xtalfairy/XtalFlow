from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.raw_crystal import RawCrystalPlan
from xtalflow.domain.worksheets import (
    ECHO_HEADER,
    SHIFTER_HEADER,
    build_echo_worksheet,
    build_shifter_worksheet,
)
from xtalflow.settings import ApplicationSettings


class WorksheetDestinationUnavailable(ReviewPersistenceError):
    pass


@dataclass(frozen=True, slots=True)
class WorksheetExportResult:
    experiment_id: str
    file_stem: str
    echo_path: Path
    shifter1_path: Path
    shifter2_path: Path


@dataclass(frozen=True, slots=True)
class ShifterExportResult:
    experiment_id: str
    file_stem: str
    shifter1_path: Path
    shifter2_path: Path


class WorksheetExporter:
    def __init__(self, settings: ApplicationSettings, username: str) -> None:
        if not username.strip() or "/" in username or "\\" in username:
            raise ValueError("invalid worksheet username")
        self.settings = settings
        self.username = username

    def existing_experiment_ids(self) -> set[str]:
        existing: set[str] = set()
        for base in self._instrument_bases():
            directory = base / self.username
            if directory.is_dir():
                existing.update(path.stem for path in directory.glob("*.csv"))
        return existing

    def export(
        self, plan: FragmentScreenPlan, experiment_id: str
    ) -> WorksheetExportResult:
        bases = self._instrument_bases()
        missing = [base for base in bases if not base.is_dir()]
        if missing and not self.settings.create_missing_instrument_roots:
            paths = ", ".join(str(path) for path in missing)
            raise WorksheetDestinationUnavailable(
                f"instrument output location is unavailable: {paths}"
            )
        for base in bases:
            (base / self.username).mkdir(parents=True, exist_ok=True)
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
            for name in ("echo650", "shifter1", "shifter2")
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return self._export_to_directories(plan, experiment_id, directories)

    def export_shifter(
        self, plan: RawCrystalPlan, experiment_id: str
    ) -> ShifterExportResult:
        bases = (self.settings.shifter1_output_directory,
                 self.settings.shifter2_output_directory)
        missing = [base for base in bases if not base.is_dir()]
        if missing and not self.settings.create_missing_instrument_roots:
            raise WorksheetDestinationUnavailable(
                "instrument output location is unavailable: "
                + ", ".join(str(path) for path in missing)
            )
        directories = tuple(base / self.username for base in bases)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return self._export_shifter_to_directories(plan, experiment_id, directories)

    def export_shifter_to_alternate_root(
        self, plan: RawCrystalPlan, experiment_id: str, root: Path
    ) -> ShifterExportResult:
        directories = tuple(
            root / name / self.username for name in ("shifter1", "shifter2")
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return self._export_shifter_to_directories(plan, experiment_id, directories)

    def _export_shifter_to_directories(
        self, plan: RawCrystalPlan, experiment_id: str,
        directories: tuple[Path, Path],
    ) -> ShifterExportResult:
        file_stem = self._available_file_stem(experiment_id, directories)
        staging = self.settings.worksheet_staging_directory / self.username / file_stem
        try:
            staging.mkdir(parents=True, exist_ok=False)
            staged = staging / "shifter.csv"
            self._write_csv(
                staged, SHIFTER_HEADER,
                tuple(row.values() for row in build_shifter_worksheet(plan)),
            )
            destinations = tuple(directory / f"{file_stem}.csv" for directory in directories)
            shutil.copy2(staged, destinations[0])
            shutil.copy2(staged, destinations[1])
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not save worksheets: {error}"
            ) from error
        return ShifterExportResult(
            experiment_id, file_stem, destinations[0], destinations[1]
        )

    def _export_to_directories(
        self,
        plan: FragmentScreenPlan,
        experiment_id: str,
        directories: tuple[Path, ...],
    ) -> WorksheetExportResult:
        file_stem = self._available_file_stem(experiment_id, directories)
        staging = self.settings.worksheet_staging_directory / self.username / file_stem
        try:
            staging.mkdir(parents=True, exist_ok=False)
            echo_staged = staging / "echo.csv"
            shifter_staged = staging / "shifter.csv"
            self._write_csv(
                echo_staged,
                ECHO_HEADER,
                tuple(row.values() for row in build_echo_worksheet(plan)),
            )
            self._write_csv(
                shifter_staged,
                SHIFTER_HEADER,
                tuple(row.values() for row in build_shifter_worksheet(plan)),
            )
            destinations = tuple(directory / f"{file_stem}.csv" for directory in directories)
            shutil.copy2(echo_staged, destinations[0])
            shutil.copy2(shifter_staged, destinations[1])
            shutil.copy2(shifter_staged, destinations[2])
        except OSError as error:
            raise WorksheetDestinationUnavailable(
                f"could not save worksheets: {error}"
            ) from error
        return WorksheetExportResult(
            experiment_id, file_stem, destinations[0], destinations[1], destinations[2]
        )

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
