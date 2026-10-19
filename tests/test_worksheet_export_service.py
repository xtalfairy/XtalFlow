from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from xtalflow.application.worksheet_export import WorksheetExportService
from xtalflow.domain.plan_lifecycle import PlanRevision
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.infrastructure.worksheet_exporter import WorksheetExporter
from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments

from test_worksheet_exporter import fragment_plan


class AuditStore:
    def __init__(self) -> None:
        self.events = []

    def record_worksheet_export(self, event) -> None:
        self.events.append(event)


def _service(tmp_path: Path, audit: AuditStore) -> WorksheetExportService:
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    return WorksheetExportService(WorksheetExporter(settings, "scientist"), audit, "scientist")


REVISION = PlanRevision(
    "revision", "plan", 1, "RawCrystal-01", "{}", "scientist",
    datetime.now(timezone.utc),
)


def test_raw_crystal_plan_is_delivered_only_to_shifter_folders(tmp_path: Path) -> None:
    audit = AuditStore()
    service = _service(tmp_path, audit)
    raw_plan = build_raw_crystal_plan(fragment_plan().selection)

    result = service.deliver(raw_plan, "RawCrystal-01")
    service.record(REVISION, "succeeded", result=result)

    assert not (tmp_path / "echo650").exists()
    assert audit.events[0].path_for("echo650") is None
    assert audit.events[0].path_for("shifter1") == str(result.path_for("shifter1"))


def test_fragment_plan_can_be_delivered_to_alternate_root(tmp_path: Path) -> None:
    audit = AuditStore()
    service = _service(tmp_path, audit)

    result = service.deliver(fragment_plan(), "FragSC-01", tmp_path / "chosen")
    service.record(REVISION, "failed", error="share offline")

    assert result.path_for("echo650").parent == tmp_path / "chosen" / "echo650" / "scientist"
    assert (audit.events[0].status, audit.events[0].path_for("echo650")) == ("failed", None)
    assert audit.events[0].error_message == "share offline"


def _condition_plan():
    """A solvent test that draws one well from a shared plate."""
    from dataclasses import replace as _replace

    from test_condition_test import _design, _selection
    from xtalflow.domain.condition_test import ConditionTestDesign, build_condition_test_plan

    design = _design()
    design = ConditionTestDesign(
        (_replace(design.additives[0], source_plate_type="384PP_DMSO2"),),
        design.series,
        design.drop_volume_nl,
    )
    return build_condition_test_plan(design, _selection(4))


class AwayRecord:
    """The shared record with the ECHO folder gone from under it."""

    def __init__(self, error) -> None:
        self.error = error
        self.published = False

    def commit(self, new_entries, experiment_id, publish):
        raise self.error


def test_a_condition_test_saves_while_the_shared_record_is_away(tmp_path: Path) -> None:
    """The record goes away with the ECHO; the plan is finalized and the plate is out."""
    from xtalflow.application.worksheet_export import SourcePlateRecordUnavailable
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord, record_directory

    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    service = _service(tmp_path, AuditStore())
    service.source_record = AwayRecord(SourcePlateRecordUnavailable("the record is away"))
    service.held_record = held

    result = service.deliver(_condition_plan(), "SolventTest-01")

    # The worksheets went out, both of them, exactly as for any other plan.
    assert result.path_for("echo650") is not None
    assert result.path_for("shifter1") is not None
    # And the wells this took are kept in the record's own format, so the
    # file is a drop-in append once the ECHO folder is back.
    kept = held.entries("LDV-1")
    assert [entry.wells for entry in kept] == [("A1",)]
    assert kept[0].experiment_id == "SolventTest-01"


def test_a_record_someone_else_is_writing_still_stops_the_save(tmp_path: Path) -> None:
    """Contention is not absence: the record is right there, so wait rather than go on."""
    import pytest

    from xtalflow.application.worksheet_export import SourceRecordBusy
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord, record_directory

    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    service = _service(tmp_path, AuditStore())
    service.source_record = AwayRecord(SourceRecordBusy("in use by someone else"))
    service.held_record = held

    with pytest.raises(SourceRecordBusy):
        service.deliver(_condition_plan(), "SolventTest-02")

    assert not (tmp_path / "pending").exists()
