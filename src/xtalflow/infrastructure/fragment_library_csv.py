from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from xtalflow.domain.fragment_screening import Fragment, FragmentLibrary


REQUIRED_COLUMNS = (
    "Vendor",
    "Library",
    "No",
    "ID",
    "Formula",
    "MW",
    "Smile",
    "Conc_mM",
    "Solvent",
    "Plate_ID",
    "Plate_well",
)


class FragmentLibraryCsvError(ValueError):
    pass


@dataclass(frozen=True)
class FragmentLibraryCatalogEntry:
    id: str
    file_name: str
    sha256: str
    imported_at: datetime
    row_count: int

    @property
    def display_name(self) -> str:
        return f"{self.file_name} · {self.row_count} rows"


def load_fragment_library(path: Path) -> FragmentLibrary:
    """One library, or ``FragmentLibraryCsvError`` saying what is wrong with it.

    Everything that can go wrong with somebody else's file leaves by that
    one door. It used not to: a file that is not UTF-8 - which is what Excel
    on a Korean Windows writes unless you pick "CSV UTF-8" - raised
    ``UnicodeDecodeError`` from the header, past every caller, and the only
    thing the researcher saw was "'utf-8' codec can't decode byte 0xc6",
    naming no file, on every fragment experiment they tried to open.
    """
    try:
        stream = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise FragmentLibraryCsvError(f"cannot open fragment library: {path}") from exc

    fragments: list[Fragment] = []
    errors: list[str] = []
    with stream:
        try:
            reader = csv.DictReader(stream)
            columns = tuple(reader.fieldnames or ())
            missing = [column for column in REQUIRED_COLUMNS if column not in columns]
            if missing:
                raise FragmentLibraryCsvError(
                    "missing fragment library columns: " + ", ".join(missing)
                )
            for row_number, row in enumerate(reader, start=2):
                try:
                    fragments.append(_fragment_from_row(row))
                except (ValueError, InvalidOperation) as exc:
                    errors.append(f"row {row_number}: {_said_plainly(exc)}")
        except UnicodeDecodeError as exc:
            raise FragmentLibraryCsvError(
                f"this file is not UTF-8 text ({exc.reason} at byte "
                f"{exc.start}); save it again as CSV UTF-8"
            ) from exc
        except csv.Error as exc:
            raise FragmentLibraryCsvError(f"this file is not readable as CSV: {exc}") from exc
        except OSError as exc:
            raise FragmentLibraryCsvError(f"cannot read fragment library: {exc}") from exc

    if errors:
        # Shown, so the file can be fixed: an empty row Excel leaves behind
        # at the end is a common one, and it used to take the whole library
        # out of the list with nothing said anywhere.
        raise FragmentLibraryCsvError("; ".join(errors[:3]) + (
            f"; and {len(errors) - 3} more rows" if len(errors) > 3 else ""
        ))
    try:
        return FragmentLibrary(path.stem, tuple(fragments))
    except ValueError as exc:
        raise FragmentLibraryCsvError(str(exc)) from exc


def _said_plainly(error: Exception) -> str:
    """A row's problem in words. Decimal says its own in class names.

    ``Decimal("")`` raises InvalidOperation whose str is
    "[<class 'decimal.ConversionSyntax'>]", which tells a researcher
    looking at a spreadsheet nothing at all.
    """
    if isinstance(error, InvalidOperation):
        return "a number column is empty or not a number"
    return str(error)


def _fragment_from_row(row: dict[str, str | None]) -> Fragment:
    values = {
        key: (row.get(key) or "").strip()
        for key in REQUIRED_COLUMNS
    }
    return Fragment(
        vendor=values["Vendor"],
        library=values["Library"],
        number=values["No"],
        compound_id=values["ID"],
        formula=values["Formula"],
        molecular_weight=Decimal(values["MW"]),
        smiles=values["Smile"],
        concentration_mm=Decimal(values["Conc_mM"]),
        solvent=values["Solvent"],
        source_plate=values["Plate_ID"],
        source_well=values["Plate_well"].upper(),
    )
