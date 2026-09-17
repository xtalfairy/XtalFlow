"""Deliver finalized plans to instrument folders and audit every attempt."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Union
from uuid import uuid4

from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.plan_lifecycle import PlanRevision, WorksheetExportEvent
from xtalflow.domain.raw_crystal import RawCrystalPlan


SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

WorksheetPlan = Union[FragmentScreenPlan, RawCrystalPlan]


class InstrumentWorksheetExporter(Protocol):
    def export(
        self,
        plan: WorksheetPlan,
        experiment_id: str,
        alternate_root: Path | None = None,
    ): ...


class WorksheetExportAuditPort(Protocol):
    def record_worksheet_export(self, event: WorksheetExportEvent) -> None: ...


class WorksheetExportService:
    """Write a plan's worksheets to the instruments that read them.

    ``deliver`` only touches instrument folders and may run on a worker thread.
    ``record`` writes the audit database and must run on the thread that owns it.
    """

    def __init__(
        self,
        exporter: InstrumentWorksheetExporter,
        audit_store: WorksheetExportAuditPort | None,
        username: str,
    ) -> None:
        self.exporter = exporter
        self.audit_store = audit_store
        self.username = username

    def deliver(
        self,
        plan: WorksheetPlan,
        experiment_id: str,
        alternate_root: Path | None = None,
    ):
        return self.exporter.export(plan, experiment_id, alternate_root)

    def record(
        self,
        revision: PlanRevision,
        status: str,
        *,
        result=None,
        error: str | None = None,
    ) -> None:
        if self.audit_store is None:
            return
        self.audit_store.record_worksheet_export(
            WorksheetExportEvent(
                str(uuid4()), revision.id, self.username, datetime.now(timezone.utc),
                status,
                result.outputs if result is not None else (),
                error,
            )
        )
