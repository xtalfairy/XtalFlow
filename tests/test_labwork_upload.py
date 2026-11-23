import json
from dataclasses import replace
from datetime import datetime, timezone

from xtalflow.application.labwork_upload import (
    addresses_sent_twice,
    UploadAvailability,
    records_not_stored,
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
    store.workspace.save_project(project)
    store.planning.save_planning_draft(PlanningDraft(
        "plan", project.id, "raw_crystal", "Plan", None, "", "BRD4", "0",
        "selection", now, now,
    ))
    revision = store.planning.finalize_plan_revision(
        PlanRevision("revision", "plan", 0, "RawCrystal-01", "{}", "jjh", now)
    )
    service = LabworkUploadService(store.audit)

    pending = service.begin(
        revision, "fbdd", "fbdd", "https://mxlive.example/upload_labworks/BL-5C/",
        ({"expri_id": "RawCrystal-01"},),
    )
    assert service.lock_state("RawCrystal-01").availability is (
        UploadAvailability.NEEDS_VERIFICATION
    )

    service.complete(pending, UploadOutcome("unknown", error_message="ReadTimeout"))
    verified = service.verify(
        store.audit.list_webdb_uploads(revision.id)[0], "RawCrystal-01",
        (_labwork("RawCrystal-01"),),
    )

    assert pending.payload_json == '[{"expri_id":"RawCrystal-01"}]'
    assert verified.status == "succeeded"
    assert service.lock_state("RawCrystal-01").availability is UploadAvailability.UPLOADED
    # The experiment list reads every experiment's history in one query.
    assert {
        experiment_id: lock.availability
        for experiment_id, lock in service.lock_states().items()
    } == {"RawCrystal-01": UploadAvailability.UPLOADED}
    store.close()


def _addressed(experiment_id: str, plate: str, well: str) -> MxLiveLabwork:
    return MxLiveLabwork.from_mapping({
        "expri_id": experiment_id, "plate_code": plate, "plate_well": well,
    })


def _sent(*wells: str) -> str:
    return json.dumps([
        {"plate_code": "2069", "plate_well": well, "expri_id": "RawCrystal-01"}
        for well in wells
    ])


def test_a_wrong_set_of_records_is_not_a_complete_upload() -> None:
    """Counting let one missing and one stranger pass as two of two.

    The attempt was then written down as succeeded, and after that its
    payload is believed to be what MxLive holds - which is the only proof of
    ownership a harvest write has.
    """
    event = replace(_event("unknown"), payload_json=_sent("A01a", "A02a"))

    verified = verified_upload_event(event, "RawCrystal-01", (
        _addressed("RawCrystal-01", "2069", "A01a"),
        _addressed("RawCrystal-01", "2069", "H12d"),
    ))

    assert verified.status == "partial"
    assert "missing 2069 A02a" in (verified.error_message or "")
    assert "2069 H12d" in (verified.error_message or "")


def test_an_upload_whose_records_are_all_there_is_complete() -> None:
    event = replace(_event("unknown"), payload_json=_sent("A01a", "A02a"))

    verified = verified_upload_event(event, "RawCrystal-01", (
        _addressed("RawCrystal-01", "2069", "A01a"),
        _addressed("RawCrystal-01", "2069", "A02a"),
        _addressed("Other-02", "2069", "A03a"),
    ))

    assert verified.status == "succeeded"


def test_which_records_are_missing_is_named_so_they_can_be_sent() -> None:
    """One record per call, so an interrupted upload leaves particular wells.

    Naming them is what lets the rest be finished without re-sending - and
    re-sending is what would overwrite the records that did land.
    """
    event = replace(_event("partial"), payload_json=_sent("A01a", "A02a", "A03a"))

    missing = records_not_stored(event, "RawCrystal-01", (
        _addressed("RawCrystal-01", "2069", "A02a"),
        _addressed("Other-02", "2069", "A01a"),
    ))

    assert [payload["plate_well"] for payload in missing] == ["A01a", "A03a"]


def test_an_attempt_with_no_readable_payload_still_verifies_by_count() -> None:
    """A corrupted audit row must not make an experiment unverifiable.

    Which wells are missing cannot be worked out, so nothing is offered to
    send - but the attempt can still be resolved.
    """
    event = replace(_event("unknown", record_count=2), payload_json="not json")

    assert verified_upload_event(event, "RawCrystal-01", ()).status == "failed"
    assert records_not_stored(event, "RawCrystal-01", ()) == ()


def test_one_address_twice_in_a_payload_is_named_so_it_can_be_refused() -> None:
    """The second of them would be an update, and an update reaches siblings.

    MxLive looks a record up by plate and well and creates it if there is
    none, so the first of a repeated address creates and the second updates -
    filtered without the plate, overwriting column for column the record this
    same upload created moments earlier. Nothing downstream could find it:
    payloads_by_well keeps the last record of an address, so the audit would
    read as though what was sent is what MxLive holds.
    """
    payloads = (
        {"plate_code": "2215", "plate_well": "C02a", "soak_id": "a"},
        {"plate_code": "2216", "plate_well": "C02a", "soak_id": "b"},
        {"plate_code": "2215", "plate_well": "C02a", "soak_id": "c"},
    )

    # Two plates sharing an address is not the problem here - one plate
    # carrying the same address twice is.
    assert addresses_sent_twice(payloads) == ("2215 C02a",)
    assert addresses_sent_twice(payloads[:2]) == ()
