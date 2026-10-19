"""Deliver finalized plans to instrument folders and audit every attempt."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Union
from uuid import uuid4

from xtalflow.domain.condition_test import ConditionTestPlan
from xtalflow.domain.echo_source import USED, PlateRecordEntry
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.plan_lifecycle import PlanRevision, WorksheetExportEvent
from xtalflow.domain.raw_crystal import RawCrystalPlan
from xtalflow.application.review_port import ReviewPersistenceError


class WorksheetDestinationUnavailable(ReviewPersistenceError):
    """A plan could not be delivered where it was meant to go.

    Defined here with the ports rather than beside one of the folders it
    names: the service decides what each failure means, and both the exporter
    and the shared source plate record speak this vocabulary back to it.
    """


class SourcePlateRecordUnavailable(WorksheetDestinationUnavailable):
    """The shared record cannot be read or written, e.g. the ECHO share is not mounted."""


class SourceRecordBusy(SourcePlateRecordUnavailable):
    """Someone else is writing the record this second.

    The record is right there, so this is the one failure worth waiting out:
    going on without it is exactly the double booking the lock prevents.
    """


class SourceWellsTaken(WorksheetDestinationUnavailable):
    """Wells planned for this experiment were used or skipped by someone else meanwhile."""


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
        instruments: tuple[str, ...] | None = None,
        hold_unreachable: bool = False,
    ): ...


class SourcePlateRecordPort(Protocol):
    def commit(self, new_entries, experiment_id: str, publish): ...


class HeldSourceRecordPort(Protocol):
    def append(self, plate: str, entries) -> None: ...


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
        held_record: HeldSourceRecordPort | None = None,
    ) -> None:
        self.exporter = exporter
        self.audit_store = audit_store
        self.username = username
        self.source_record = source_record
        # Where the entries wait when the shared record is away, the way an
        # unreachable instrument's worksheet waits in a folder of our own.
        self.held_record = held_record

    def deliver(
        self,
        plan: WorksheetPlan,
        experiment_id: str,
        alternate_root: Path | None = None,
        instruments: tuple[str, ...] | None = None,
        hold_unreachable: bool = False,
    ):
        """Save the worksheets; a condition test also records the source wells it takes.

        Saving to the instrument folders records them on the shared source plates,
        after checking nobody took them meanwhile. A copy saved elsewhere is not
        what the ECHO runs, so it records nothing.
        """
        publish = lambda: self.exporter.export(  # noqa: E731
            plan, experiment_id, alternate_root, instruments, hold_unreachable
        )
        if (
            not isinstance(plan, ConditionTestPlan) or alternate_root is not None
            or self.source_record is None
        ):
            return publish()
        now = datetime.now(timezone.utc)
        entries: dict[str, list[PlateRecordEntry]] = {}
        for plate, additive, wells in plan.source_wells_used():
            entries.setdefault(plate, []).append(
                PlateRecordEntry(
                    USED, wells, self.username, now, experiment_id, additive.name,
                    plate_type=additive.source_plate_type,
                )
            )
        if not entries:
            return publish()
        return self._commit(entries, experiment_id, publish)

    def _commit(self, entries, experiment_id: str, publish):
        """Record the wells and publish; publish anyway when the record is away.

        The record lives with the ECHO worksheets, so it goes away with them,
        and refusing to save then only moves the problem: the plan is already
        finalized and the plate is already on the bench. So we go on, on the
        standing assumption that a plate used while the record is away is a
        fresh one, and keep the entries in the record's own format where the
        held worksheets wait, ready to be appended when it comes back.
        """
        published: list = []

        def publish_once():
            published.append(publish())
            return published[-1]

        try:
            return self.source_record.commit(entries, experiment_id, publish_once)
        except SourceRecordBusy:
            raise
        except SourcePlateRecordUnavailable:
            result = published[-1] if published else publish_once()
            if self.held_record is not None:
                for plate, plate_entries in entries.items():
                    try:
                        self.held_record.append(plate, plate_entries)
                    except WorksheetDestinationUnavailable:
                        # Holding them is a courtesy; the worksheets are saved
                        # and failing here would take that away again.
                        pass
            return result

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
