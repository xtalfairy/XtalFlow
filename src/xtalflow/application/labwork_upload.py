"""Rules that keep MxLive labwork uploads from destroying records.

MxLive's write endpoint is an upsert, not a create: a call that matches no
record makes one, and a call that matches a record overwrites every column it
sends and nulls every column it does not.  There is no delete.  So uploading
an experiment ID a second time does not fail and does not duplicate - it
erases whatever has been written on those records since, the harvest time and
the puck among it, and nothing can put them back.

Hence an experiment ID is uploaded only once.  Every attempt is recorded
before it is sent; an attempt whose outcome is unknown stays locked until it
is checked against MxLive.  And the lock here is not the last word: it knows
only the uploads made from this computer and this database, so the upload path
asks MxLive itself whether it already holds the ID before anything is sent.
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


def payloads_by_well(
    payload_json: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    """The records of one upload attempt, keyed by plate and well.

    What was sent, not what the plan says now: a plan edited or re-finalized
    since would name wells this attempt never carried. Anything that is not a
    record with both halves of an address is left out rather than guessed at.
    """
    try:
        payloads = json.loads(payload_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payloads, list):
        return {}
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        key = (
            str(payload.get("plate_code") or ""),
            str(payload.get("plate_well") or ""),
        )
        if all(key):
            found[key] = payload
    return found


def addresses_sent_twice(
    payloads: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """Addresses this payload carries more than once, which must not be sent.

    An upload sends one record per call, and MxLive's write endpoint looks a
    record up by plate and well before deciding whether to create it or update
    it. Two records of one address in one payload would therefore create on
    the first call and update on the second - and that update is filtered
    without the plate, so in an experiment where a well address is on two
    plates it overwrites, column for column, the sibling created seconds
    earlier.

    Nothing downstream could find it afterwards. ``payloads_by_well`` keeps
    the last record of an address, so the audit would read as though what was
    sent is what MxLive holds, and the harvest read compares against that
    audit.

    No payload like that has been seen: wells are unique upstream by their
    id, not by the text of their address. Which is why this is a refusal and
    not a repair - it costs nothing when it is wrong, and what it prevents
    cannot be undone.
    """
    counted_by_address: dict[tuple[str, str], int] = {}
    for payload in payloads:
        key = (
            str(payload.get("plate_code") or ""),
            str(payload.get("plate_well") or ""),
        )
        counted_by_address[key] = counted_by_address.get(key, 0) + 1
    return tuple(
        f"{plate} {well}"
        for (plate, well), seen in counted_by_address.items() if seen > 1
    )


def records_not_stored(
    event: WebDBUploadEvent,
    experiment_id: str,
    labworks: Sequence[MxLiveLabwork],
) -> tuple[Mapping[str, Any], ...]:
    """The records of this attempt that MxLive does not hold, in sent order.

    By address rather than by count, because an upload sends one record per
    call and the one that was interrupted is a particular well, not a number.
    """
    held = {
        (item.plate_code or "", item.plate_well or "")
        for item in labworks
        if item.experiment_id == experiment_id
    }
    return tuple(
        payload
        for key, payload in payloads_by_well(event.payload_json).items()
        if key not in held
    )


def verified_upload_event(
    event: WebDBUploadEvent,
    experiment_id: str,
    labworks: Sequence[MxLiveLabwork],
) -> WebDBUploadEvent:
    """Resolve an uncertain attempt from the records MxLive actually holds.

    By address, not by count. Counting let a wrong set pass as a right one:
    one record missing and one record there that this attempt never sent came
    to the same number, and the attempt was then written down as succeeded -
    after which its payload is believed to be what MxLive holds, which is the
    only proof of ownership a harvest write has.

    A record under this ID that the attempt did not send does not make the
    attempt incomplete; it is said, and left to the harvest read, which
    refuses the experiment over it. What makes it incomplete is one of ours
    missing.
    """
    sent = payloads_by_well(event.payload_json)
    held = {
        (item.plate_code or "", item.plate_well or "")
        for item in labworks
        if item.experiment_id == experiment_id
    }
    if not sent:
        # An attempt whose payload cannot be read any more. Nothing to compare
        # addresses against, so this falls back to counting - which is what
        # this did for every attempt, and is kept here unchanged rather than
        # made stricter, because a corrupted audit row is not the thing being
        # fixed and an experiment must not become unverifiable over it.
        stored = sum(1 for item in labworks if item.experiment_id == experiment_id)
        note = f"Verified on MxLive: {stored} of {event.record_count} records found"
        if stored == 0:
            status = FAILED
        elif stored >= event.record_count:
            status = SUCCEEDED
        else:
            status = PARTIAL
        message = f"{event.error_message}; {note}" if event.error_message else note
        return replace(event, status=status, error_message=message)
    missing = [key for key in sent if key not in held]
    strays = sorted(held - set(sent))
    note = (
        f"Verified on MxLive: {len(sent) - len(missing)} of {len(sent)} "
        f"records found"
    )
    if len(missing) == len(sent):
        status = FAILED
    elif missing:
        status = PARTIAL
        note += "; missing " + ", ".join(f"{plate} {well}" for plate, well in missing)
    else:
        status = SUCCEEDED
    if strays:
        note += "; MxLive also holds " + ", ".join(
            f"{plate} {well}" for plate, well in strays
        ) + ", which this upload did not send"
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

    def list_webdb_uploads_by_experiment(
        self,
    ) -> Mapping[str, tuple[WebDBUploadEvent, ...]]: ...


class LabworkUploadService:
    def __init__(self, store: UploadAuditPort) -> None:
        self.store = store

    def lock_state(self, experiment_id: str) -> UploadLockState:
        return upload_lock_state(self.store.list_webdb_uploads_for_experiment(experiment_id))

    def lock_states(self) -> dict[str, UploadLockState]:
        """The lock of every experiment ID that was ever uploaded, in one read."""
        return {
            experiment_id: upload_lock_state(events)
            for experiment_id, events in self.store.list_webdb_uploads_by_experiment().items()
        }

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
