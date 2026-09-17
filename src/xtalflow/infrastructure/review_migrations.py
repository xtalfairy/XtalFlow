from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


LATEST_SCHEMA_VERSION = 20
IMPORTED_PROJECT_ID = "imported-standalone-reviews"
LEGACY_PLATE_FORMAT_ID = "swissci-midi-3-lens-hr3-194"
LEGACY_PLATE_FORMAT_VERSION = 1


class ReviewDatabaseTooNewError(sqlite3.DatabaseError):
    """The database was upgraded by a newer XtalFlow than the one opening it."""

    def __init__(self, database_version: int) -> None:
        self.database_version = database_version
        super().__init__(
            f"review database schema {database_version} is newer than this "
            f"XtalFlow supports ({LATEST_SCHEMA_VERSION}); use a newer XtalFlow"
        )


def schema_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def needs_upgrade(connection: sqlite3.Connection) -> bool:
    """True when an existing database will be changed by the next migration."""
    version = schema_version(connection)
    if version > LATEST_SCHEMA_VERSION:
        raise ReviewDatabaseTooNewError(version)
    has_tables = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table')"
    ).fetchone()[0]
    return bool(has_tables) and version < LATEST_SCHEMA_VERSION


def migrate_review_database(connection: sqlite3.Connection) -> None:
    """Upgrade both unversioned legacy databases and new databases in place.

    Every step runs in one transaction. Python's sqlite3 module otherwise
    commits schema changes made before the first data change, so an
    interrupted upgrade could leave a partly migrated database behind.
    """
    starting_version = schema_version(connection)
    if starting_version > LATEST_SCHEMA_VERSION:
        # Writing our version would silently downgrade the marker.
        raise ReviewDatabaseTooNewError(starting_version)
    connection.execute("BEGIN IMMEDIATE")
    try:
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
        _create_experiment_project_schema(connection)
        if starting_version < 10:
            _assign_legacy_selection_times(connection, "target_point")
        _create_legacy_target_import_schema(connection, starting_version)
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
        if starting_version < 18:
            _copy_experiment_positions(connection)
        if starting_version < 7:
            connection.execute(
                """
                UPDATE image_set_image_calibration
                SET physical_diameter_mm = 2.77
                WHERE ABS(physical_diameter_mm - 3.8) < 0.000001
                """
            )
        connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


