"""Site-specific filesystem defaults for XtalFlow.

Edit this file when installing XtalFlow at a new beamline or workstation.  Command
line arguments may override the defaults without changing source code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ApplicationSettings:
    rmserver_root: Path
    fragment_library_directory: Path
    worksheet_staging_directory: Path
    echo_output_directory: Path
    shifter1_output_directory: Path
    shifter2_output_directory: Path
    create_missing_instrument_roots: bool
    require_network_instrument_mounts: bool = False
    review_database_filename: str = "reviews.sqlite3"
    mxlive_base_url: str | None = None
    mxlive_beamline: str = "BL-5C"
    mxlive_key_path: Path | None = None
    mxlive_ca_bundle: Path | None = None
    mxlive_timeout_seconds: float = 10.0
    mxlive_config_path: Path | None = None


DEVELOPMENT_SETTINGS = ApplicationSettings(
    # Development default. Override with --root on an operating workstation.
    rmserver_root=PROJECT_ROOT / "tests" / "fixtures" / "rmserver",
    fragment_library_directory=PROJECT_ROOT / "chems",
    worksheet_staging_directory=PROJECT_ROOT / "tests" / "runtime" / "worksheets",
    echo_output_directory=PROJECT_ROOT
    / "tests"
    / "runtime"
    / "smbmount"
    / "echo650",
    shifter1_output_directory=PROJECT_ROOT
    / "tests"
    / "runtime"
    / "smbmount"
    / "shifter1",
    shifter2_output_directory=PROJECT_ROOT
    / "tests"
    / "runtime"
    / "smbmount"
    / "shifter2",
    create_missing_instrument_roots=True,
)


OPERATING_SERVER_SETTINGS = ApplicationSettings(
    rmserver_root=Path("/smbmount/rmserver/RockMakerStorage/WellImages"),
    fragment_library_directory=PROJECT_ROOT / "chems",
    worksheet_staging_directory=Path("/tmp/xtalflow/worksheets"),
    echo_output_directory=Path("/smbmount/echo650"),
    shifter1_output_directory=Path("/smbmount/shifter1"),
    shifter2_output_directory=Path("/smbmount/shifter2"),
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
    directories = (
        settings.echo_output_directory,
        settings.shifter1_output_directory,
        settings.shifter2_output_directory,
    )
    if all(_is_within_project(directory) for directory in directories):
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
