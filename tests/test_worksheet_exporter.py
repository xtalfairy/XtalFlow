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
from xtalflow.settings import DEFAULT_SETTINGS


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
        echo_output_directory=tmp_path / "echo650",
        shifter1_output_directory=tmp_path / "shifter1",
        shifter2_output_directory=tmp_path / "shifter2",
        create_missing_instrument_roots=True,
    )
    exporter = WorksheetExporter(settings, "scientist")

    first = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")
    second = exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    assert first.echo_path == (
        tmp_path / "echo650" / "scientist" / "FragSC-202607-BRD4-01.csv"
    )
    assert first.echo_path.is_file()
    assert first.shifter1_path.is_file()
    assert first.shifter2_path.is_file()
    assert second.file_stem == "FragSC-202607-BRD4-01_01"
    with first.shifter1_path.open(newline="", encoding="utf-8") as stream:
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
        echo_output_directory=tmp_path / "missing-echo",
        shifter1_output_directory=tmp_path / "missing-shifter1",
        shifter2_output_directory=tmp_path / "missing-shifter2",
        create_missing_instrument_roots=False,
    )
    exporter = WorksheetExporter(settings, "scientist")

    with pytest.raises(WorksheetDestinationUnavailable, match="unavailable"):
        exporter.export(fragment_plan(), "FragSC-202607-BRD4-01")

    result = exporter.export_to_alternate_root(
        fragment_plan(), "FragSC-202607-BRD4-01", tmp_path / "chosen"
    )
    assert result.echo_path == (
        tmp_path
        / "chosen"
        / "echo650"
        / "scientist"
        / "FragSC-202607-BRD4-01.csv"
    )


def test_raw_crystal_export_writes_only_shifter_files(tmp_path: Path) -> None:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        echo_output_directory=tmp_path / "echo650",
        shifter1_output_directory=tmp_path / "shifter1",
        shifter2_output_directory=tmp_path / "shifter2",
        create_missing_instrument_roots=True,
    )
    fragment = fragment_plan()
    raw_plan = build_raw_crystal_plan(
        tuple(assignment.crystal for assignment in fragment.assignments)
    )

    result = WorksheetExporter(settings, "scientist").export_shifter(
        raw_plan, "RawCrystal-202607-BRD4-01"
    )

    assert result.shifter1_path.is_file()
    assert result.shifter2_path.is_file()
    assert not (tmp_path / "echo650").exists()
