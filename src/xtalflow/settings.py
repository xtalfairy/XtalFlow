"""Site-specific filesystem defaults for XtalFlow.

Edit this file when installing XtalFlow at a new beamline or workstation.  Command
line arguments may override the defaults without changing source code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from xtalflow.domain.instruments import (
    ECHO_650,
    INSTRUMENT_LABELS,
    SHIFTER_1,
    SHIFTER_2,
    WorksheetKind,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class InstrumentDestination:
    """A folder an instrument reads worksheets from, and the format it reads."""

    instrument: str
    worksheet: WorksheetKind
    output_directory: Path
    label: str = ""

    def __post_init__(self) -> None:
        if not self.instrument.strip() or any(c in self.instrument for c in "/\\"):
            raise ValueError(f"invalid instrument id: {self.instrument!r}")
        object.__setattr__(self, "worksheet", WorksheetKind(self.worksheet))
        object.__setattr__(self, "output_directory", Path(self.output_directory))
        if not self.label:
            object.__setattr__(
                self, "label", INSTRUMENT_LABELS.get(self.instrument, self.instrument)
            )


def standard_instruments(
    echo_directory: Path, shifter1_directory: Path, shifter2_directory: Path
) -> tuple[InstrumentDestination, ...]:
    """The BL-5C set: one ECHO 650 and two SHIFTER harvesting stations."""
    return (
        InstrumentDestination(ECHO_650, WorksheetKind.ECHO, echo_directory),
        InstrumentDestination(SHIFTER_1, WorksheetKind.SHIFTER, shifter1_directory),
        InstrumentDestination(SHIFTER_2, WorksheetKind.SHIFTER, shifter2_directory),
    )


@dataclass(frozen=True)
class ApplicationSettings:
    rmserver_root: Path
    fragment_library_directory: Path
    worksheet_staging_directory: Path
    instruments: tuple[InstrumentDestination, ...]
    create_missing_instrument_roots: bool
    require_network_instrument_mounts: bool = False
    review_database_filename: str = "reviews.sqlite3"
    mxlive_base_url: str | None = None
    mxlive_beamline: str = "BL-5C"
    mxlive_key_path: Path | None = None
    mxlive_ca_bundle: Path | None = None
    mxlive_timeout_seconds: float = 10.0
    mxlive_config_path: Path | None = None

    def __post_init__(self) -> None:
        ids = [destination.instrument for destination in self.instruments]
        if len(ids) != len(set(ids)):
            raise ValueError("instrument ids must be unique")

    def instrument(self, instrument_id: str) -> InstrumentDestination | None:
        return next(
            (item for item in self.instruments if item.instrument == instrument_id),
            None,
        )

    def with_instrument_directory(
        self, instrument_id: str, output_directory: Path
    ) -> ApplicationSettings:
        if self.instrument(instrument_id) is None:
            raise ValueError(f"instrument {instrument_id!r} is not configured")
        return replace(
            self,
            instruments=tuple(
                replace(item, output_directory=Path(output_directory))
                if item.instrument == instrument_id else item
                for item in self.instruments
            ),
        )


DEVELOPMENT_SETTINGS = ApplicationSettings(
    # Development default. Override with --root on an operating workstation.
    rmserver_root=PROJECT_ROOT / "tests" / "fixtures" / "rmserver",
    fragment_library_directory=PROJECT_ROOT / "chems",
    worksheet_staging_directory=PROJECT_ROOT / "tests" / "runtime" / "worksheets",
    instruments=standard_instruments(
        PROJECT_ROOT / "tests" / "runtime" / "smbmount" / "echo650",
        PROJECT_ROOT / "tests" / "runtime" / "smbmount" / "shifter1",
        PROJECT_ROOT / "tests" / "runtime" / "smbmount" / "shifter2",
    ),
    create_missing_instrument_roots=True,
)


OPERATING_SERVER_SETTINGS = ApplicationSettings(
    rmserver_root=Path("/smbmount/rmserver/RockMakerStorage/WellImages"),
    fragment_library_directory=PROJECT_ROOT / "chems",
    worksheet_staging_directory=Path("/tmp/xtalflow/worksheets"),
    instruments=standard_instruments(
        Path("/smbmount/echo650"),
        Path("/smbmount/shifter1"),
        Path("/smbmount/shifter2"),
    ),
    create_missing_instrument_roots=False,
    require_network_instrument_mounts=True,
    mxlive_base_url="https://mxlive.postech.ac.kr",
    mxlive_ca_bundle=Path("/etc/pki/tls/certs/ca-bundle.crt"),
    mxlive_config_path=Path("/etc/xtalflow/xtalflow.toml"),
    # Legacy resolves /data/users/{username}/.config/mxdc/keys.dsa. Supply the
    # user-specific path with --mxlive-key rather than embedding an account.
)


# Change this one assignment for an operating-server installation. CLI path
# arguments can still override individual directories.
DEFAULT_SETTINGS = DEVELOPMENT_SETTINGS


def with_instrument_output_policy(
    settings: ApplicationSettings, allow_local_instrument_directories: bool = False
) -> ApplicationSettings:
    """Treat instrument folders outside this repository as real network shares.

    Development defaults create missing folders under ``tests/runtime``.  Keeping
    that behavior for operating paths given on the command line would silently
    write worksheets to local disk when a share is not mounted.
    """
    if allow_local_instrument_directories:
        return replace(
            settings,
            create_missing_instrument_roots=True,
            require_network_instrument_mounts=False,
        )
    if all(
        _is_within_project(destination.output_directory)
        for destination in settings.instruments
    ):
        return settings
    return replace(
        settings,
        create_missing_instrument_roots=False,
        require_network_instrument_mounts=True,
    )


def _is_within_project(path: Path) -> bool:
    try:
        path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return True
