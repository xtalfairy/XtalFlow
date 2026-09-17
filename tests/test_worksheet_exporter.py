import csv
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from xtalflow.domain import SWISSCI_MIDI_3_LENS
from xtalflow.domain.fragment_screening import (
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.infrastructure.worksheet_exporter import (
    WorksheetDestinationUnavailable,
    WorksheetExporter,
)
from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments


def fragment_plan():
    fragment = Fragment(
        "Vendor",
        "Library",
        "1",
        "CMP-1",
        "C2H6O",
        Decimal("46.07"),
        "CCO",
        Decimal("100"),
        "DMSO",
        "SRC-1",
        "A01",
    )
    crystal = SelectedCrystal(
        "image",
        "2069",
        "A01d",
        (CrystalTarget("target", Decimal("0.25"), Decimal("-0.5"), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    return build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), (crystal,), Decimal("25")
    )


def test_development_export_writes_user_scoped_echo_and_shifter_files(
    tmp_path: Path,
) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    exporter = WorksheetExporter(settings, "scientist")

    first = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    second = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert first.path_for("echo650") == (
        tmp_path / "echo650" / "scientist" / "FragSC-202607-BRD4-01.csv"
    )
    assert first.path_for("echo650").is_file()
    assert first.path_for("shifter1").is_file()
    assert first.path_for("shifter2").is_file()
    assert second.file_stem == "FragSC-202607-BRD4-01_01"
    with first.path_for("shifter1").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    assert len(rows[0]) == 15
    assert len(rows[1]) == 15
    assert rows[1][6:] == [""] * 9


def test_missing_operating_mount_requires_explicit_alternate_root(
    tmp_path: Path,
) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "missing-echo",
            tmp_path / "missing-shifter1",
            tmp_path / "missing-shifter2",
        ),
        create_missing_instrument_roots=False,
    )
    exporter = WorksheetExporter(settings, "scientist")

    with pytest.raises(WorksheetDestinationUnavailable, match="unavailable"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    result = exporter.export(
        fragment_plan(), "FragSC-202607-BRD4-01", tmp_path / "chosen"
    )
    assert result.path_for("echo650") == (
        tmp_path
        / "chosen"
        / "echo650"
        / "scientist"
        / "FragSC-202607-BRD4-01.csv"
    )


def test_permission_error_preparing_output_is_reported_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    original_mkdir = Path.mkdir

    def deny_runtime_directory(path, *args, **kwargs):
        if path == tmp_path / "echo650" / "scientist":
            raise PermissionError("permission denied for test")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_runtime_directory)

    with pytest.raises(
        WorksheetDestinationUnavailable, match="could not prepare instrument"
    ):
        WorksheetExporter(settings, "scientist").export(
            fragment_plan(), "FragSC-202607-BRD4-01"
        )


