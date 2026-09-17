"""Experiment projects, plan drafts, finalized revisions, and experiment IDs."""

from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from xtalflow.application import ReviewPersistenceError
from xtalflow.application.planning_service import selection_from_snapshot
from xtalflow.domain import (
    CrystalSelection,
    ExperimentPlan,
    ExperimentProject,
    PlanType,
    SelectedWell,
    SelectedWellUsage,
    SoakingPosition,
)
from xtalflow.domain.plan_lifecycle import PlanningDraft, PlanRevision


@dataclass(frozen=True)
class PlanningProjectMigrationReport:
    migrated: int
    skipped_existing: int
    legacy_drafts_without_revision: int
    invalid_snapshots: tuple[tuple[str, str], ...]


class SQLitePlanningStore:
    """Selected-well snapshots and the plans built on them."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def migrate_finalized_planning_projects(
        self,
    ) -> PlanningProjectMigrationReport:
        """Copy recoverable legacy plan snapshots into the new aggregate."""
        rows = self._connection.execute(
            """SELECT draft.plan_id, draft.name, draft.plan_type,
                      draft.created_at, draft.updated_at,
                      revision.snapshot_json
               FROM planning_draft AS draft
               JOIN plan_revision AS revision
                 ON revision.plan_id = draft.plan_id
               WHERE revision.revision_number = (
                   SELECT MAX(latest.revision_number)
                   FROM plan_revision AS latest
                   WHERE latest.plan_id = draft.plan_id
               )
               ORDER BY draft.created_at, draft.plan_id"""
        ).fetchall()
        existing_ids = {
            row[0] for row in self._connection.execute(
                "SELECT project_id FROM experiment_project"
            ).fetchall()
        }
        draft_count = self._connection.execute(
            "SELECT COUNT(*) FROM planning_draft"
        ).fetchone()[0]
        migrated = 0
        skipped = 0
        invalid: list[tuple[str, str]] = []
        for row in rows:
            plan_id = row[0]
            if plan_id in existing_ids:
                skipped += 1
                continue
            try:
                project = _project_from_legacy_snapshot(*row)
                self.save_experiment_project(project)
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                ReviewPersistenceError,
            ) as error:
                invalid.append((plan_id, str(error)))
                continue
            existing_ids.add(plan_id)
            migrated += 1
        return PlanningProjectMigrationReport(
            migrated,
            skipped,
            max(draft_count - len(rows), 0),
            tuple(invalid),
        )

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
        return self._load_experiment_projects(None)

    def load_experiment_project(
        self, project_id: str
    ) -> ExperimentProject | None:
        projects = self._load_experiment_projects(project_id)
        return projects[0] if projects else None

    def _load_experiment_projects(
        self, project_id: str | None
    ) -> tuple[ExperimentProject, ...]:
        # Load one project on its own so a damaged record cannot hide the others.
        project_filter = "" if project_id is None else " WHERE project_id = ?"
        selection_filter = "" if project_id is None else (
            " WHERE selection_id IN (SELECT selection_id FROM crystal_selection"
            " WHERE project_id = ?)"
        )
        parameters = () if project_id is None else (project_id,)
        try:
            project_rows = self._connection.execute(
                "SELECT project_id, name, created_at, updated_at FROM experiment_project"
                f"{project_filter} ORDER BY created_at, project_id",
                parameters,
            ).fetchall()
            selection_rows = self._connection.execute(
                "SELECT selection_id, project_id, created_at, updated_at"
                f" FROM crystal_selection{project_filter}",
                parameters,
            ).fetchall()
            well_rows = self._connection.execute(
                """SELECT selected_well_id, selection_id, image_set_id,
                          image_key, image_path, plate_code, well_address,
                          batch_id, profile, plate_format_id,
                          plate_format_version, selection_order, selected_at
                   FROM selected_well"""
                f"{selection_filter} ORDER BY selection_id, selection_order",
                parameters,
            ).fetchall()
            position_rows = self._connection.execute(
                """SELECT position_id, selected_well_id, source_target_id,
                          position_order, x_mm, y_mm, selected_at
                   FROM soaking_position"""
                + (
                    "" if project_id is None else
                    " WHERE selected_well_id IN (SELECT selected_well_id FROM"
                    " selected_well" + selection_filter + ")"
                )
                + " ORDER BY selected_well_id, position_order",
                parameters,
            ).fetchall()
            plan_rows = self._connection.execute(
                "SELECT plan_id, project_id, plan_type, created_at, updated_at"
                f" FROM experiment_plan{project_filter}",
                parameters,
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError(
                "could not load experiment projects"
            ) from error
        try:
            return _experiment_projects_from_rows(
                project_rows, selection_rows, well_rows, position_rows, plan_rows
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            subject = "experiment projects" if project_id is None else (
                f"experiment project {project_id}"
            )
            raise ReviewPersistenceError(f"stored {subject} is invalid: {error}") from error

    def prior_selected_well_usage(
        self, current_project_id: str, image_keys: tuple[str, ...]
    ) -> dict[str, tuple[SelectedWellUsage, ...]]:
        if not image_keys:
            return {}
        current = self._connection.execute(
            """SELECT created_at, project_id FROM experiment_project
               WHERE project_id = ?""",
            (current_project_id,),
        ).fetchone()
        if current is None:
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
                                 AND upload.status <> 'failed'
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
                      AND (
                        project.created_at < ?
                        OR (project.created_at = ? AND project.project_id < ?)
                      )
                    ORDER BY project.created_at, project.project_id""",
                (*image_keys, current[0], current[0], current_project_id),
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

    def save_planning_draft(self, draft: PlanningDraft) -> None:
        try:
            with self._connection:
                values = (
                    draft.id, draft.project_id, draft.plan_type, draft.name,
                    draft.library_id, draft.library_rows, draft.protein,
                    draft.volume_nl, draft.assignment_order,
                    draft.created_at.isoformat(), draft.updated_at.isoformat(),
                    draft.experiment_id, draft.workflow_step,
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO planning_draft(
                        plan_id, project_id, plan_type, name, library_id,
                        library_rows, protein, volume_nl, assignment_order,
                        created_at, updated_at, experiment_id, workflow_step
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                self._connection.execute(
                    """UPDATE planning_draft
                       SET name = ?, library_id = ?, library_rows = ?,
                           protein = ?, volume_nl = ?, assignment_order = ?,
                           updated_at = ?, experiment_id = ?,
                           workflow_step = COALESCE(?, workflow_step)
                       WHERE plan_id = ?""",
                    (values[3], values[4], values[5], values[6], values[7],
                     values[8], values[10], values[11], values[12], values[0]),
                )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save planning draft") from error

    def load_planning_drafts(self, project_id: str) -> tuple[PlanningDraft, ...]:
        try:
            rows = self._connection.execute(
                """SELECT plan_id, project_id, plan_type, name, library_id,
                          library_rows, protein, volume_nl, assignment_order,
                          created_at, updated_at, experiment_id, workflow_step
                   FROM planning_draft WHERE project_id = ?
                   ORDER BY created_at, plan_id""",
                (project_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load planning drafts") from error
        return tuple(
            PlanningDraft(
                *row[:9], datetime.fromisoformat(row[9]),
                datetime.fromisoformat(row[10]), row[11], row[12]
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


def _experiment_projects_from_rows(
    project_rows, selection_rows, well_rows, position_rows, plan_rows
) -> tuple[ExperimentProject, ...]:
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


def _project_from_legacy_snapshot(
    plan_id: str,
    name: str,
    plan_type_value: str,
    created_at_value: str,
    updated_at_value: str,
    snapshot_json: str,
) -> ExperimentProject:
    payload = json.loads(snapshot_json)
    if not isinstance(payload, dict):
        raise ValueError("snapshot root must be an object")
    plan_type = PlanType(plan_type_value)
    snapshot_type = payload.get("plan_type")
    if snapshot_type != plan_type.value:
        raise ValueError(
            f"snapshot plan type {snapshot_type!r} does not match {plan_type.value!r}"
        )
    created_at = datetime.fromisoformat(created_at_value)
    updated_at = datetime.fromisoformat(updated_at_value)
    selection = selection_from_snapshot(plan_id, payload, created_at, updated_at)
    return ExperimentProject(
        plan_id,
        name,
        selection,
        ExperimentPlan(
            str(uuid5(NAMESPACE_URL, f"xtalflow:{plan_id}:plan")),
            plan_id,
            plan_type,
            created_at,
            updated_at,
        ),
        created_at,
        updated_at,
    )
