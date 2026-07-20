from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain import ReviewPreferences, ReviewProgress, TargetPoint
from xtalflow.infrastructure.review_migrations import migrate_review_database


class SQLiteReviewStore:
    """Persist one authoritative target snapshot per logical image."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._closed = False
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
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