def test_offline_instrument_share_is_reported_as_unavailable_at_export(
    tmp_path: Path, monkeypatch
) -> None:
    offline = tmp_path / "shifter1"
    settings = replace(
        DEFAULT_SETTINGS,
        instruments=standard_instruments(
            tmp_path / "echo650",
            offline,
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=False,
    )
    original_is_dir = Path.is_dir

    def host_down(path):
        if path == offline or path == offline / "scientist":
            raise OSError(112, "Host is down")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", host_down)
    exporter = WorksheetExporter(settings, "scientist")

    with pytest.raises(WorksheetDestinationUnavailable, match="unavailable"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")


def test_raw_crystal_export_writes_only_shifter_files(tmp_path: Path) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    fragment = fragment_plan()
    raw_plan = build_raw_crystal_plan(fragment.selection)

    result = WorksheetExporter(settings, "scientist").export(
        raw_plan, "RawCrystal-202607-BRD4-01"
    )

    assert result.path_for("shifter1").is_file()
    assert result.path_for("shifter2").is_file()
    assert not (tmp_path / "echo650").exists()


def _development_settings(tmp_path: Path):
    return replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )


def _worksheet_files(tmp_path: Path) -> list[Path]:
    return sorted(
        path
        for name in ("echo650", "shifter1", "shifter2")
        for path in (tmp_path / name).rglob("*")
        if path.is_file()
    )


def test_failed_copy_leaves_no_worksheet_and_retry_reuses_experiment_id(
    tmp_path: Path, monkeypatch
) -> None:
    import xtalflow.infrastructure.worksheet_exporter as exporter_module

    original_copyfile = exporter_module.shutil.copyfile

    def shifter1_share_drops(source, destination, **kwargs):
        if "shifter1" in str(destination):
            raise OSError(112, "Host is down")
        return original_copyfile(source, destination, **kwargs)

    exporter = WorksheetExporter(_development_settings(tmp_path), "scientist")
    monkeypatch.setattr(exporter_module.shutil, "copyfile", shifter1_share_drops)

    with pytest.raises(WorksheetDestinationUnavailable, match="Host is down"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert _worksheet_files(tmp_path) == []
    monkeypatch.setattr(exporter_module.shutil, "copyfile", original_copyfile)
    result = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    assert result.file_stem == "FragSC-202607-BRD4-01"
    assert _worksheet_files(tmp_path) == sorted(
        (result.path_for("echo650"), result.path_for("shifter1"), result.path_for("shifter2"))
    )


def test_failed_rename_removes_worksheets_already_published(
    tmp_path: Path, monkeypatch
) -> None:
    import xtalflow.infrastructure.worksheet_exporter as exporter_module

    original_replace = exporter_module.os.replace

    def shifter2_rename_fails(source, destination):
        if "shifter2" in str(destination):
            raise PermissionError("permission denied for test")
        return original_replace(source, destination)

    monkeypatch.setattr(exporter_module.os, "replace", shifter2_rename_fails)

    with pytest.raises(WorksheetDestinationUnavailable, match="permission denied"):
        WorksheetExporter(_development_settings(tmp_path), "scientist").export(
            fragment_plan(), "FragSC-202607-BRD4-01"
        )

    assert _worksheet_files(tmp_path) == []


def test_invalid_plan_does_not_leave_staging_that_blocks_export(
    tmp_path: Path, monkeypatch
) -> None:
    import xtalflow.infrastructure.worksheet_exporter as exporter_module

    def invalid_plan(plan):
        raise ValueError("invalid plan for test")

    exporter = WorksheetExporter(_development_settings(tmp_path), "scientist")
    with monkeypatch.context() as patch:
        patch.setattr(exporter_module, "build_echo_worksheet", invalid_plan)
        with pytest.raises(ValueError, match="invalid plan"):
            exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    result = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert result.file_stem == "FragSC-202607-BRD4-01"
    assert list((tmp_path / "staging" / "scientist").iterdir()) == []


def test_unmounted_share_directory_is_not_used_for_instrument_output(
    tmp_path: Path, monkeypatch
) -> None:
    settings = replace(
        _development_settings(tmp_path),
        create_missing_instrument_roots=False,
        require_network_instrument_mounts=True,
    )
    for name in ("echo650", "shifter1", "shifter2"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(
        "xtalflow.infrastructure.worksheet_exporter.is_network_mount",
        lambda path: path.name != "shifter2",
    )

    with pytest.raises(
        WorksheetDestinationUnavailable, match="shifter2 \\(network share is not mounted\\)"
    ):
        WorksheetExporter(settings, "scientist").export(
            fragment_plan(), "FragSC-202607-BRD4-01"
        )

    assert _worksheet_files(tmp_path) == []


def _configured_settings(tmp_path: Path, instruments):
    return replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=instruments,
        create_missing_instrument_roots=True,
    )


def test_each_worksheet_goes_to_every_instrument_that_reads_it(tmp_path: Path) -> None:
    from xtalflow.domain.instruments import WorksheetKind
    from xtalflow.settings import InstrumentDestination

    instruments = (
        *standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        InstrumentDestination("shifter3", WorksheetKind.SHIFTER, tmp_path / "shifter3"),
    )
    exporter = WorksheetExporter(_configured_settings(tmp_path, instruments), "scientist")

    fragment = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    raw = exporter.export(
        build_raw_crystal_plan(fragment_plan().selection), "RawCrystal-202607-BRD4-01"
    )
    alternate = exporter.export(
        fragment_plan(), "FragSC-202607-BRD4-02", tmp_path / "chosen"
    )

    assert [output.instrument for output in fragment.outputs] == [
        "echo650", "shifter1", "shifter2", "shifter3"
    ]
    assert fragment.path_for("shifter3").read_text(encoding="utf-8") == (
        fragment.path_for("shifter1").read_text(encoding="utf-8")
    )
    assert fragment.path_for("echo650").read_text(encoding="utf-8") != (
        fragment.path_for("shifter1").read_text(encoding="utf-8")
    )
    assert [output.instrument for output in raw.outputs] == [
        "shifter1", "shifter2", "shifter3"
    ]
    assert alternate.path_for("shifter3") == (
        tmp_path / "chosen" / "shifter3" / "scientist" / "FragSC-202607-BRD4-02.csv"
    )


def test_plan_needing_an_unconfigured_worksheet_is_not_partly_exported(
    tmp_path: Path,
) -> None:
    from xtalflow.domain.instruments import WorksheetKind
    from xtalflow.settings import InstrumentDestination

    shifter_only = (
        InstrumentDestination("shifter1", WorksheetKind.SHIFTER, tmp_path / "shifter1"),
    )
    exporter = WorksheetExporter(_configured_settings(tmp_path, shifter_only), "scientist")

    with pytest.raises(
        WorksheetDestinationUnavailable, match="no instrument is configured for echo"
    ):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert not (tmp_path / "shifter1").exists()
    raw = exporter.export(
        build_raw_crystal_plan(fragment_plan().selection), "RawCrystal-202607-BRD4-01"
    )
    assert [output.instrument for output in raw.outputs] == ["shifter1"]
