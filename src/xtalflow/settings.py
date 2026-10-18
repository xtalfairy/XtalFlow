"""Site-specific filesystem defaults for XtalFlow.

Edit this file when installing XtalFlow at a new beamline or workstation.  Command
line arguments may override the defaults without changing source code.
"""

from __future__ import annotations

import os
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

# Site configuration sits beside the installed version, not inside it, so that
# upgrading means putting the next version folder next to this one and removing
# the whole XtalFlow folder removes the installation with it:
#
#   /usr/local/fbdd_apps/XtalFlow/xtalflow.toml   <- instruments, MxLive accounts
#   /usr/local/fbdd_apps/XtalFlow/0.9/            <- this source tree
#
# A missing file is not an error: the built-in instruments and the local user
# name are used instead.
SITE_CONFIG_PATH = PROJECT_ROOT.parent / "xtalflow.toml"


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
    # Where worksheets are built before they are copied to the instruments.
    # None asks for a scratch folder of the running account's own, which is
    # what a machine with many researchers on it needs: a folder below one
    # they share would be created by whoever saves first, and that account's
    # umask would then decide whether anybody else can save at all.
    worksheet_staging_directory: Path | None
    instruments: tuple[InstrumentDestination, ...]
    create_missing_instrument_roots: bool
    # Where a worksheet waits when its instrument cannot be reached, so that
    # the experiment can still run on the instruments that are up. None keeps
    # it in the account's own home, which survives a reboot; a temporary
    # folder would not.
    pending_worksheet_directory: Path | None = None
    require_network_instrument_mounts: bool = False
    require_network_image_root: bool = False
    review_database_filename: str = "reviews.sqlite3"
    mxlive_base_url: str | None = None
    mxlive_beamline: str = "BL-5C"
    mxlive_key_path: Path | None = None
    mxlive_ca_bundle: Path | None = None
    mxlive_timeout_seconds: float = 10.0
    mxlive_config_path: Path | None = None
    examples_directory: Path | None = None

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
    pending_worksheet_directory=PROJECT_ROOT / "tests" / "runtime" / "pending",
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
    # Many researchers share this machine, so each gets a folder of their own.
    worksheet_staging_directory=None,
    instruments=standard_instruments(
        Path("/smbmount/echo650"),
        Path("/smbmount/shifter1"),
        Path("/smbmount/shifter2"),
    ),
    create_missing_instrument_roots=False,
    require_network_instrument_mounts=True,
    require_network_image_root=True,
    mxlive_base_url="https://mxlive.postech.ac.kr",
    mxlive_ca_bundle=Path("/etc/pki/tls/certs/ca-bundle.crt"),
    mxlive_config_path=SITE_CONFIG_PATH,
    # Legacy resolves /data/users/{username}/.config/mxdc/keys.dsa. Supply the
    # user-specific path with --mxlive-key rather than embedding an account.
)


SETTINGS_VARIABLE = "XTALFLOW_SETTINGS"
SETTINGS_CHOICES = {
    "development": DEVELOPMENT_SETTINGS,
    "operating": OPERATING_SERVER_SETTINGS,
}


def settings_from_environment(name: str | None = None) -> ApplicationSettings:
    """The installation this run belongs to, named by XTALFLOW_SETTINGS.

    An operating server exports XTALFLOW_SETTINGS=operating once, for every
    user, instead of each of them repeating the beamline paths on the command
    line. Nothing else has to be edited, so an installed version stays the
    same files as the commit it came from.
    """
    choice = (name if name is not None else os.environ.get(SETTINGS_VARIABLE, "")).strip().lower()
    if not choice:
        return DEVELOPMENT_SETTINGS
    if choice not in SETTINGS_CHOICES:
        raise ValueError(
            f"{SETTINGS_VARIABLE} must be one of "
            f"{', '.join(sorted(SETTINGS_CHOICES))}; got {choice!r}"
        )
    return SETTINGS_CHOICES[choice]


# Command line path arguments still override individual directories.
DEFAULT_SETTINGS = settings_from_environment()


def with_network_path_policy(
    settings: ApplicationSettings,
    allow_local_instrument_directories: bool = False,
    allow_local_image_root: bool = False,
) -> ApplicationSettings:
    """Treat folders outside this repository as real network shares.

    Development defaults create missing folders under ``tests/runtime`` and read
    images from a fixture folder. Keeping that for operating paths given on the
    command line would write worksheets to local disk when a share is not
    mounted, and would call a dropped image share a missing plate.
    """
    if allow_local_instrument_directories:
        settings = replace(
            settings,
            create_missing_instrument_roots=True,
            require_network_instrument_mounts=False,
        )
    elif not all(
        _is_within_project(destination.output_directory)
        for destination in settings.instruments
    ):
        settings = replace(
            settings,
            create_missing_instrument_roots=False,
            require_network_instrument_mounts=True,
        )
    if not allow_local_image_root and not _is_within_project(settings.rmserver_root):
        settings = replace(settings, require_network_image_root=True)
    return settings


def _is_within_project(path: Path) -> bool:
    try:
        path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return True
