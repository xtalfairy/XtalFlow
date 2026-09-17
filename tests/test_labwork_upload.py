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


def test_send_labworks_classifies_upload_errors_by_retry_safety() -> None:
    from xtalflow.application.labwork_upload import send_labworks
    from xtalflow.domain.mxlive import (
        MxLivePartialWriteError,
        MxLiveUncertainWriteError,
        MxLiveWriteError,
    )

    def raising(error):
        def upload():
            raise error
        return upload

    succeeded = send_labworks(lambda: {"uploaded_count": 2})

    assert (succeeded.status, succeeded.response_json) == (
        "succeeded", '{"uploaded_count":2}'
    )
    assert send_labworks(raising(MxLivePartialWriteError(1, 2, "HTTP 500"))).status == "partial"
    assert send_labworks(raising(MxLiveUncertainWriteError("ReadTimeout"))).status == "unknown"
    assert send_labworks(raising(MxLiveWriteError("HTTP 400"))).status == "failed"


def test_upload_service_records_attempt_before_outcome(tmp_path) -> None:
    from xtalflow.application.labwork_upload import (
        LabworkUploadService,
        UploadOutcome,
    )
    from xtalflow.domain import Project
    from xtalflow.domain.plan_lifecycle import PlanningDraft, PlanRevision
    from xtalflow.infrastructure import SQLiteReviewStore

    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(timezone.utc)
    project = Project.create("Upload")
    store.save_project(project)
    store.save_planning_draft(PlanningDraft(
        "plan", project.id, "raw_crystal", "Plan", None, "", "BRD4", "0",
        "selection", now, now,
    ))
    revision = store.finalize_plan_revision(
        PlanRevision("revision", "plan", 0, "RawCrystal-01", "{}", "jjh", now)
    )
    service = LabworkUploadService(store)

    pending = service.begin(
        revision, "fbdd", "fbdd", "https://mxlive.example/upload_labworks/BL-5C/",
        ({"expri_id": "RawCrystal-01"},),
    )
    assert service.lock_state("RawCrystal-01").availability is (
        UploadAvailability.NEEDS_VERIFICATION
    )

    service.complete(pending, UploadOutcome("unknown", error_message="ReadTimeout"))
    verified = service.verify(
        store.list_webdb_uploads(revision.id)[0], "RawCrystal-01",
        (_labwork("RawCrystal-01"),),
    )

    assert pending.payload_json == '[{"expri_id":"RawCrystal-01"}]'
    assert verified.status == "succeeded"
    assert service.lock_state("RawCrystal-01").availability is UploadAvailability.UPLOADED
    store.close()
