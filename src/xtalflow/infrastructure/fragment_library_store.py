"""Fragment library CSV files imported into the review database."""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain.fragment_screening import Fragment, FragmentLibrary
from xtalflow.infrastructure.fragment_library_csv import (
    FragmentLibraryCatalogEntry,
    load_fragment_library as load_fragment_library_csv,
)


class SQLiteFragmentLibraryStore:
    """Imported fragment library catalog."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def import_fragment_library(
        self, path: Path | str
    ) -> FragmentLibraryCatalogEntry:
        source = Path(path)
        library = load_fragment_library_csv(source)
        try:
            digest = sha256(source.read_bytes()).hexdigest()
        except OSError as error:
            raise ReviewPersistenceError(
                f"could not read fragment library: {source}"
            ) from error
        existing = next(
            (
                item
                for item in self.list_fragment_libraries()
                if item.sha256 == digest
            ),
            None,
        )
        if existing is not None:
            return existing
        imported_at = datetime.now(timezone.utc)
        entry = FragmentLibraryCatalogEntry(
            digest,
            source.name,
            digest,
            imported_at,
            len(library.fragments),
        )
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO fragment_library_import(
                        library_import_id, file_name, sha256, imported_at, row_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.file_name,
                        entry.sha256,
                        entry.imported_at.isoformat(),
                        entry.row_count,
                    ),
                )
                self._connection.executemany(
                    """
                    INSERT INTO fragment_library_entry(
                        library_import_id, row_number, vendor, library_name,
                        compound_number, compound_id, formula, molecular_weight,
                        smiles, concentration_mm, solvent, source_plate, source_well
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            entry.id,
                            row_number,
                            fragment.vendor,
                            fragment.library,
                            fragment.number,
                            fragment.compound_id,
                            fragment.formula,
                            str(fragment.molecular_weight),
                            fragment.smiles,
                            str(fragment.concentration_mm),
                            fragment.solvent,
                            fragment.source_plate,
                            fragment.source_well,
                        )
                        for row_number, fragment in enumerate(
                            library.fragments, start=1
                        )
                    ),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not import fragment library"
            ) from error
        return entry

    def list_fragment_libraries(self) -> tuple[FragmentLibraryCatalogEntry, ...]:
        try:
            rows = self._connection.execute(
                """
                SELECT library_import_id, file_name, sha256, imported_at, row_count
                FROM fragment_library_import
                ORDER BY imported_at DESC, file_name
                """
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not list fragment libraries"
            ) from error
        return tuple(
            FragmentLibraryCatalogEntry(
                row[0], row[1], row[2], datetime.fromisoformat(row[3]), row[4]
            )
            for row in rows
        )

    def load_fragment_library(self, library_import_id: str) -> FragmentLibrary:
        try:
            metadata = self._connection.execute(
                """
                SELECT file_name FROM fragment_library_import
                WHERE library_import_id = ?
                """,
                (library_import_id,),
            ).fetchone()
            rows = self._connection.execute(
                """
                SELECT vendor, library_name, compound_number, compound_id,
                       formula, molecular_weight, smiles, concentration_mm,
                       solvent, source_plate, source_well
                FROM fragment_library_entry
                WHERE library_import_id = ? ORDER BY row_number
                """,
                (library_import_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not load fragment library"
            ) from error
        if metadata is None or not rows:
            raise ValueError("fragment library does not exist")
        fragments = tuple(
            Fragment(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                Decimal(row[5]),
                row[6],
                Decimal(row[7]),
                row[8],
                row[9],
                row[10],
            )
            for row in rows
        )
        return FragmentLibrary(Path(metadata[0]).stem, fragments)
