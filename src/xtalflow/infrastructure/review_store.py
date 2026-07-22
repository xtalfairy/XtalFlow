from __future__ import annotations

import sqlite3
from hashlib import sha256
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain import (
    CalibrationMethod,
    CrystalSelection,
    ExperimentPlan,
    ExperimentProject,
    ImageCalibration,
    PlanType,
    Project,
    ProjectImageSet,
    ReviewPreferences,
    ReviewProgress,
    SelectedWell,
    SelectedWellUsage,
    SoakingPosition,
    TargetPoint,
)
from xtalflow.domain.fragment_screening import Fragment, FragmentLibrary
from xtalflow.domain.plan_lifecycle import (
    PlanningDraft,
    PlanRevision,
    WebDBUploadEvent,
    WorksheetExportEvent,
)
from xtalflow.infrastructure.fragment_library_csv import (
    FragmentLibraryCatalogEntry,
    load_fragment_library as load_fragment_library_csv,
)
from xtalflow.infrastructure.review_migrations import migrate_review_database


class SQLiteReviewStore:
    """Persist one authoritative target snapshot per logical image."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._closed = False
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
            self._connection.execute("PRAGMA foreign_keys = ON")
            migrate_review_database(self._connection)
        except (OSError, sqlite3.Error) as error:
            raise ReviewPersistenceError(
                f"cannot open review database: {self.database_path}"
            ) from error

    def save_review_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None:
        try:
            with self._connection:
                self._upsert_review_state(progress, preferences)
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save review state") from error

    def save_experiment_project(self, project: ExperimentProject) -> None:
        """Persist the complete selected-well snapshot and its single plan."""
        selection = project.crystal_selection
        plan = project.plan
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT OR IGNORE INTO experiment_project VALUES (?, ?, ?, ?)",
                    (project.id, project.name, project.created_at.isoformat(),
                     project.updated_at.isoformat()),
                )
                self._connection.execute(
                    """UPDATE experiment_project
                       SET name = ?, updated_at = ? WHERE project_id = ?""",
                    (project.name, project.updated_at.isoformat(), project.id),
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO crystal_selection VALUES (?, ?, ?, ?)",
                    (selection.id, project.id, selection.created_at.isoformat(),
                     selection.updated_at.isoformat()),
                )
                self._connection.execute(
                    """UPDATE crystal_selection SET updated_at = ?
                       WHERE selection_id = ?""",
                    (selection.updated_at.isoformat(), selection.id),
                )
                self._connection.execute(
                    "DELETE FROM selected_well WHERE selection_id = ?",
                    (selection.id,),
                )
                for well in selection.wells:
                    self._connection.execute(
                        """INSERT INTO selected_well VALUES (
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                           )""",
                        (
                            well.id, selection.id, well.image_set_id,
                            well.image_key, well.image_path, well.plate_code,
                            well.well_address, well.batch_id, well.profile,
                            well.plate_format_id, well.plate_format_version,
                            well.selection_order, well.selected_at.isoformat(),
                        ),
                    )
                    self._connection.executemany(
                        "INSERT INTO soaking_position VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            (
                                position.id, well.id, position.source_target_id,
                                position.position_order, str(position.x_mm),
                                str(position.y_mm), position.selected_at.isoformat(),
                            )
                            for position in well.soaking_positions
                        ),
                    )
                self._connection.execute(
                    "INSERT OR IGNORE INTO experiment_plan VALUES (?, ?, ?, ?, ?)",
                    (plan.id, project.id, plan.plan_type.value,
                     plan.created_at.isoformat(), plan.updated_at.isoformat()),
                )
                self._connection.execute(
                    """UPDATE experiment_plan
                       SET plan_type = ?, updated_at = ? WHERE plan_id = ?""",
                    (plan.plan_type.value, plan.updated_at.isoformat(), plan.id),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not save experiment project"
            ) from error

    def load_experiment_projects(self) -> tuple[ExperimentProject, ...]:
        try:
            project_rows = self._connection.execute(
                """SELECT project_id, name, created_at, updated_at
                   FROM experiment_project ORDER BY created_at, project_id"""
            ).fetchall()
            selection_rows = self._connection.execute(
                """SELECT selection_id, project_id, created_at, updated_at
                   FROM crystal_selection"""
            ).fetchall()
            well_rows = self._connection.execute(
                """SELECT selected_well_id, selection_id, image_set_id,
                          image_key, image_path, plate_code, well_address,
                          batch_id, profile, plate_format_id,
                          plate_format_version, selection_order, selected_at
                   FROM selected_well ORDER BY selection_id, selection_order"""
            ).fetchall()
            position_rows = self._connection.execute(
                """SELECT position_id, selected_well_id, source_target_id,
                          position_order, x_mm, y_mm, selected_at
                   FROM soaking_position
                   ORDER BY selected_well_id, position_order"""
            ).fetchall()
            plan_rows = self._connection.execute(
                """SELECT plan_id, project_id, plan_type, created_at, updated_at
                   FROM experiment_plan"""
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not load experiment projects"
            ) from error

        positions_by_well: dict[str, list[SoakingPosition]] = {}
        for row in position_rows:
            positions_by_well.setdefault(row[1], []).append(
                SoakingPosition(
                    row[0], row[1], row[2], row[3], Decimal(row[4]),
                    Decimal(row[5]), datetime.fromisoformat(row[6]),
                )
            )
        wells_by_selection: dict[str, list[SelectedWell]] = {}
        for row in well_rows:
            wells_by_selection.setdefault(row[1], []).append(
                SelectedWell(
                    id=row[0], crystal_selection_id=row[1], image_set_id=row[2],
                    image_key=row[3], image_path=row[4], plate_code=row[5],
                    well_address=row[6], batch_id=row[7], profile=row[8],
                    plate_format_id=row[9], plate_format_version=row[10],
                    selection_order=row[11], selected_at=datetime.fromisoformat(row[12]),
                    soaking_positions=tuple(positions_by_well.get(row[0], [])),
                )
            )
        selections = {
            row[1]: CrystalSelection(
                row[0], row[1], tuple(wells_by_selection.get(row[0], [])),
                datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]),
            )
            for row in selection_rows
        }
        plans = {
            row[1]: ExperimentPlan(
                row[0], row[1], PlanType(row[2]),
                datetime.fromisoformat(row[3]), datetime.fromisoformat(row[4]),
            )
            for row in plan_rows
        }
        return tuple(
            ExperimentProject(
                row[0], row[1], selections[row[0]], plans[row[0]],
                datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]),
            )
            for row in project_rows
        )

    def load_experiment_project(
        self, project_id: str
    ) -> ExperimentProject | None:
        return next(
            (
                project for project in self.load_experiment_projects()
                if project.id == project_id
            ),
            None,
        )

    def selected_well_usage(
        self, image_keys: tuple[str, ...]
    ) -> dict[str, tuple[SelectedWellUsage, ...]]:
        if not image_keys:
            return {}
        placeholders = ",".join("?" for _ in image_keys)
        try:
            rows = self._connection.execute(
                f"""SELECT well.image_key, project.project_id, project.name,
                           plan.plan_type,
                           CASE
                             WHEN EXISTS (
                               SELECT 1 FROM webdb_upload_event AS upload
                               JOIN plan_revision AS revision
                                 ON revision.revision_id = upload.revision_id
                               WHERE revision.plan_id = project.project_id
                             ) THEN 'Uploaded'
                             WHEN EXISTS (
                               SELECT 1 FROM plan_revision AS revision
                               WHERE revision.plan_id = project.project_id
                             ) THEN 'Finalized'
                             ELSE 'Draft'
                           END
                    FROM selected_well AS well
                    JOIN crystal_selection AS selection
                      ON selection.selection_id = well.selection_id
                    JOIN experiment_project AS project
                      ON project.project_id = selection.project_id
                    JOIN experiment_plan AS plan
                      ON plan.project_id = project.project_id
                    WHERE well.image_key IN ({placeholders})
                    ORDER BY project.created_at, project.project_id""",
                image_keys,
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not inspect selected-well usage"
            ) from error
        grouped: dict[str, list[SelectedWellUsage]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(
                SelectedWellUsage(row[1], row[2], PlanType(row[3]), row[4])
            )
        return {key: tuple(value) for key, value in grouped.items()}

    def save_checkpoint(
        self,
        image_key: str,
        targets: tuple[TargetPoint, ...],
        progress: ReviewProgress,
        preferences: ReviewPreferences,
        mark_reviewed: bool = False,
    ) -> None:
        if any(target.image_key != image_key for target in targets):
            raise ValueError("all targets must belong to the checkpoint image")
        try:
            with self._connection:
                self._replace_image_targets(image_key, targets)
                self._upsert_review_state(progress, preferences)
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save review checkpoint") from error

    def load_review_state(
        self, plan_key: str
    ) -> tuple[ReviewProgress, ReviewPreferences] | None:
        try:
            row = self._connection.execute(
                """
                SELECT plan_key, plate_code, batch_id, profile, auto_advance_target_count,
                       current_image_key, created_at, updated_at
                FROM review_plan WHERE plan_key = ?
                """,
                (plan_key,),
            ).fetchone()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load review state") from error
        if row is None:
            return None
        return (
            ReviewProgress(
                plan_key=row[0],
                plate_code=row[1],
                batch_id=row[2],
                profile=row[3],
                current_image_key=row[5],
                created_at=datetime.fromisoformat(row[6]),
                updated_at=datetime.fromisoformat(row[7]),
            ),
            ReviewPreferences(auto_advance_target_count=row[4]),
        )

    def save_image(self, image_key: str, targets: tuple[TargetPoint, ...]) -> None:
        if any(target.image_key != image_key for target in targets):
            raise ValueError("all targets must belong to the saved image")
        try:
            with self._connection:
                self._replace_image_targets(image_key, targets)
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save image targets") from error

    def load_images(self, image_keys: tuple[str, ...]) -> tuple[TargetPoint, ...]:
        if not image_keys:
            return ()
        placeholders = ",".join("?" for _ in image_keys)
        try:
            rows = self._connection.execute(
                f"SELECT target_id, image_key, x_px, y_px, selected_at "
                f"FROM target_point WHERE image_key IN ({placeholders}) "
                f"ORDER BY selected_at, rowid",
                image_keys,
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load image targets") from error
        return tuple(
            TargetPoint(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]))
            for row in rows
        )

    def load_reviewed_images(self, image_keys: tuple[str, ...]) -> tuple[str, ...]:
        return ()

    def _replace_image_targets(
        self, image_key: str, targets: tuple[TargetPoint, ...]
    ) -> None:
        self._connection.execute(
            "DELETE FROM target_point WHERE image_key = ?", (image_key,)
        )
        self._connection.executemany(
            "INSERT INTO target_point(target_id, image_key, x_px, y_px, selected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (
                    target.id,
                    target.image_key,
                    target.x_px,
                    target.y_px,
                    target.selected_at.isoformat(),
                )
                for target in targets
            ),
        )

    def _upsert_review_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None:
        values = (
            progress.plan_key, progress.plate_code, progress.batch_id,
            progress.profile, preferences.auto_advance_target_count,
            progress.current_image_key, progress.created_at.isoformat(),
            progress.updated_at.isoformat(),
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO review_plan(
                plan_key, plate_code, batch_id, profile, auto_advance_target_count,
                current_image_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        self._connection.execute(
            """UPDATE review_plan SET auto_advance_target_count = ?,
                      current_image_key = ?, updated_at = ?
               WHERE plan_key = ?""",
            (values[4], values[5], values[7], values[0]),
        )

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

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

    def save_project(self, project: Project) -> None:
        try:
            with self._connection:
                project_values = (
                    project.id, project.name, project.active_image_set_id,
                    project.created_at.isoformat(), project.updated_at.isoformat(),
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO project(
                        project_id, name, active_image_set_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    project_values,
                )
                self._connection.execute(
                    """UPDATE project SET name = ?, active_image_set_id = ?,
                              updated_at = ? WHERE project_id = ?""",
                    (project_values[1], project_values[2], project_values[4],
                     project_values[0]),
                )
                for image_set in project.image_sets:
                    image_set_values = (
                        image_set.id, image_set.project_id, image_set.plate_code,
                        image_set.batch_id, image_set.profile,
                        image_set.display_order, image_set.active_image_key,
                        image_set.created_at.isoformat(),
                        image_set.archived_at.isoformat()
                        if image_set.archived_at else None,
                        image_set.plate_format_id, image_set.plate_format_version,
                    )
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO project_image_set(
                            image_set_id, project_id, plate_code, batch_id, profile,
                            display_order, active_image_key, created_at, archived_at,
                            plate_format_id, plate_format_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        image_set_values,
                    )
                    self._connection.execute(
                        """UPDATE project_image_set
                           SET display_order = ?, active_image_key = ?,
                               archived_at = ?, plate_format_id = ?,
                               plate_format_version = ?
                           WHERE image_set_id = ?""",
                        (image_set_values[5], image_set_values[6],
                         image_set_values[8], image_set_values[9],
                         image_set_values[10], image_set_values[0]),
                    )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save project") from error

    def load_projects(self) -> tuple[Project, ...]:
        try:
            project_rows = self._connection.execute(
                "SELECT project_id, name, active_image_set_id, created_at, updated_at "
                "FROM project ORDER BY created_at, project_id"
            ).fetchall()
            image_set_rows = self._connection.execute(
                """
                SELECT image_set_id, project_id, plate_code, batch_id, profile,
                       display_order, active_image_key, created_at, archived_at,
                       plate_format_id, plate_format_version
                FROM project_image_set ORDER BY project_id, display_order
                """
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load projects") from error
        grouped: dict[str, list[ProjectImageSet]] = {}
        for row in image_set_rows:
            grouped.setdefault(row[1], []).append(
                ProjectImageSet(
                    id=row[0],
                    project_id=row[1],
                    plate_code=row[2],
                    batch_id=row[3],
                    profile=row[4],
                    display_order=row[5],
                    active_image_key=row[6],
                    created_at=datetime.fromisoformat(row[7]),
                    archived_at=datetime.fromisoformat(row[8]) if row[8] else None,
                    plate_format_id=row[9],
                    plate_format_version=row[10],
                )
            )
        return tuple(
            Project(
                id=row[0],
                name=row[1],
                active_image_set_id=row[2],
                created_at=datetime.fromisoformat(row[3]),
                updated_at=datetime.fromisoformat(row[4]),
                image_sets=grouped.get(row[0], []),
            )
            for row in project_rows
        )

    def scoped_to(self, image_set_id: str) -> SQLiteImageSetReviewStore:
        return SQLiteImageSetReviewStore(self, image_set_id)

    def target_count_for_image_set(self, image_set_id: str) -> int:
        try:
            return self._connection.execute(
                "SELECT COUNT(*) FROM image_set_target_point WHERE image_set_id = ?",
                (image_set_id,),
            ).fetchone()[0]
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not count image-set targets") from error

    def delete_targets(self, target_ids: tuple[str, ...]) -> None:
        if not target_ids:
            return
        placeholders = ",".join("?" for _ in target_ids)
        try:
            with self._connection:
                self._connection.execute(
                    f"DELETE FROM image_set_target_point "
                    f"WHERE target_id IN ({placeholders})",
                    target_ids,
                )
                # Imported standalone rows must also be removed or migration would
                # restore them the next time the database is opened.
                self._connection.execute(
                    f"DELETE FROM target_point WHERE target_id IN ({placeholders})",
                    target_ids,
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not delete selected targets") from error

    def save_last_open_project(self, project_id: str) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT OR IGNORE INTO app_state(state_key, state_value) "
                    "VALUES ('last_project_id', ?)",
                    (project_id,),
                )
                self._connection.execute(
                    "UPDATE app_state SET state_value = ? "
                    "WHERE state_key = 'last_project_id'", (project_id,)
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save active project") from error

    def load_last_open_project(self) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT state_value FROM app_state WHERE state_key = 'last_project_id'"
            ).fetchone()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load active project") from error
        return row[0] if row else None

    def save_planning_draft(self, draft: PlanningDraft) -> None:
        try:
            with self._connection:
                values = (
                    draft.id, draft.project_id, draft.plan_type, draft.name,
                    draft.library_id, draft.library_rows, draft.protein,
                    draft.volume_nl, draft.assignment_order,
                    draft.created_at.isoformat(), draft.updated_at.isoformat(),
                    draft.experiment_id,
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO planning_draft(
                        plan_id, project_id, plan_type, name, library_id,
                        library_rows, protein, volume_nl, assignment_order,
                        created_at, updated_at, experiment_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                self._connection.execute(
                    """UPDATE planning_draft
                       SET name = ?, library_id = ?, library_rows = ?,
                           protein = ?, volume_nl = ?, assignment_order = ?,
                           updated_at = ?, experiment_id = ? WHERE plan_id = ?""",
                    (values[3], values[4], values[5], values[6], values[7],
                     values[8], values[10], values[11], values[0]),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save planning draft") from error

    def load_planning_drafts(self, project_id: str) -> tuple[PlanningDraft, ...]:
        try:
            rows = self._connection.execute(
                """SELECT plan_id, project_id, plan_type, name, library_id,
                          library_rows, protein, volume_nl, assignment_order,
                          created_at, updated_at, experiment_id
                   FROM planning_draft WHERE project_id = ?
                   ORDER BY created_at, plan_id""",
                (project_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load planning drafts") from error
        return tuple(
            PlanningDraft(
                *row[:9], datetime.fromisoformat(row[9]),
                datetime.fromisoformat(row[10]), row[11]
            )
            for row in rows
        )

    def delete_planning_draft(self, plan_id: str) -> None:
        """Delete a plan unless an MxLive upload attempt must be audited."""
        try:
            with self._connection:
                upload_count = self._connection.execute(
                    """SELECT COUNT(*)
                       FROM webdb_upload_event AS upload
                       JOIN plan_revision AS revision
                         ON revision.revision_id = upload.revision_id
                       WHERE revision.plan_id = ?""",
                    (plan_id,),
                ).fetchone()[0]
                if upload_count:
                    raise ValueError(
                        "a plan with MxLive upload history cannot be deleted"
                    )
                self._connection.execute(
                    """DELETE FROM worksheet_export_event
                       WHERE revision_id IN (
                           SELECT revision_id FROM plan_revision WHERE plan_id = ?
                       )""",
                    (plan_id,),
                )
                self._connection.execute(
                    "DELETE FROM plan_revision WHERE plan_id = ?", (plan_id,)
                )
                self._connection.execute(
                    "DELETE FROM planning_draft WHERE plan_id = ?", (plan_id,)
                )
                self._connection.execute(
                    "DELETE FROM experiment_project WHERE project_id = ?",
                    (plan_id,),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not delete planning draft") from error

    def planning_plan_has_upload_history(self, plan_id: str) -> bool:
        try:
            row = self._connection.execute(
                """SELECT EXISTS(
                       SELECT 1
                       FROM webdb_upload_event AS upload
                       JOIN plan_revision AS revision
                         ON revision.revision_id = upload.revision_id
                       WHERE revision.plan_id = ?
                   )""",
                (plan_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not inspect planning upload history"
            ) from error
        return bool(row[0])

    def finalize_plan_revision(self, revision: PlanRevision) -> PlanRevision:
        try:
            with self._connection:
                next_number = self._connection.execute(
                    "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM plan_revision WHERE plan_id = ?",
                    (revision.plan_id,),
                ).fetchone()[0]
                saved = PlanRevision(
                    revision.id, revision.plan_id, next_number,
                    revision.experiment_id, revision.snapshot_json,
                    revision.finalized_by, revision.finalized_at,
                )
                self._connection.execute(
                    "INSERT INTO plan_revision VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        saved.id, saved.plan_id, saved.revision, saved.experiment_id,
                        saved.snapshot_json, saved.finalized_by,
                        saved.finalized_at.isoformat(),
                    ),
                )
                return saved
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not finalize plan revision") from error

    def list_plan_revisions(self, plan_id: str) -> tuple[PlanRevision, ...]:
        rows = self._connection.execute(
            """SELECT revision_id, plan_id, revision_number, experiment_id,
                      snapshot_json, finalized_by, finalized_at
               FROM plan_revision WHERE plan_id = ? ORDER BY revision_number""",
            (plan_id,),
        ).fetchall()
        return tuple(
            PlanRevision(*row[:6], datetime.fromisoformat(row[6])) for row in rows
        )

    def reserved_experiment_ids(self) -> set[str]:
        try:
            rows = self._connection.execute(
                "SELECT DISTINCT experiment_id FROM plan_revision"
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not list experiment ids") from error
        return {row[0] for row in rows}

    def record_worksheet_export(self, event: WorksheetExportEvent) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO worksheet_export_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id, event.revision_id, event.username,
                        event.exported_at.isoformat(), event.status, event.echo_path,
                        event.shifter1_path, event.shifter2_path, event.error_message,
                    ),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not record worksheet export") from error

    def list_worksheet_exports(self, revision_id: str) -> tuple[WorksheetExportEvent, ...]:
        rows = self._connection.execute(
            """SELECT export_id, revision_id, username, exported_at, status,
                      echo_path, shifter1_path, shifter2_path, error_message
               FROM worksheet_export_event WHERE revision_id = ? ORDER BY exported_at""",
            (revision_id,),
        ).fetchall()
        return tuple(
            WorksheetExportEvent(row[0], row[1], row[2], datetime.fromisoformat(row[3]), *row[4:])
            for row in rows
        )

    def record_webdb_upload(self, event: WebDBUploadEvent) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO webdb_upload_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id, event.revision_id, event.username,
                        event.account_id, event.endpoint,
                        event.attempted_at.isoformat(), event.status,
                        event.record_count, event.payload_json,
                        event.response_json, event.error_message,
                    ),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not record WebDB upload") from error

    def list_webdb_uploads(self, revision_id: str) -> tuple[WebDBUploadEvent, ...]:
        rows = self._connection.execute(
            """SELECT upload_id, revision_id, username, account_id, endpoint,
                      attempted_at, status, record_count, payload_json,
                      response_json, error_message
               FROM webdb_upload_event WHERE revision_id = ? ORDER BY attempted_at""",
            (revision_id,),
        ).fetchall()
        return tuple(
            WebDBUploadEvent(
                row[0], row[1], row[2], row[3], row[4],
                datetime.fromisoformat(row[5]), *row[6:]
            )
            for row in rows
        )


class SQLiteImageSetReviewStore:
    """Review persistence scoped to one project membership."""

    def __init__(self, workspace: SQLiteReviewStore, image_set_id: str) -> None:
        self.workspace = workspace
        self.image_set_id = image_set_id

    @property
    def _connection(self) -> sqlite3.Connection:
        return self.workspace._connection

    def save_review_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None:
        try:
            with self._connection:
                self._upsert_state(progress, preferences)
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save image-set review state") from error

    def save_checkpoint(
        self,
        image_key: str,
        targets: tuple[TargetPoint, ...],
        progress: ReviewProgress,
        preferences: ReviewPreferences,
        mark_reviewed: bool = False,
    ) -> None:
        if any(target.image_key != image_key for target in targets):
            raise ValueError("all targets must belong to the checkpoint image")
        try:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM image_set_target_point "
                    "WHERE image_set_id = ? AND image_key = ?",
                    (self.image_set_id, image_key),
                )
                self._connection.executemany(
                    "INSERT INTO image_set_target_point VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (
                            target.id,
                            self.image_set_id,
                            target.image_key,
                            target.x_px,
                            target.y_px,
                            target.selected_at.isoformat(),
                        )
                        for target in targets
                    ),
                )
                self._upsert_state(progress, preferences)
                if mark_reviewed:
                    reviewed_at = datetime.now(timezone.utc).isoformat()
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO image_set_image_review(
                            image_set_id, image_key, reviewed_at
                        )
                        VALUES (?, ?, ?)
                        """,
                        (self.image_set_id, image_key, reviewed_at),
                    )
                    self._connection.execute(
                        """UPDATE image_set_image_review SET reviewed_at = ?
                           WHERE image_set_id = ? AND image_key = ?""",
                        (reviewed_at, self.image_set_id, image_key),
                    )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save image-set checkpoint") from error

    def load_review_state(
        self, plan_key: str
    ) -> tuple[ReviewProgress, ReviewPreferences] | None:
        try:
            row = self._connection.execute(
                """
                SELECT p.plate_code, p.batch_id, p.profile,
                       s.auto_advance_target_count, s.current_image_key,
                       s.created_at, s.updated_at
                FROM image_set_review_state s
                JOIN project_image_set p ON p.image_set_id = s.image_set_id
                WHERE s.image_set_id = ?
                """,
                (self.image_set_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load image-set review state") from error
        if row is None:
            return None
        return (
            ReviewProgress(
                plan_key=plan_key,
                plate_code=row[0],
                batch_id=row[1],
                profile=row[2],
                current_image_key=row[4],
                created_at=datetime.fromisoformat(row[5]),
                updated_at=datetime.fromisoformat(row[6]),
            ),
            ReviewPreferences(row[3]),
        )

    def load_images(self, image_keys: tuple[str, ...]) -> tuple[TargetPoint, ...]:
        if not image_keys:
            return ()
        placeholders = ",".join("?" for _ in image_keys)
        try:
            rows = self._connection.execute(
                f"SELECT target_id, image_key, x_px, y_px, selected_at "
                f"FROM image_set_target_point WHERE image_set_id = ? "
                f"AND image_key IN ({placeholders}) ORDER BY selected_at, rowid",
                (self.image_set_id, *image_keys),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load image-set targets") from error
        return tuple(
            TargetPoint(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]))
            for row in rows
        )

    def load_reviewed_images(self, image_keys: tuple[str, ...]) -> tuple[str, ...]:
        if not image_keys:
            return ()
        placeholders = ",".join("?" for _ in image_keys)
        try:
            rows = self._connection.execute(
                f"SELECT image_key FROM image_set_image_review "
                f"WHERE image_set_id = ? AND image_key IN ({placeholders})",
                (self.image_set_id, *image_keys),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load reviewed images") from error
        return tuple(row[0] for row in rows)

    def save_calibration(self, calibration: ImageCalibration) -> None:
        try:
            with self._connection:
                values = (
                    self.image_set_id, calibration.image_key,
                    calibration.center_x_px, calibration.center_y_px,
                    calibration.radius_x_px, calibration.radius_y_px,
                    calibration.physical_diameter_mm, calibration.method.value,
                    calibration.confidence, int(calibration.confirmed),
                    calibration.updated_at.isoformat(),
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO image_set_image_calibration(
                        image_set_id, image_key, center_x_px, center_y_px,
                        radius_x_px, radius_y_px, physical_diameter_mm,
                        method, confidence, confirmed, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                self._connection.execute(
                    """UPDATE image_set_image_calibration
                       SET center_x_px = ?, center_y_px = ?, radius_x_px = ?,
                           radius_y_px = ?, physical_diameter_mm = ?, method = ?,
                           confidence = ?, confirmed = ?, updated_at = ?
                       WHERE image_set_id = ? AND image_key = ?""",
                    (*values[2:], values[0], values[1]),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save image calibration") from error

    def load_calibration(self, image_key: str) -> ImageCalibration | None:
        try:
            row = self._connection.execute(
                """
                SELECT image_key, center_x_px, center_y_px, radius_x_px, radius_y_px,
                       physical_diameter_mm, method, confidence, confirmed, updated_at
                FROM image_set_image_calibration
                WHERE image_set_id = ? AND image_key = ?
                """,
                (self.image_set_id, image_key),
            ).fetchone()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load image calibration") from error
        if row is None:
            return None
        return ImageCalibration(
            image_key=row[0],
            center_x_px=row[1],
            center_y_px=row[2],
            radius_x_px=row[3],
            radius_y_px=row[4],
            physical_diameter_mm=row[5],
            method=CalibrationMethod(row[6]),
            confidence=row[7],
            confirmed=bool(row[8]),
            updated_at=datetime.fromisoformat(row[9]),
        )

    def _upsert_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None:
        values = (
            self.image_set_id, preferences.auto_advance_target_count,
            progress.current_image_key, progress.created_at.isoformat(),
            progress.updated_at.isoformat(),
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO image_set_review_state(
                image_set_id, auto_advance_target_count, current_image_key,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
        self._connection.execute(
            """UPDATE image_set_review_state
               SET auto_advance_target_count = ?, current_image_key = ?,
                   updated_at = ? WHERE image_set_id = ?""",
            (values[1], values[2], values[4], values[0]),
        )
        self._connection.execute(
            "UPDATE project_image_set SET active_image_key = ? WHERE image_set_id = ?",
            (progress.current_image_key, self.image_set_id),
        )
