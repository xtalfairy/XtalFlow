from dataclasses import replace
from datetime import datetime, timezone

from xtalflow.application.labwork_upload import (
    UploadAvailability,
    upload_lock_state,
    verified_upload_event,
)
from xtalflow.domain.mxlive import MxLiveLabwork
from xtalflow.domain.plan_lifecycle import WebDBUploadEvent


def _event(status: str, record_count: int = 2) -> WebDBUploadEvent:
    return WebDBUploadEvent(
        f"upload-{status}", "revision-1", "fbdd", "fbdd",
        "https://mxlive.example/upload_labworks/BL-5C/",
        datetime.now(timezone.utc), status, record_count, "[]",
    )


def _labwork(experiment_id: str) -> MxLiveLabwork:
    return MxLiveLabwork.from_mapping({"expri_id": experiment_id})


def test_only_definite_failures_leave_upload_available() -> None:
    assert upload_lock_state(()).can_upload
    assert upload_lock_state((_event("failed"),)).can_upload

    for status, availability in (
        ("pending", UploadAvailability.NEEDS_VERIFICATION),
        ("unknown", UploadAvailability.NEEDS_VERIFICATION),
        ("partial", UploadAvailability.PARTIAL),
        ("succeeded", UploadAvailability.UPLOADED),
    ):
        state = upload_lock_state((_event("failed"), _event(status)))
        assert state.availability is availability
        assert not state.can_upload


def test_verification_resolves_unknown_upload_from_stored_records() -> None:
    unknown = replace(_event("unknown"), error_message="ReadTimeout")

    absent = verified_upload_event(unknown, "RawCrystal-01", ())
    complete = verified_upload_event(
        unknown, "RawCrystal-01",
        (_labwork("RawCrystal-01"), _labwork("RawCrystal-01"), _labwork("Other")),
    )
    incomplete = verified_upload_event(
        unknown, "RawCrystal-01", (_labwork("RawCrystal-01"),)
    )

    assert absent.status == "failed"
    assert upload_lock_state((absent,)).can_upload
    assert complete.status == "succeeded"
    assert incomplete.status == "partial"
    assert incomplete.error_message == (
        "ReadTimeout; Verified on MxLive: 1 of 2 records found"
    )
