"""Audit records of worksheet exports and MxLive labwork uploads."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from xtalflow.application import ReviewPersistenceError
from xtalflow.domain.plan_lifecycle import WebDBUploadEvent, WorksheetExportEvent


class SQLiteDeliveryAuditStore:
    """Every attempt to deliver a finalized revision outside XtalFlow."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

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

    def update_webdb_upload(self, event: WebDBUploadEvent) -> None:
        """Record the outcome of an upload attempt written before it was sent."""
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """UPDATE webdb_upload_event
                       SET status = ?, response_json = ?, error_message = ?
                       WHERE upload_id = ?""",
                    (event.status, event.response_json, event.error_message, event.id),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError(f"unknown upload event {event.id}")
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not update WebDB upload") from error

    def list_webdb_uploads_for_experiment(
        self, experiment_id: str
    ) -> tuple[WebDBUploadEvent, ...]:
        try:
            rows = self._connection.execute(
                """SELECT upload.upload_id, upload.revision_id, upload.username,
                          upload.account_id, upload.endpoint, upload.attempted_at,
                          upload.status, upload.record_count, upload.payload_json,
                          upload.response_json, upload.error_message
                   FROM webdb_upload_event AS upload
                   JOIN plan_revision AS revision
                     ON revision.revision_id = upload.revision_id
                   WHERE revision.experiment_id = ?
                   ORDER BY upload.attempted_at""",
                (experiment_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise ReviewPersistenceError("could not list WebDB uploads") from error
        return tuple(
            WebDBUploadEvent(
                row[0], row[1], row[2], row[3], row[4],
                datetime.fromisoformat(row[5]), *row[6:]
            )
            for row in rows
        )

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
