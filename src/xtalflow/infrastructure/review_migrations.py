from __future__ import annotations

import sqlite3


LATEST_SCHEMA_VERSION = 9
IMPORTED_PROJECT_ID = "imported-standalone-reviews"
LEGACY_PLATE_FORMAT_ID = "swissci-midi-3-lens-hr3-194"
LEGACY_PLATE_FORMAT_VERSION = 1


def migrate_review_database(connection: sqlite3.Connection) -> None:
    """Upgrade both unversioned legacy databases and new databases in place."""
    starting_version = connection.execute("PRAGMA user_version").fetchone()[0]
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
        _create_project_schema(connection)
        _import_standalone_reviews(connection)
        connection.execute(
            """
            UPDATE project_image_set
            SET plate_format_id = ?, plate_format_version = ?
            WHERE plate_format_id IS NULL OR plate_format_version IS NULL
            """,
            (LEGACY_PLATE_FORMAT_ID, LEGACY_PLATE_FORMAT_VERSION),
        )
        if starting_version < 5:
            connection.execute(
                """
                INSERT OR IGNORE INTO image_set_image_review(
                    image_set_id, image_key, reviewed_at
                )
                SELECT DISTINCT image_set_id, image_key, '1970-01-01T00:00:00+00:00'
                FROM image_set_target_point
                """
            )
        if starting_version < 7:
            connection.execute(
                """
                UPDATE image_set_image_calibration
                SET physical_diameter_mm = 2.77
                WHERE ABS(physical_diameter_mm - 3.8) < 0.000001
                """
            )
        connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")


def _create_project_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS app_state (state_key TEXT PRIMARY KEY, state_value TEXT)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS project (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            active_image_set_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS project_image_set (
            image_set_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(project_id),
            plate_code TEXT NOT NULL,
            batch_id INTEGER NOT NULL,
            profile TEXT NOT NULL,
            display_order INTEGER NOT NULL,
            active_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archived_at TEXT,
            plate_format_id TEXT NOT NULL DEFAULT 'swissci-midi-3-lens-hr3-194',
            plate_format_version INTEGER NOT NULL DEFAULT 1,
            UNIQUE(project_id, plate_code, batch_id, profile)
        )
        """
    )
    image_set_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(project_image_set)")
    }
    if "plate_format_id" not in image_set_columns:
        connection.execute(
            "ALTER TABLE project_image_set ADD COLUMN plate_format_id TEXT NOT NULL "
            f"DEFAULT '{LEGACY_PLATE_FORMAT_ID}'"
        )
    if "plate_format_version" not in image_set_columns:
        connection.execute(
            "ALTER TABLE project_image_set ADD COLUMN plate_format_version INTEGER "
            f"NOT NULL DEFAULT {LEGACY_PLATE_FORMAT_VERSION}"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_set_review_state (
            image_set_id TEXT PRIMARY KEY REFERENCES project_image_set(image_set_id),
            auto_advance_target_count INTEGER NOT NULL,
            current_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_set_target_point (
            target_id TEXT PRIMARY KEY,
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            image_key TEXT NOT NULL,
            x_px REAL NOT NULL,
            y_px REAL NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS image_set_target_image "
        "ON image_set_target_point(image_set_id, image_key)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_set_image_review (
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            image_key TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY(image_set_id, image_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_set_image_calibration (
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            image_key TEXT NOT NULL,
            center_x_px REAL NOT NULL,
            center_y_px REAL NOT NULL,
            radius_x_px REAL NOT NULL,
            radius_y_px REAL NOT NULL,
            physical_diameter_mm REAL NOT NULL,
            method TEXT NOT NULL,
            confidence REAL NOT NULL,
            confirmed INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(image_set_id, image_key)
        )
        """
    )


def _import_standalone_reviews(connection: sqlite3.Connection) -> None:
    state_rows = connection.execute(
        """
        SELECT plan_key, plate_code, batch_id, profile, auto_advance_target_count,
               current_image_key, created_at, updated_at
        FROM review_plan
        """
    ).fetchall()
    target_rows = connection.execute(
        "SELECT target_id, image_key, x_px, y_px FROM target_point"
    ).fetchall()
    if not state_rows and not target_rows:
        return

    timestamp = "1970-01-01T00:00:00+00:00"
    connection.execute(
        "INSERT OR IGNORE INTO project VALUES (?, ?, NULL, ?, ?)",
        (IMPORTED_PROJECT_ID, "Imported standalone reviews", timestamp, timestamp),
    )
    sources: dict[str, tuple[str, int, str, str, int, str, str]] = {}
    for row in state_rows:
        plan_key, plate_code, batch_id, profile, count, current, created, updated = row
        sources[plan_key] = (
            plate_code,
            batch_id,
            profile,
            current,
            count,
            created,
            updated,
        )
    for _, image_key, _, _ in target_rows:
        parts = image_key.split(":", 4)
        if len(parts) != 5:
            continue
        plate_code, batch_id_text, _, _, profile = parts
        plan_key = f"{plate_code}:{batch_id_text}:{profile}"
        sources.setdefault(
            plan_key,
            (plate_code, int(batch_id_text), profile, image_key, 1, timestamp, timestamp),
        )

    for order, (plan_key, source) in enumerate(sorted(sources.items())):
        plate_code, batch_id, profile, current, count, created, updated = source
        image_set_id = f"legacy:{plan_key}"
        connection.execute(
            """
            INSERT OR IGNORE INTO project_image_set(
                image_set_id, project_id, plate_code, batch_id, profile,
                display_order, active_image_key, created_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                image_set_id,
                IMPORTED_PROJECT_ID,
                plate_code,
                batch_id,
                profile,
                order,
                current,
                created,
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO image_set_review_state VALUES (?, ?, ?, ?, ?)",
            (image_set_id, count, current, created, updated),
        )
        prefix = f"{plate_code}:{batch_id}:"
        for target_id, image_key, x_px, y_px in target_rows:
            if image_key.startswith(prefix) and image_key.endswith(f":{profile}"):
                connection.execute(
                    "INSERT OR IGNORE INTO image_set_target_point VALUES (?, ?, ?, ?, ?)",
                    (target_id, image_set_id, image_key, x_px, y_px),
                )

    first_image_set = connection.execute(
        "SELECT image_set_id FROM project_image_set WHERE project_id = ? "
        "ORDER BY display_order LIMIT 1",
        (IMPORTED_PROJECT_ID,),
    ).fetchone()
    if first_image_set:
        connection.execute(
            "UPDATE project SET active_image_set_id = ? WHERE project_id = ? "
            "AND active_image_set_id IS NULL",
            (first_image_set[0], IMPORTED_PROJECT_ID),
        )
