from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

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
                f"SELECT target_id, image_key, x_px, y_px FROM target_point "
                f"WHERE image_key IN ({placeholders}) ORDER BY rowid",
                image_keys,
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load image targets") from error
        return tuple(TargetPoint(*row) for row in rows)

    def load_reviewed_images(self, image_keys: tuple[str, ...]) -> tuple[str, ...]:
        return ()

    def _replace_image_targets(
        self, image_key: str, targets: tuple[TargetPoint, ...]
    ) -> None:
        self._connection.execute(
            "DELETE FROM target_point WHERE image_key = ?", (image_key,)
        )
        self._connection.executemany(
            "INSERT INTO target_point(target_id, image_key, x_px, y_px) VALUES (?, ?, ?, ?)",
            ((target.id, target.image_key, target.x_px, target.y_px) for target in targets),
        )

    def _upsert_review_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO review_plan(
                plan_key, plate_code, batch_id, profile, auto_advance_target_count,
                current_image_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_key) DO UPDATE SET
                auto_advance_target_count = excluded.auto_advance_target_count,
                current_image_key = excluded.current_image_key,
                updated_at = excluded.updated_at
            """,
            (
                progress.plan_key,
                progress.plate_code,
                progress.batch_id,
                progress.profile,
                preferences.auto_advance_target_count,
                progress.current_image_key,
                progress.created_at.isoformat(),
                progress.updated_at.isoformat(),
            ),
        )

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def save_project(self, project: Project) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO project(project_id, name, active_image_set_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        name = excluded.name,
                        active_image_set_id = excluded.active_image_set_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        project.id,
                        project.name,
                        project.active_image_set_id,
                        project.created_at.isoformat(),
                        project.updated_at.isoformat(),
                    ),
                )
                for image_set in project.image_sets:
                    self._connection.execute(
                        """
                        INSERT INTO project_image_set(
                            image_set_id, project_id, plate_code, batch_id, profile,
                            display_order, active_image_key, created_at, archived_at,
                            plate_format_id, plate_format_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(image_set_id) DO UPDATE SET
                            display_order = excluded.display_order,
                            active_image_key = excluded.active_image_key,
                            archived_at = excluded.archived_at,
                            plate_format_id = excluded.plate_format_id,
                            plate_format_version = excluded.plate_format_version
                        """,
                        (
                            image_set.id,
                            image_set.project_id,
                            image_set.plate_code,
                            image_set.batch_id,
                            image_set.profile,
                            image_set.display_order,
                            image_set.active_image_key,
                            image_set.created_at.isoformat(),
                            image_set.archived_at.isoformat()
                            if image_set.archived_at
                            else None,
                            image_set.plate_format_id,
                            image_set.plate_format_version,
                        ),
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

    def save_last_open_project(self, project_id: str) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO app_state(state_key, state_value) VALUES ('last_project_id', ?)
                    ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
                    """,
                    (project_id,),
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
                    "INSERT INTO image_set_target_point VALUES (?, ?, ?, ?, ?)",
                    (
                        (target.id, self.image_set_id, target.image_key, target.x_px, target.y_px)
                        for target in targets
                    ),
                )
                self._upsert_state(progress, preferences)
                if mark_reviewed:
                    self._connection.execute(
                        """
                        INSERT INTO image_set_image_review(image_set_id, image_key, reviewed_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(image_set_id, image_key) DO UPDATE SET
                            reviewed_at = excluded.reviewed_at
                        """,
                        (self.image_set_id, image_key, datetime.now(UTC).isoformat()),
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
                f"SELECT target_id, image_key, x_px, y_px "
                f"FROM image_set_target_point WHERE image_set_id = ? "
                f"AND image_key IN ({placeholders}) ORDER BY rowid",
                (self.image_set_id, *image_keys),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not load image-set targets") from error
        return tuple(TargetPoint(*row) for row in rows)

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
                self._connection.execute(
                    """
                    INSERT INTO image_set_image_calibration(
                        image_set_id, image_key, center_x_px, center_y_px,
                        radius_x_px, radius_y_px, physical_diameter_mm,
                        method, confidence, confirmed, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(image_set_id, image_key) DO UPDATE SET
                        center_x_px = excluded.center_x_px,
                        center_y_px = excluded.center_y_px,
                        radius_x_px = excluded.radius_x_px,
                        radius_y_px = excluded.radius_y_px,
                        physical_diameter_mm = excluded.physical_diameter_mm,
                        method = excluded.method,
                        confidence = excluded.confidence,
                        confirmed = excluded.confirmed,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.image_set_id,
                        calibration.image_key,
                        calibration.center_x_px,
                        calibration.center_y_px,
                        calibration.radius_x_px,
                        calibration.radius_y_px,
                        calibration.physical_diameter_mm,
                        calibration.method.value,
                        calibration.confidence,
                        int(calibration.confirmed),
                        calibration.updated_at.isoformat(),
                    ),
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
        self._connection.execute(
            """
            INSERT INTO image_set_review_state(
                image_set_id, auto_advance_target_count, current_image_key,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(image_set_id) DO UPDATE SET
                auto_advance_target_count = excluded.auto_advance_target_count,
                current_image_key = excluded.current_image_key,
                updated_at = excluded.updated_at
            """,
            (
                self.image_set_id,
                preferences.auto_advance_target_count,
                progress.current_image_key,
                progress.created_at.isoformat(),
                progress.updated_at.isoformat(),
            ),
        )
        self._connection.execute(
            "UPDATE project_image_set SET active_image_key = ? WHERE image_set_id = ?",
            (progress.current_image_key, self.image_set_id),
        )
