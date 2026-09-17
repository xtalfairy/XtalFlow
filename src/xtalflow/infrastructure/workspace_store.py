"""Workspaces, their image sets, review progress, targets, and well calibrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain import (
    CalibrationMethod,
    ImageCalibration,
    Project,
    ProjectImageSet,
    ReviewPreferences,
    ReviewProgress,
    TargetPoint,
)


# Positions chosen before experiments owned their own selection.
WORKSPACE_REVIEW = ""

_NOT_TAKEN_BY_AN_EXPERIMENT = """NOT EXISTS (
    SELECT 1 FROM image_set_target_point AS taken
    WHERE taken.target_id = target.target_id AND taken.experiment_id <> ''
)"""


class SQLiteWorkspaceStore:
    """Review workspaces and the targets chosen on their images."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_project(self, project: Project) -> None:
        try:
            with self._connection:
                project_values = (
                    project.id, project.name, project.active_image_set_id,
                    project.created_at.isoformat(), project.updated_at.isoformat(),
                    project.hidden_at.isoformat() if project.hidden_at else None,
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO project(
                        project_id, name, active_image_set_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    project_values[:5],
                )
                self._connection.execute(
                    """UPDATE project SET name = ?, active_image_set_id = ?,
                              updated_at = ?, hidden_at = ? WHERE project_id = ?""",
                    (project_values[1], project_values[2], project_values[4],
                     project_values[5], project_values[0]),
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
                "SELECT project_id, name, active_image_set_id, created_at, updated_at, "
                "hidden_at FROM project ORDER BY created_at, project_id"
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
                hidden_at=datetime.fromisoformat(row[5]) if row[5] else None,
            )
            for row in project_rows
        )

    def scoped_to(
        self, image_set_id: str, experiment_id: str = WORKSPACE_REVIEW
    ) -> SQLiteImageSetReviewStore:
        return SQLiteImageSetReviewStore(self, image_set_id, experiment_id)

    def target_count_for_image_set(
        self, image_set_id: str, experiment_id: str = WORKSPACE_REVIEW
    ) -> int:
        try:
            return self._connection.execute(
                "SELECT COUNT(*) FROM image_set_target_point "
                "WHERE image_set_id = ? AND experiment_id = ?",
                (image_set_id, experiment_id),
            ).fetchone()[0]
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not count image-set targets") from error

    def delete_targets(
        self, target_ids: tuple[str, ...], experiment_id: str = WORKSPACE_REVIEW
    ) -> None:
        if not target_ids:
            return
        placeholders = ",".join("?" for _ in target_ids)
        try:
            with self._connection:
                self._connection.execute(
                    f"DELETE FROM image_set_target_point "
                    f"WHERE experiment_id = ? AND target_id IN ({placeholders})",
                    (experiment_id, *target_ids),
                )
                if experiment_id == WORKSPACE_REVIEW:
                    # Imported standalone rows must also be removed or migration
                    # would restore them the next time the database is opened.
                    self._connection.execute(
                        f"DELETE FROM target_point WHERE target_id IN ({placeholders})",
                        target_ids,
                    )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not delete selected targets") from error

    def unassigned_workspace_position_count(self, project_id: str) -> int:
        """Positions from the shared workspace review that no experiment has taken."""
        try:
            return self._connection.execute(
                f"""SELECT COUNT(*) FROM image_set_target_point AS target
                    JOIN project_image_set AS image_set
                      ON image_set.image_set_id = target.image_set_id
                    WHERE image_set.project_id = ? AND image_set.archived_at IS NULL
                      AND target.experiment_id = ''
                      AND {_NOT_TAKEN_BY_AN_EXPERIMENT}""",
                (project_id,),
            ).fetchone()[0]
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not count workspace positions") from error

    def adopt_workspace_positions(self, project_id: str, experiment_id: str) -> int:
        """Copy untaken shared-review positions and their review marks into an experiment."""
        if experiment_id == WORKSPACE_REVIEW:
            raise ValueError("choose an experiment to adopt positions into")
        try:
            with self._connection:
                cursor = self._connection.execute(
                    f"""INSERT OR IGNORE INTO image_set_target_point(
                            target_id, image_set_id, image_key, x_px, y_px,
                            selected_at, experiment_id
                        )
                        SELECT target.target_id, target.image_set_id, target.image_key,
                               target.x_px, target.y_px, target.selected_at, ?
                        FROM image_set_target_point AS target
                        JOIN project_image_set AS image_set
                          ON image_set.image_set_id = target.image_set_id
                        WHERE image_set.project_id = ? AND image_set.archived_at IS NULL
                          AND target.experiment_id = ''
                          AND {_NOT_TAKEN_BY_AN_EXPERIMENT}""",
                    (experiment_id, project_id),
                )
                self._connection.execute(
                    """INSERT OR IGNORE INTO image_set_image_review(
                           image_set_id, image_key, reviewed_at, experiment_id
                       )
                       SELECT review.image_set_id, review.image_key,
                              review.reviewed_at, ?
                       FROM image_set_image_review AS review
                       JOIN project_image_set AS image_set
                         ON image_set.image_set_id = review.image_set_id
                       WHERE image_set.project_id = ? AND review.experiment_id = ''""",
                    (experiment_id, project_id),
                )
                return cursor.rowcount
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not adopt workspace positions") from error

    def delete_experiment_positions(self, experiment_id: str) -> None:
        """Remove the positions and review marks that belonged to a deleted experiment."""
        if experiment_id == WORKSPACE_REVIEW:
            raise ValueError("the shared workspace review cannot be deleted")
        try:
            with self._connection:
                for table in (
                    "image_set_target_point", "image_set_image_review",
                    "image_set_review_state",
                ):
                    self._connection.execute(
                        f"DELETE FROM {table} WHERE experiment_id = ?", (experiment_id,)
                    )
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not delete experiment positions") from error

    def experiments_using_images(
        self, image_set_id: str, exclude_experiment_id: str
    ) -> dict[str, tuple[str, ...]]:
        """Other experiments with positions on each image of an image set."""
        try:
            rows = self._connection.execute(
                """SELECT DISTINCT image_key, experiment_id FROM image_set_target_point
                   WHERE image_set_id = ? AND experiment_id NOT IN ('', ?)
                   ORDER BY image_key, experiment_id""",
                (image_set_id, exclude_experiment_id),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load well usage") from error
        usage: dict[str, list[str]] = {}
        for image_key, experiment_id in rows:
            usage.setdefault(image_key, []).append(experiment_id)
        return {key: tuple(value) for key, value in usage.items()}

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

    def save_review_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None:
        try:
            with self._connection:
                self._upsert_review_state(progress, preferences)
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not save review state") from error

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


class SQLiteImageSetReviewStore:
    """Review persistence for one image set within one experiment.

    Positions, review marks, and navigation are the experiment's own; well
    calibrations are shared by every experiment using the image set.
    """

    def __init__(
        self,
        workspace: SQLiteWorkspaceStore,
        image_set_id: str,
        experiment_id: str = WORKSPACE_REVIEW,
    ) -> None:
        self.workspace = workspace
        self.image_set_id = image_set_id
        self.experiment_id = experiment_id

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
                    "WHERE image_set_id = ? AND image_key = ? AND experiment_id = ?",
                    (self.image_set_id, image_key, self.experiment_id),
                )
                self._connection.executemany(
                    "INSERT INTO image_set_target_point(target_id, image_set_id, "
                    "image_key, x_px, y_px, selected_at, experiment_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            target.id,
                            self.image_set_id,
                            target.image_key,
                            target.x_px,
                            target.y_px,
                            target.selected_at.isoformat(),
                            self.experiment_id,
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
                            image_set_id, image_key, reviewed_at, experiment_id
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (self.image_set_id, image_key, reviewed_at, self.experiment_id),
                    )
                    self._connection.execute(
                        """UPDATE image_set_image_review SET reviewed_at = ?
                           WHERE image_set_id = ? AND image_key = ?
                             AND experiment_id = ?""",
                        (reviewed_at, self.image_set_id, image_key, self.experiment_id),
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
                WHERE s.image_set_id = ? AND s.experiment_id = ?
                """,
                (self.image_set_id, self.experiment_id),
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
                f"AND experiment_id = ? "
                f"AND image_key IN ({placeholders}) ORDER BY selected_at, rowid",
                (self.image_set_id, self.experiment_id, *image_keys),
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
                f"WHERE image_set_id = ? AND experiment_id = ? "
                f"AND image_key IN ({placeholders})",
                (self.image_set_id, self.experiment_id, *image_keys),
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
            progress.updated_at.isoformat(), self.experiment_id,
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO image_set_review_state(
                image_set_id, auto_advance_target_count, current_image_key,
                created_at, updated_at, experiment_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        self._connection.execute(
            """UPDATE image_set_review_state
               SET auto_advance_target_count = ?, current_image_key = ?,
                   updated_at = ? WHERE image_set_id = ? AND experiment_id = ?""",
            (values[1], values[2], values[4], values[0], values[5]),
        )
        self._connection.execute(
            "UPDATE project_image_set SET active_image_key = ? WHERE image_set_id = ?",
            (progress.current_image_key, self.image_set_id),
        )
