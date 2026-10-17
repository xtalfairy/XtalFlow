from dataclasses import replace
from pathlib import Path

import pytest

from xtalflow.settings import (
    DEFAULT_SETTINGS,
    DEVELOPMENT_SETTINGS,
    OPERATING_SERVER_SETTINGS,
    PROJECT_ROOT,
    SITE_CONFIG_PATH,
    settings_from_environment,
    with_network_path_policy,
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
    # Site configuration is looked for beside the installed version, whichever
    # settings the build defaults to.
    assert defaults.mxlive_config == SITE_CONFIG_PATH
    assert SITE_CONFIG_PATH == PROJECT_ROOT.parent / "xtalflow.toml"
    assert OPERATING_SERVER_SETTINGS.mxlive_config_path == SITE_CONFIG_PATH


def test_image_root_outside_repository_must_be_a_network_share() -> None:
    development = with_network_path_policy(DEVELOPMENT_SETTINGS)
    operating = with_network_path_policy(
        replace(DEVELOPMENT_SETTINGS, rmserver_root=Path("/smbmount/rmserver"))
    )
    allowed = with_network_path_policy(
        replace(DEVELOPMENT_SETTINGS, rmserver_root=Path("/tmp/images")),
        allow_local_image_root=True,
    )

    assert not development.require_network_image_root
    assert operating.require_network_image_root
    assert not allowed.require_network_image_root
    assert OPERATING_SERVER_SETTINGS.require_network_image_root


def test_instrument_directories_outside_repository_must_be_network_shares() -> None:
    development = with_network_path_policy(DEVELOPMENT_SETTINGS)
    operating_paths = with_network_path_policy(
        DEVELOPMENT_SETTINGS.with_instrument_directory("echo650", Path("/smbmount/echo650"))
    )
    allowed = with_network_path_policy(
        DEVELOPMENT_SETTINGS.with_instrument_directory("echo650", Path("/tmp/echo650")),
        allow_local_instrument_directories=True,
    )

    assert development == DEVELOPMENT_SETTINGS
    assert not operating_paths.create_missing_instrument_roots
    assert operating_paths.require_network_instrument_mounts
    assert OPERATING_SERVER_SETTINGS.require_network_instrument_mounts
    assert allowed.create_missing_instrument_roots
    assert not allowed.require_network_instrument_mounts


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "xtalflow.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_site_config_instruments_are_used_and_cli_directories_override_them(
    tmp_path: Path,
) -> None:
    from xtalflow.viewer import settings_from_arguments

    config = _write_config(tmp_path, """
[[instruments]]
id = "echo650"
worksheet = "echo"
directory = "/smbmount/echo650"

[[instruments]]
id = "shifter1"
worksheet = "shifter"
directory = "/smbmount/shifter1"

[[instruments]]
id = "shifter3"
worksheet = "SHIFTER"
directory = "/smbmount/shifter3"
label = "SHIFTER 3 (hutch B)"
""")
    args = build_parser().parse_args(
        ["--mxlive-config", str(config), "--shifter1-dir", "/mnt/shifter1"]
    )

    settings = settings_from_arguments(args)

    assert [item.instrument for item in settings.instruments] == [
        "echo650", "shifter1", "shifter3"
    ]
    assert settings.instrument("shifter1").output_directory == Path("/mnt/shifter1")
    assert settings.instrument("shifter3").label == "SHIFTER 3 (hutch B)"
    assert settings.instrument("echo650").label == "ECHO 650"
    assert settings.require_network_instrument_mounts


def test_invalid_instrument_configuration_is_reported(tmp_path: Path) -> None:
    import pytest

    from xtalflow.infrastructure.instrument_config import (
        InstrumentConfigurationError,
        load_instrument_destinations,
    )
    from xtalflow.viewer import settings_from_arguments

    cases = {
        'id = "mosquito"\nworksheet = "pipette"\ndirectory = "/m"': "is invalid",
        'id = "shifter1"\nworksheet = "shifter"': "missing directory",
    }
    for body, message in cases.items():
        config = _write_config(tmp_path, f"[[instruments]]\n{body}\n")
        with pytest.raises(InstrumentConfigurationError, match=message):
            load_instrument_destinations(config)

    duplicate = _write_config(tmp_path, (
        '[[instruments]]\nid = "s"\nworksheet = "shifter"\ndirectory = "/a"\n'
        '[[instruments]]\nid = "s"\nworksheet = "shifter"\ndirectory = "/b"\n'
    ))
    with pytest.raises(InstrumentConfigurationError, match="unique: s"):
        load_instrument_destinations(duplicate)

    shifter_only = _write_config(tmp_path, (
        '[[instruments]]\nid = "shifter1"\nworksheet = "shifter"\ndirectory = "/a"\n'
    ))
    args = build_parser().parse_args(
        ["--mxlive-config", str(shifter_only), "--echo-dir", "/echo"]
    )
    with pytest.raises(ValueError, match="'echo650' is not configured"):
        settings_from_arguments(args)


def test_installation_is_chosen_by_the_environment() -> None:
    assert settings_from_environment("") is DEVELOPMENT_SETTINGS
    assert settings_from_environment("operating") is OPERATING_SERVER_SETTINGS
    assert settings_from_environment(" Operating ") is OPERATING_SERVER_SETTINGS
    assert DEFAULT_SETTINGS is DEVELOPMENT_SETTINGS  # nothing set while testing

    with pytest.raises(ValueError, match="XTALFLOW_SETTINGS"):
        settings_from_environment("beamline")


def test_the_suite_refuses_to_run_against_the_operating_folders(monkeypatch) -> None:
    """The guard that keeps a run on the server out of the real shares.

    /etc/profile.d/xtalflow.sh exports XTALFLOW_SETTINGS=operating for every
    login there, and the settings are decided when xtalflow is first imported.
    """
    import conftest
    import xtalflow.settings as settings_module

    monkeypatch.setattr(
        settings_module, "DEFAULT_SETTINGS", OPERATING_SERVER_SETTINGS
    )

    with pytest.raises(pytest.UsageError, match="outside the repository") as error:
        conftest.pytest_configure(None)

    assert "/smbmount/shifter1" in str(error.value)
    assert "/smbmount/rmserver/RockMakerStorage/WellImages" in str(error.value)