def _create_experiment_project_schema(connection: sqlite3.Connection) -> None:
    """Add the new project = crystal selection + plan aggregate."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_project (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS crystal_selection (
            selection_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE
                REFERENCES experiment_project(project_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS selected_well (
            selected_well_id TEXT PRIMARY KEY,
            selection_id TEXT NOT NULL
                REFERENCES crystal_selection(selection_id) ON DELETE CASCADE,
            image_set_id TEXT,
            image_key TEXT NOT NULL,
            image_path TEXT NOT NULL,
            plate_code TEXT NOT NULL,
            well_address TEXT NOT NULL,
            batch_id INTEGER,
            profile TEXT NOT NULL,
            plate_format_id TEXT NOT NULL,
            plate_format_version INTEGER NOT NULL,
            selection_order INTEGER NOT NULL CHECK(selection_order > 0),
            selected_at TEXT NOT NULL,
            UNIQUE(selection_id, image_key),
            UNIQUE(selection_id, selection_order)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS selected_well_image_key "
        "ON selected_well(image_key)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS soaking_position (
            position_id TEXT PRIMARY KEY,
            selected_well_id TEXT NOT NULL
                REFERENCES selected_well(selected_well_id) ON DELETE CASCADE,
            source_target_id TEXT NOT NULL,
            position_order INTEGER NOT NULL CHECK(position_order > 0),
            x_mm TEXT NOT NULL,
            y_mm TEXT NOT NULL,
            selected_at TEXT NOT NULL,
            UNIQUE(selected_well_id, position_order)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_plan (
            plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE
                REFERENCES experiment_project(project_id) ON DELETE CASCADE,
            plan_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


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
            updated_at TEXT NOT NULL,
            experiment_id TEXT
        )
        """
    )
    draft_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(planning_draft)")
    }
    if "experiment_id" not in draft_columns:
        connection.execute(
            "ALTER TABLE planning_draft ADD COLUMN experiment_id TEXT"
        )
    if "workflow_step" not in draft_columns:
        # Schema 18: the guided step to resume an experiment at.
        connection.execute("ALTER TABLE planning_draft ADD COLUMN workflow_step TEXT")
    if "details_json" not in draft_columns:
        # Schema 20: plan inputs that do not fit the fixed columns, such as a
        # condition test's additives and tables.
        connection.execute("ALTER TABLE planning_draft ADD COLUMN details_json TEXT")
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
        UPDATE planning_draft
        SET experiment_id = (
            SELECT revision.experiment_id
            FROM plan_revision AS revision
            WHERE revision.plan_id = planning_draft.plan_id
            ORDER BY revision.revision_number DESC
            LIMIT 1
        )
        WHERE experiment_id IS NULL
          AND EXISTS (
              SELECT 1 FROM plan_revision AS revision
              WHERE revision.plan_id = planning_draft.plan_id
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
            error_message TEXT
        )
        """
    )
    _move_worksheet_paths_to_instrument_outputs(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS worksheet_export_output (
            export_id TEXT NOT NULL
                REFERENCES worksheet_export_event(export_id) ON DELETE CASCADE,
            output_order INTEGER NOT NULL CHECK(output_order > 0),
            instrument TEXT NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY(export_id, output_order)
        )
        """
    )
    _key_worksheet_outputs_by_order(connection)
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
    project_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(project)")
    }
    if "hidden_at" not in project_columns:
        # Schema 19: workspaces can be hidden from the home list without deletion.
        connection.execute("ALTER TABLE project ADD COLUMN hidden_at TEXT")
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
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            auto_advance_target_count INTEGER NOT NULL,
            current_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            experiment_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(experiment_id, image_set_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_set_target_point (
            target_id TEXT NOT NULL,
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            image_key TEXT NOT NULL,
            x_px REAL NOT NULL,
            y_px REAL NOT NULL,
            selected_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
            experiment_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(experiment_id, target_id)
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
            experiment_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(experiment_id, image_set_id, image_key)
        )
        """
    )
    _scope_review_tables_by_experiment(connection)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS image_set_target_image "
        "ON image_set_target_point(image_set_id, image_key)"
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


def _create_legacy_target_import_schema(
    connection: sqlite3.Connection, starting_version: int
) -> None:
    """Remember imported standalone targets so later deletions stay deleted."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS legacy_target_import (
            target_id TEXT PRIMARY KEY
        )
        """
    )
    if 0 < starting_version < 16:
        # Earlier versions imported every standalone target on each open. Rows
        # missing from image_set_target_point were deleted by the user.
        connection.execute(
            "INSERT OR IGNORE INTO legacy_target_import(target_id) "
            "SELECT target_id FROM target_point"
        )


LEGACY_WORKSHEET_PATH_COLUMNS = (
    ("echo_path", "echo650"),
    ("shifter1_path", "shifter1"),
    ("shifter2_path", "shifter2"),
)


def _key_worksheet_outputs_by_order(connection: sqlite3.Connection) -> None:
    """Schema 20: one instrument can receive several files from one export.

    A condition test sends one ECHO file per dispense time, so outputs are keyed
    by their order instead of by instrument.
    """
    key = {
        row[1] for row in connection.execute("PRAGMA table_info(worksheet_export_output)")
        if row[5]
    }
    if "instrument" not in key:
        return
    connection.execute(
        "ALTER TABLE worksheet_export_output RENAME TO worksheet_export_output_schema19"
    )
    connection.execute(
        """
        CREATE TABLE worksheet_export_output (
            export_id TEXT NOT NULL
                REFERENCES worksheet_export_event(export_id) ON DELETE CASCADE,
            output_order INTEGER NOT NULL CHECK(output_order > 0),
            instrument TEXT NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY(export_id, output_order)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO worksheet_export_output(export_id, output_order, instrument, path)
        SELECT export_id, output_order, instrument, path
        FROM worksheet_export_output_schema19
        """
    )
    connection.execute("DROP TABLE worksheet_export_output_schema19")


def _move_worksheet_paths_to_instrument_outputs(connection: sqlite3.Connection) -> None:
    """Replace one path column per instrument with one output row per file.

    Schema 16 and earlier stored ECHO, SHIFTER 1, and SHIFTER 2 paths as fixed
    columns, so recording another instrument required a schema change.
    """
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(worksheet_export_event)")
    }
    if "echo_path" not in columns:
        return
    # Renaming before the output table exists keeps its foreign key pointing
    # at the rebuilt event table.
    connection.execute(
        "ALTER TABLE worksheet_export_event RENAME TO worksheet_export_event_schema16"
    )
    connection.execute(
        """
        CREATE TABLE worksheet_export_event (
            export_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL REFERENCES plan_revision(revision_id),
            username TEXT NOT NULL,
            exported_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO worksheet_export_event(
            export_id, revision_id, username, exported_at, status, error_message
        )
        SELECT export_id, revision_id, username, exported_at, status, error_message
        FROM worksheet_export_event_schema16
        """
    )
    connection.execute(
        """
        CREATE TABLE worksheet_export_output (
            export_id TEXT NOT NULL
                REFERENCES worksheet_export_event(export_id) ON DELETE CASCADE,
            output_order INTEGER NOT NULL CHECK(output_order > 0),
            instrument TEXT NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY(export_id, output_order)
        )
        """
    )
    for order, (column, instrument) in enumerate(LEGACY_WORKSHEET_PATH_COLUMNS, start=1):
        connection.execute(
            f"""
            INSERT INTO worksheet_export_output(export_id, output_order, instrument, path)
            SELECT export_id, ?, ?, {column}
            FROM worksheet_export_event_schema16
            WHERE {column} IS NOT NULL AND {column} <> ''
            """,
            (order, instrument),
        )
    connection.execute("DROP TABLE worksheet_export_event_schema16")


REVIEW_TABLE_LAYOUTS = {
    "image_set_target_point": (
        """
        CREATE TABLE image_set_target_point (
            target_id TEXT NOT NULL,
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            image_key TEXT NOT NULL,
            x_px REAL NOT NULL,
            y_px REAL NOT NULL,
            selected_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
            experiment_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(experiment_id, target_id)
        )
        """,
        "target_id, image_set_id, image_key, x_px, y_px, selected_at",
    ),
    "image_set_image_review": (
        """
        CREATE TABLE image_set_image_review (
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            image_key TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            experiment_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(experiment_id, image_set_id, image_key)
        )
        """,
        "image_set_id, image_key, reviewed_at",
    ),
    "image_set_review_state": (
        """
        CREATE TABLE image_set_review_state (
            image_set_id TEXT NOT NULL REFERENCES project_image_set(image_set_id),
            auto_advance_target_count INTEGER NOT NULL,
            current_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            experiment_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(experiment_id, image_set_id)
        )
        """,
        "image_set_id, auto_advance_target_count, current_image_key, created_at, updated_at",
    ),
}


def _scope_review_tables_by_experiment(connection: sqlite3.Connection) -> None:
    """Schema 18: positions, review marks, and navigation belong to an experiment.

    Rows from earlier schemas keep the empty experiment ID, which is the shared
    workspace review. Well calibrations stay shared because they describe the
    physical plate.
    """
    for table, (create_sql, columns) in REVIEW_TABLE_LAYOUTS.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "experiment_id" in existing:
            continue
        connection.execute(f"ALTER TABLE {table} RENAME TO {table}_schema17")
        connection.execute(create_sql)
        connection.execute(
            f"INSERT INTO {table}({columns}) SELECT {columns} FROM {table}_schema17"
        )
        connection.execute(f"DROP TABLE {table}_schema17")


def _copy_experiment_positions(connection: sqlite3.Connection) -> None:
    """Give each existing experiment its own copy of the positions it fixed."""
    connection.execute(
        """
        INSERT OR IGNORE INTO image_set_target_point(
            target_id, image_set_id, image_key, x_px, y_px, selected_at, experiment_id
        )
        SELECT target.target_id, target.image_set_id, target.image_key,
               target.x_px, target.y_px, target.selected_at, selection.project_id
        FROM soaking_position AS position
        JOIN selected_well AS well ON well.selected_well_id = position.selected_well_id
        JOIN crystal_selection AS selection ON selection.selection_id = well.selection_id
        JOIN image_set_target_point AS target
          ON target.target_id = position.source_target_id AND target.experiment_id = ''
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO image_set_image_review(
            image_set_id, image_key, reviewed_at, experiment_id
        )
        SELECT DISTINCT review.image_set_id, review.image_key, review.reviewed_at,
               target.experiment_id
        FROM image_set_target_point AS target
        JOIN image_set_image_review AS review
          ON review.image_set_id = target.image_set_id
         AND review.image_key = target.image_key AND review.experiment_id = ''
        WHERE target.experiment_id <> ''
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
        "FROM target_point WHERE target_id NOT IN "
        "(SELECT target_id FROM legacy_target_import) "
        "ORDER BY selected_at, rowid"
    ).fetchall()
    if not state_rows and not target_rows:
        return

    timestamp = "1970-01-01T00:00:00+00:00"
    connection.execute(
        "INSERT OR IGNORE INTO project(project_id, name, active_image_set_id, "
        "created_at, updated_at) VALUES (?, ?, NULL, ?, ?)",
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
            "INSERT OR IGNORE INTO image_set_review_state(image_set_id, "
            "auto_advance_target_count, current_image_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (image_set_id, count, current, created, updated),
        )
        prefix = f"{plate_code}:{batch_id}:"
        for target_id, image_key, x_px, y_px, selected_at in target_rows:
            if image_key.startswith(prefix) and image_key.endswith(f":{profile}"):
                connection.execute(
                    "INSERT OR IGNORE INTO image_set_target_point(target_id, "
                    "image_set_id, image_key, x_px, y_px, selected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (target_id, image_set_id, image_key, x_px, y_px, selected_at),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO legacy_target_import(target_id) VALUES (?)",
                    (target_id,),
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
