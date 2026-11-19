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


@pytest.mark.parametrize(
    "encoding", ["cp949", "utf-16"], ids=["korean-windows-excel", "utf-16"]
)
def test_a_file_that_is_not_utf8_is_refused_by_name_not_raised(
    tmp_path: Path, encoding: str
) -> None:
    """Excel on a Korean Windows writes CP949 unless you pick "CSV UTF-8".

    This raised UnicodeDecodeError out of the header, past every caller, so
    one such file in the folder left fragment screening with no libraries at
    all and the researcher looking at "'utf-8' codec can't decode byte
    0xc6" - which names no file and says nothing about what to do.
    """
    path = tmp_path / "library.csv"
    path.write_bytes(
        (HEADER + "엔아민,Lib,1,Z1,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n").encode(
            encoding
        )
    )

    with pytest.raises(FragmentLibraryCsvError) as refused:
        load_fragment_library(path)

    assert "not UTF-8 text" in str(refused.value)
    assert "save it again as CSV UTF-8" in str(refused.value)


def test_a_row_problem_is_said_in_words_a_spreadsheet_explains(
    tmp_path: Path,
) -> None:
    """The empty row Excel leaves at the end of a file is the common one.

    It used to refuse the library with "row 3: [<class
    'decimal.ConversionSyntax'>]", which is Decimal describing itself, and
    the list then simply did not have that library in it.
    """
    path = tmp_path / "library.csv"
    path.write_text(
        HEADER
        + "Vendor,Lib,1,CMP-1,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n"
        + ",,,,,,,,,,\n",
        encoding="utf-8",
    )

    with pytest.raises(FragmentLibraryCsvError) as refused:
        load_fragment_library(path)

    assert "row 3" in str(refused.value)
    assert "a number column is empty or not a number" in str(refused.value)
    assert "ConversionSyntax" not in str(refused.value)

