from __future__ import annotations

import sqlite3


LATEST_SCHEMA_VERSION = 2


def migrate_review_database(connection: sqlite3.Connection) -> None:
    """Upgrade both unversioned legacy databases and new databases in place."""
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS target_point (
                target_id TEXT PRIMARY KEY,
                image_key TEXT NOT NULL,
                x_px REAL NOT NULL,
                y_px REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_plan (
                plan_key TEXT PRIMARY KEY,
                plate_code TEXT NOT NULL,
                batch_id INTEGER NOT NULL,
                profile TEXT NOT NULL,
                auto_advance_target_count INTEGER NOT NULL,
                current_image_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        plan_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_plan)")
        }
        if "auto_advance_target_count" not in plan_columns:
            if "required_target_count" not in plan_columns:
                raise sqlite3.DatabaseError(
                    "review_plan has no recognized target-count column"
                )
            connection.execute(
                "ALTER TABLE review_plan ADD COLUMN auto_advance_target_count INTEGER"
            )
            connection.execute(
                "UPDATE review_plan SET auto_advance_target_count = required_target_count"
            )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS target_point_image_key ON target_point(image_key)"
        )
        connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
