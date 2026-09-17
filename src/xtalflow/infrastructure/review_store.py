"""Open the per-user review database and assemble its repositories."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.infrastructure.delivery_audit_store import SQLiteDeliveryAuditStore
from xtalflow.infrastructure.fragment_library_store import SQLiteFragmentLibraryStore
from xtalflow.infrastructure.planning_store import SQLitePlanningStore
from xtalflow.infrastructure.review_migrations import (
    ReviewDatabaseTooNewError,
    migrate_review_database,
    needs_upgrade,
    schema_version,
)
from xtalflow.infrastructure.workspace_store import SQLiteWorkspaceStore


class SQLiteReviewStore:
    """One SQLite database shared by focused repositories.

    Callers depend on the repository they need: ``workspace`` for image review,
    ``planning`` for experiment plans, ``audit`` for deliveries outside
    XtalFlow, and ``fragment_libraries`` for imported library catalogs.
    """

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._closed = False
        self.upgrade_backup_path: Path | None = None
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
            self._connection.execute("PRAGMA foreign_keys = ON")
            if needs_upgrade(self._connection):
                self.upgrade_backup_path = self._backup_before_upgrade()
            migrate_review_database(self._connection)
            self.workspace = SQLiteWorkspaceStore(self._connection)
            self.planning = SQLitePlanningStore(self._connection)
            self.audit = SQLiteDeliveryAuditStore(self._connection)
            self.fragment_libraries = SQLiteFragmentLibraryStore(self._connection)
            self.planning_project_migration = (
                self.planning.migrate_finalized_planning_projects()
            )
        except ReviewDatabaseTooNewError as error:
            self._connection.close()
            raise ReviewPersistenceError(
                f"cannot open review database {self.database_path}: {error}"
            ) from error
        except (OSError, sqlite3.Error) as error:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise ReviewPersistenceError(
                f"cannot open review database: {self.database_path}"
            ) from error

    def _backup_before_upgrade(self) -> Path:
        """Copy the database before changing its schema; no backup, no upgrade."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        version = schema_version(self._connection)
        backup_path = self.database_path.with_name(
            f"{self.database_path.name}.schema{version}-{stamp}.bak"
        )
        backup = sqlite3.connect(backup_path)
        try:
            self._connection.backup(backup)
        finally:
            backup.close()
        return backup_path

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True
