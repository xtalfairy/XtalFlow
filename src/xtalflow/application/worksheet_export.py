"""Deliver finalized plans to instrument folders and audit every attempt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Union
from uuid import uuid4

from xtalflow.domain.condition_test import ConditionTestPlan
from xtalflow.domain.echo_source import USED, PlateRecordEntry
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.plan_lifecycle import PlanRevision, WorksheetExportEvent
from xtalflow.domain.raw_crystal import RawCrystalPlan


SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

WorksheetPlan = Union[FragmentScreenPlan, RawCrystalPlan, ConditionTestPlan]


class InstrumentWorksheetExporter(Protocol):
    def export(
        self,
        plan: WorksheetPlan,
        experiment_id: str,
        alternate_root: Path | None = None,
    ): ...


class SourcePlateRecordPort(Protocol):
    def commit(self, new_entries, experiment_id: str, publish): ...


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
        source_record: SourcePlateRecordPort | None = None,
    ) -> None:
        self.exporter = exporter
        self.audit_store = audit_store
        self.username = username
        self.source_record = source_record

    def deliver(
        self,
        plan: WorksheetPlan,
        experiment_id: str,
        alternate_root: Path | None = None,
        skipped: Mapping[str, Sequence[PlateRecordEntry]] | None = None,
    ):
        """Save the worksheets; a condition test also records the source wells it takes.

        Saving to the instrument folders records them on the shared source plates,
        after checking nobody took them meanwhile. A copy saved elsewhere is not
        what the ECHO runs, so it records nothing.
        """
        publish = lambda: self.exporter.export(plan, experiment_id, alternate_root)  # noqa: E731
        if (
            not isinstance(plan, ConditionTestPlan) or alternate_root is not None
            or self.source_record is None
        ):
            return publish()
        now = datetime.now(timezone.utc)
        entries: dict[str, list[PlateRecordEntry]] = {
            plate: list(items) for plate, items in (skipped or {}).items()
        }
        for plate, additive, wells in plan.source_wells_used():
            entries.setdefault(plate, []).append(
                PlateRecordEntry(
                    USED, wells, self.username, now, experiment_id, additive.name,
                    plate_type=additive.source_plate_type,
                )
            )
        if not entries:
            return publish()
        return self.source_record.commit(entries, experiment_id, publish)

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
