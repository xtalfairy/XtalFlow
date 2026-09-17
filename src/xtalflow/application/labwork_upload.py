"""Rules that keep MxLive labwork uploads from creating duplicate records.

MxLive has no update or delete API for labworks, so an experiment ID may be
uploaded only once.  Every attempt is recorded before it is sent; an attempt
whose outcome is unknown stays locked until it is checked against MxLive.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum

from xtalflow.domain.mxlive import MxLiveLabwork
from xtalflow.domain.plan_lifecycle import WebDBUploadEvent


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
