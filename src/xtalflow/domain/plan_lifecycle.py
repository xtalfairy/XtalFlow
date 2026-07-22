from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PlanningDraft:
    id: str
    project_id: str
    plan_type: str
    name: str
    library_id: str | None
    library_rows: str
    protein: str
    volume_nl: str
    assignment_order: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PlanRevision:
    id: str
    plan_id: str
    revision: int
    experiment_id: str
    snapshot_json: str
    finalized_by: str
    finalized_at: datetime


@dataclass(frozen=True)
class WorksheetExportEvent:
    id: str
    revision_id: str
    username: str
    exported_at: datetime
    status: str
    echo_path: str | None = None
    shifter1_path: str | None = None
    shifter2_path: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class WebDBUploadEvent:
    id: str
    revision_id: str
    username: str
    account_id: str
    endpoint: str
    attempted_at: datetime
    status: str
    record_count: int
    payload_json: str
    response_json: str | None = None
    error_message: str | None = None
