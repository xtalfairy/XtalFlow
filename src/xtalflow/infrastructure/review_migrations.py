from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


LATEST_SCHEMA_VERSION = 13
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
                y_px REAL NOT NULL,
                selected_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
            )
            """
        )
        target_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(target_point)")
        }
        if "selected_at" not in target_columns:
            connection.execute(
                "ALTER TABLE target_point ADD COLUMN selected_at TEXT NOT NULL "
                "DEFAULT '1970-01-01T00:00:00+00:00'"
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
        _create_fragment_library_schema(connection)
        _create_planning_schema(connection)
        if starting_version < 10:
            _assign_legacy_selection_times(connection, "target_point")
        _import_standalone_reviews(connection)
        if starting_version < 10:
            _assign_legacy_selection_times(connection, "image_set_target_point")
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


def _create_planning_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS planning_draft (
            plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
            plan_type TEXT NOT NULL,
            name TEXT NOT NULL,
            library_id TEXT,
            library_rows TEXT NOT NULL,
            protein TEXT NOT NULL,
            volume_nl TEXT NOT NULL,
            assignment_order TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_revision (
            revision_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES planning_draft(plan_id) ON DELETE CASCADE,
            revision_number INTEGER NOT NULL CHECK(revision_number > 0),
            experiment_id TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            finalized_by TEXT NOT NULL,
            finalized_at TEXT NOT NULL,
            UNIQUE(plan_id, revision_number)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS worksheet_export_event (
            export_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL REFERENCES plan_revision(revision_id),
            username TEXT NOT NULL,
            exported_at TEXT NOT NULL,
            status TEXT NOT NULL,
            echo_path TEXT,
            shifter1_path TEXT,
            shifter2_path TEXT,
            error_message TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS webdb_upload_event (
            upload_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL REFERENCES plan_revision(revision_id),
            username TEXT NOT NULL,
            account_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            status TEXT NOT NULL,
            record_count INTEGER NOT NULL CHECK(record_count > 0),
            payload_json TEXT NOT NULL,
            response_json TEXT,
            error_message TEXT
        )
        """
    )


def _create_fragment_library_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fragment_library_import (
            library_import_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            imported_at TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK(row_count > 0)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fragment_library_entry (
            library_import_id TEXT NOT NULL
                REFERENCES fragment_library_import(library_import_id) ON DELETE CASCADE,
            row_number INTEGER NOT NULL CHECK(row_number > 0),
            vendor TEXT NOT NULL,
            library_name TEXT NOT NULL,
            compound_number TEXT NOT NULL,
            compound_id TEXT NOT NULL,
            formula TEXT NOT NULL,
            molecular_weight TEXT NOT NULL,
            smiles TEXT NOT NULL,
            concentration_mm TEXT NOT NULL,
            solvent TEXT NOT NULL,
            source_plate TEXT NOT NULL,
            source_well TEXT NOT NULL,
            PRIMARY KEY(library_import_id, row_number)
        )
        """
    )


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
            y_px REAL NOT NULL,
            selected_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
        )
        """
    )
    target_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(image_set_target_point)")
    }
    if "selected_at" not in target_columns:
        connection.execute(
            "ALTER TABLE image_set_target_point ADD COLUMN selected_at TEXT NOT NULL "
            "DEFAULT '1970-01-01T00:00:00+00:00'"
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
        "SELECT target_id, image_key, x_px, y_px, selected_at "
        "FROM target_point ORDER BY selected_at, rowid"
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
    for _, image_key, _, _, _ in target_rows:
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
        for target_id, image_key, x_px, y_px, selected_at in target_rows:
            if image_key.startswith(prefix) and image_key.endswith(f":{profile}"):
                connection.execute(
                    "INSERT OR IGNORE INTO image_set_target_point "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (target_id, image_set_id, image_key, x_px, y_px, selected_at),
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


def _assign_legacy_selection_times(
    connection: sqlite3.Connection, table_name: str
) -> None:
    epoch = "1970-01-01T00:00:00+00:00"
    rows = connection.execute(
        f"SELECT rowid FROM {table_name} WHERE selected_at = ? ORDER BY rowid",
        (epoch,),
    ).fetchall()
    base = datetime(1970, 1, 1, tzinfo=timezone.utc)
    connection.executemany(
        f"UPDATE {table_name} SET selected_at = ? WHERE rowid = ?",
        (
            ((base + timedelta(microseconds=index)).isoformat(), row[0])
            for index, row in enumerate(rows, start=1)
        ),
    )
