"""Rules that keep MxLive labwork uploads from creating duplicate records.

MxLive has no update or delete API for labworks, so an experiment ID may be
uploaded only once.  Every attempt is recorded before it is sent; an attempt
whose outcome is unknown stays locked until it is checked against MxLive.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from xtalflow.domain.mxlive import (
    MxLiveLabwork,
    MxLivePartialWriteError,
    MxLiveUncertainWriteError,
    MxLiveWriteError,
)
from xtalflow.domain.plan_lifecycle import PlanRevision, WebDBUploadEvent


PENDING = "pending"
UNKNOWN = "unknown"
PARTIAL = "partial"
SUCCEEDED = "succeeded"
FAILED = "failed"


class UploadAvailability(Enum):
    AVAILABLE = "available"
    UPLOADED = "uploaded"
    PARTIAL = "partial"
    NEEDS_VERIFICATION = "needs_verification"


@dataclass(frozen=True)
class UploadLockState:
    availability: UploadAvailability
    event: WebDBUploadEvent | None = None

    @property
    def can_upload(self) -> bool:
        return self.availability is UploadAvailability.AVAILABLE

    @property
    def can_verify(self) -> bool:
        return self.availability in (
            UploadAvailability.PARTIAL, UploadAvailability.NEEDS_VERIFICATION
        )


def upload_lock_state(events: Sequence[WebDBUploadEvent]) -> UploadLockState:
    """Return the lock for one experiment ID from all of its upload attempts."""
    for event in reversed(events):
        if event.status == SUCCEEDED:
            return UploadLockState(UploadAvailability.UPLOADED, event)
        if event.status == PARTIAL:
            return UploadLockState(UploadAvailability.PARTIAL, event)
        if event.status in (PENDING, UNKNOWN):
            return UploadLockState(UploadAvailability.NEEDS_VERIFICATION, event)
    return UploadLockState(UploadAvailability.AVAILABLE)


def verified_upload_event(
    event: WebDBUploadEvent,
    experiment_id: str,
    labworks: Sequence[MxLiveLabwork],
) -> WebDBUploadEvent:
    """Resolve an uncertain attempt from the records MxLive actually holds."""
    stored = sum(1 for item in labworks if item.experiment_id == experiment_id)
    note = f"Verified on MxLive: {stored} of {event.record_count} records found"
    if stored == 0:
        status = FAILED
    elif stored == event.record_count:
        status = SUCCEEDED
    else:
        status = PARTIAL
    message = f"{event.error_message}; {note}" if event.error_message else note
    return replace(event, status=status, error_message=message)


@dataclass(frozen=True)
class UploadOutcome:
    status: str
    response_json: str | None = None
    error_message: str | None = None


def send_labworks(upload: Callable[[], Mapping[str, Any]]) -> UploadOutcome:
    """Run an upload and classify how safe it is to try again.

    It touches no database, so it can run on a worker thread.
    """
    try:
        response = upload()
    except MxLivePartialWriteError as error:
        return UploadOutcome(PARTIAL, error_message=str(error))
    except MxLiveUncertainWriteError as error:
        return UploadOutcome(UNKNOWN, error_message=str(error))
    except (MxLiveWriteError, ValueError) as error:
        return UploadOutcome(FAILED, error_message=str(error))
    return UploadOutcome(SUCCEEDED, response_json=_canonical_json(response))


class UploadAuditPort(Protocol):
    def record_webdb_upload(self, event: WebDBUploadEvent) -> None: ...

    def update_webdb_upload(self, event: WebDBUploadEvent) -> None: ...

    def list_webdb_uploads_for_experiment(
        self, experiment_id: str
    ) -> tuple[WebDBUploadEvent, ...]: ...


class LabworkUploadService:
    def __init__(self, store: UploadAuditPort) -> None:
        self.store = store

    def lock_state(self, experiment_id: str) -> UploadLockState:
        return upload_lock_state(self.store.list_webdb_uploads_for_experiment(experiment_id))

    def begin(
        self,
        revision: PlanRevision,
        username: str,
        account_id: str,
        endpoint: str,
        payload: Sequence[Mapping[str, Any]],
    ) -> WebDBUploadEvent:
        """Record the attempt before sending it.

        A crash or audit failure after this point still leaves a pending attempt
        that locks the experiment ID until it is verified on MxLive.
        """
        pending = WebDBUploadEvent(
            str(uuid4()), revision.id, username, account_id, endpoint,
            datetime.now(timezone.utc), PENDING, len(payload),
            _canonical_json(list(payload)),
        )
        self.store.record_webdb_upload(pending)
        return pending

    def complete(
        self, pending: WebDBUploadEvent, outcome: UploadOutcome
    ) -> WebDBUploadEvent:
        event = replace(
            pending, status=outcome.status, response_json=outcome.response_json,
            error_message=outcome.error_message,
        )
        self.store.update_webdb_upload(event)
        return event

    def verify(
        self,
        event: WebDBUploadEvent,
        experiment_id: str,
        labworks: Sequence[MxLiveLabwork],
    ) -> WebDBUploadEvent:
        verified = verified_upload_event(event, experiment_id, labworks)
        self.store.update_webdb_upload(verified)
        return verified


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
