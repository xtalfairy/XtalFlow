from pathlib import Path

import pytest

from xtalflow.infrastructure.fragment_library_csv import (
    FragmentLibraryCsvError,
    load_fragment_library,
)


HEADER = "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"


def test_loads_legacy_library_csv_with_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "library.csv"
    path.write_text(
        "\ufeff" + HEADER + "Vendor,Lib,1,CMP-1,C2H6O,46.07,CCO,100,DMSO,SRC-1,a01\n",
        encoding="utf-8",
    )

    library = load_fragment_library(path)

    assert library.name == "library"
    assert library.fragments[0].compound_id == "CMP-1"
    assert library.fragments[0].source_well == "A01"


def test_reports_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "library.csv"
    path.write_text("Vendor,ID\nVendor,CMP-1\n", encoding="utf-8")

    with pytest.raises(FragmentLibraryCsvError, match="missing.*Plate_well"):
        load_fragment_library(path)


def test_reports_all_invalid_rows(tmp_path: Path) -> None:
    path = tmp_path / "library.csv"
    path.write_text(
        HEADER
        + "Vendor,Lib,1,CMP-1,C2H6O,bad,CCO,100,DMSO,SRC-1,A01\n"
        + "Vendor,Lib,2,CMP-2,C2H6O,46.07,CCO,0,DMSO,SRC-1,A02\n",
        encoding="utf-8",
    )

    with pytest.raises(FragmentLibraryCsvError) as error:
        load_fragment_library(path)

    assert "row 2" in str(error.value)
    assert "row 3" in str(error.value)
