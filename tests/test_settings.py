from pathlib import Path

from xtalflow.settings import (
    DEFAULT_SETTINGS,
    DEVELOPMENT_SETTINGS,
    OPERATING_SERVER_SETTINGS,
    PROJECT_ROOT,
    with_instrument_output_policy,
)
from xtalflow.viewer import build_parser


def test_development_paths_are_centralized_in_settings() -> None:
    assert DEFAULT_SETTINGS.rmserver_root == (
        PROJECT_ROOT / "tests" / "fixtures" / "rmserver"
    )
    assert DEFAULT_SETTINGS.fragment_library_directory == PROJECT_ROOT / "chems"
    assert DEFAULT_SETTINGS.worksheet_staging_directory == (
        PROJECT_ROOT / "tests" / "runtime" / "worksheets"
    )


def test_operating_mxlive_url_uses_certificate_hostname() -> None:
    assert OPERATING_SERVER_SETTINGS.mxlive_base_url == (
        "https://mxlive.postech.ac.kr"
    )


def test_cli_uses_central_defaults_and_allows_site_overrides() -> None:
    defaults = build_parser().parse_args([])
    overridden = build_parser().parse_args(
        ["--root", "/rm", "--library-dir", "/libraries", "--echo-dir", "/echo",
         "--mxlive-url", "https://mxlive.example", "--mxlive-key", "/keys.dsa"]
    )

    assert defaults.root == DEFAULT_SETTINGS.rmserver_root
    assert defaults.library_dir == DEFAULT_SETTINGS.fragment_library_directory
    assert str(overridden.root) == "/rm"
    assert str(overridden.library_dir) == "/libraries"
    assert str(overridden.echo_dir) == "/echo"
    assert overridden.mxlive_url == "https://mxlive.example"
    assert str(overridden.mxlive_key) == "/keys.dsa"
    assert defaults.mxlive_config == DEFAULT_SETTINGS.mxlive_config_path


def test_instrument_directories_outside_repository_must_be_network_shares() -> None:
    from dataclasses import replace

    development = with_instrument_output_policy(DEVELOPMENT_SETTINGS)
    operating_paths = with_instrument_output_policy(
        replace(DEVELOPMENT_SETTINGS, echo_output_directory=Path("/smbmount/echo650"))
    )
    allowed = with_instrument_output_policy(
        replace(DEVELOPMENT_SETTINGS, echo_output_directory=Path("/tmp/echo650")),
        allow_local_instrument_directories=True,
    )

    assert development == DEVELOPMENT_SETTINGS
    assert not operating_paths.create_missing_instrument_roots
    assert operating_paths.require_network_instrument_mounts
    assert OPERATING_SERVER_SETTINGS.require_network_instrument_mounts
    assert allowed.create_missing_instrument_roots
    assert not allowed.require_network_instrument_mounts
