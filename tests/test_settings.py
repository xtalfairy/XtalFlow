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


def test_the_requirements_file_holds_every_runtime_dependency() -> None:
    """Installing by the README must not miss one.

    The README installs requirements.txt and then the package with
    --no-deps, so anything in pyproject but not here is simply absent at
    run time - which is how pillow reached the beamline missing.
    """
    import re

    root = Path(__file__).resolve().parent.parent
    pinned = re.findall(
        r'^\s*"([A-Za-z0-9_.\-]+)[=<>;]',
        (root / "pyproject.toml").read_text().split("dependencies = [")[1].split("]")[0],
        re.MULTILINE,
    )
    listed = {
        re.split(r"[=<>;]", line, 1)[0].strip().casefold()
        for line in (root / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = [name for name in pinned if name.casefold() not in listed]
    assert not missing, f"in pyproject but not requirements.txt: {missing}"


def test_a_score_is_never_rounded_up_past_the_rule_it_is_judged_by() -> None:
    """89.5% showed as "90%" beside a rule that wanted 90: 38 fixture images sit there."""
    from xtalflow.text import counted, percent

    assert percent(0.895) == "89%"
    assert percent(0.8999) == "89%"
    assert percent(0.9) == "90%"
    assert percent(1.0) == "100%"
    # English plurals, because the steps said "2 boundarys to confirm".
    assert counted(1, "boundary") == "1 boundary"
    assert counted(2, "boundary") == "2 boundaries"
    assert counted(2, "well") == "2 wells"
    assert counted(2, "box") == "2 boxes"


def test_bulk_confirming_does_not_promise_a_restriction_it_dropped() -> None:
    """The dialog said positions outside the outline would hold a well back.

    They stopped holding one back when the detected outline turned out to
    be smaller than the well often enough that the button read "Confirm
    detected boundaries (0)" beside a list of wells waiting for it. Only
    the sentence in the dialog stayed behind, so the person was told fewer
    boundaries would be confirmed than were, and said yes to that.
    """
    from xtalflow.text import bulk_confirm_question

    question = bulk_confirm_question(12)

    assert "12 automatically detected boundaries" in question
    assert "1 automatically detected boundary?" in bulk_confirm_question(1)
    # It must not claim the outline decides which wells are included.
    assert "Only wells" not in question
    assert "does not hold a well back" in question
    # And it still says what is left out, which is a real exclusion.
    assert "no boundary detected" in question
    assert "set by hand" in question


def test_the_operating_preset_holds_the_stations_the_beamline_has() -> None:
    """Three SHIFTER stations since November 2026, numbered from 0.

    The beamline's own xtalflow.toml names them too, and that file is what
    actually runs. This is what runs when it is missing, renamed, or read
    with --mxlive-config pointed at a trial copy, so the two have to agree.
    Disagreeing does not fail: a save would quietly reach two stations
    instead of three, every lamp would look right, and the third station
    would simply never be asked for.
    """
    from xtalflow.settings import OPERATING_SERVER_SETTINGS

    assert [item.instrument for item in OPERATING_SERVER_SETTINGS.instruments] == [
        "echo650", "shifter0", "shifter1", "shifter2",
    ], "the order here is the order of the lamps, the table, and the saved rows"
    assert [str(item.output_directory) for item in OPERATING_SERVER_SETTINGS.instruments] == [
        "/smbmount/echo650",
        "/smbmount/shifter0",
        "/smbmount/shifter1",
        "/smbmount/shifter2",
    ]
    assert [item.label for item in OPERATING_SERVER_SETTINGS.instruments] == [
        "ECHO 650", "SHIFTER 0", "SHIFTER 1", "SHIFTER 2",
    ], "a station missing from INSTRUMENT_LABELS is drawn by its id"


def test_the_example_site_file_names_the_instruments_the_preset_holds() -> None:
    """The file staff copy, read as the loader reads it.

    The first version of this test wrote its own TOML and compared that to
    the preset, which cannot fail: it was checking a string against the
    string beside it. Meanwhile the file people actually copy still listed
    two stations, and copying it would have replaced the built-in set with a
    smaller one - the quiet failure this whole change exists to prevent.

    So the example file is the fixture now. If it and the preset ever
    disagree about which instruments exist, where they are, or what they are
    called, this fails, and whoever changed one has to change the other or
    say why.
    """
    from xtalflow.domain.instruments import WorksheetKind
    from xtalflow.infrastructure.instrument_config import (
        load_instrument_destinations,
    )
    from xtalflow.settings import OPERATING_SERVER_SETTINGS, PROJECT_ROOT

    example = PROJECT_ROOT / "xtalflow.example.toml"
    destinations = load_instrument_destinations(example)
    assert destinations is not None, "the example has to carry [[instruments]]"

    def described(items):
        return [
            (item.instrument, str(item.output_directory), item.label)
            for item in items
        ]

    assert described(destinations) == described(OPERATING_SERVER_SETTINGS.instruments)

    # An ECHO destination has to be among them, named exactly "echo650":
    # without one, saving a fragment plan is refused outright, and the
    # version installed beside this one asks for that id by name.
    echo = [item for item in destinations if item.worksheet is WorksheetKind.ECHO]
    assert [item.instrument for item in echo] == ["echo650"]

    # And every station's id has to contain "shifter", because the harvest
    # picks the worksheets to read back by that substring rather than by the
    # worksheet kind. A station named otherwise would be written to and then
    # never read from, which nothing would report.
    stations = [
        item for item in destinations if item.worksheet is WorksheetKind.SHIFTER
    ]
    assert len(stations) == 3
    assert all("shifter" in item.instrument for item in stations)
