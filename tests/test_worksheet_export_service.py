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
