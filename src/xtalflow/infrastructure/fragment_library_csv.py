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


@dataclass(frozen=True, slots=True)
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
    try:
        stream = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise FragmentLibraryCsvError(f"cannot open fragment library: {path}") from exc

    with stream:
        reader = csv.DictReader(stream)
        columns = tuple(reader.fieldnames or ())
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise FragmentLibraryCsvError(
                "missing fragment library columns: " + ", ".join(missing)
            )

        fragments: list[Fragment] = []
        errors: list[str] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                fragments.append(_fragment_from_row(row))
            except (ValueError, InvalidOperation) as exc:
                errors.append(f"row {row_number}: {exc}")

    if errors:
        raise FragmentLibraryCsvError("; ".join(errors))
    try:
        return FragmentLibrary(path.stem, tuple(fragments))
    except ValueError as exc:
        raise FragmentLibraryCsvError(str(exc)) from exc


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
