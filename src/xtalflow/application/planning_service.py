"""Experiment plan lifecycle: drafts, revision snapshots, and finalization.

A finalized revision stores a canonical JSON snapshot of the exact plan that
worksheets and MxLive records are generated from.  The snapshot schema lives
here, independent of the widgets that edit a plan, so the same rules apply to
any user interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from xtalflow.domain.crystal_selection import CrystalSelection, SelectedWell
from xtalflow.domain.experiment_naming import suggest_experiment_id
from xtalflow.domain.experiment_project import ExperimentPlan, ExperimentProject, PlanType
from xtalflow.domain.fragment_screening import FragmentScreenPlan
from xtalflow.domain.plan_lifecycle import PlanningDraft, PlanRevision
from xtalflow.domain.raw_crystal import RawCrystalPlan


EXPERIMENT_ID_PREFIXES = {
    PlanType.FRAGMENT_SCREENING: "FragSC",
    PlanType.RAW_CRYSTAL: "RawCrystal",
}


class PlanningStorePort(Protocol):
    def save_planning_draft(self, draft: PlanningDraft) -> None: ...

    def finalize_plan_revision(self, revision: PlanRevision) -> PlanRevision: ...

    def list_plan_revisions(self, plan_id: str) -> tuple[PlanRevision, ...]: ...

    def reserved_experiment_ids(self) -> set[str]: ...

    def save_experiment_project(self, project: ExperimentProject) -> None: ...

    def load_experiment_project(self, project_id: str) -> ExperimentProject | None: ...


@dataclass(frozen=True)
class PlanStatus:
    """How a plan's lifecycle is shown in the plan editor and the plan list."""

    label: str
    list_status: str
    finalized: bool = False


def fragment_plan_snapshot(
    plan: FragmentScreenPlan, protein: str, library_id: str | None, library_rows: str
) -> str:
    return _canonical_json({
        "schema": 1,
        "plan_type": PlanType.FRAGMENT_SCREENING.value,
        "protein": protein,
        "library_name": plan.library.name,
        "library_id": library_id,
        "library_rows": library_rows,
        "volume_per_crystal_nl": str(plan.volume_per_crystal_nl),
        "assignment_order": plan.assignment_order.value,
        "assignments": [
            {
                "image_key": item.selected_well.image_key,
                "image_path": item.selected_well.image_path,
                "plate": item.selected_well.plate_code,
                "well": item.selected_well.well_address,
                **_plate_format_fields(item.selected_well),
                "fragment": {
                    "vendor": item.fragment.vendor,
                    "library": item.fragment.library,
                    "number": item.fragment.number,
                    "compound_id": item.fragment.compound_id,
                    "formula": item.fragment.formula,
                    "molecular_weight": str(item.fragment.molecular_weight),
                    "smiles": item.fragment.smiles,
                    "concentration_mm": str(item.fragment.concentration_mm),
                    "solvent": item.fragment.solvent,
                    "source_plate": item.fragment.source_plate,
                    "source_well": item.fragment.source_well,
                },
                "targets": [
                    {"id": transfer.position.source_target_id,
                     "x_mm": str(transfer.position.x_mm),
                     "y_mm": str(transfer.position.y_mm),
                     "selected_at": transfer.position.selected_at.isoformat(),
                     "volume_nl": str(transfer.volume_nl)}
                    for transfer in item.transfers
                ],
            }
            for item in plan.assignments
        ],
    })


def raw_crystal_plan_snapshot(plan: RawCrystalPlan, protein: str) -> str:
    return _canonical_json({
        "schema": 1,
        "plan_type": PlanType.RAW_CRYSTAL.value,
        "protein": protein,
        "assignment_order": plan.assignment_order.value,
        "selections": [
            {"image_key": selection.selected_well.image_key,
             "image_path": selection.selected_well.image_path,
             "plate": selection.selected_well.plate_code,
             "well": selection.selected_well.well_address,
             **_plate_format_fields(selection.selected_well),
             "target": {"id": selection.position.source_target_id,
                        "x_mm": str(selection.position.x_mm),
                        "y_mm": str(selection.position.y_mm),
                        "selected_at": selection.position.selected_at.isoformat()}}
            for selection in plan.selections
        ],
    })


def _plate_format_fields(selected_well: SelectedWell) -> dict[str, object]:
    # Schema 1 snapshots finalized before format versions were recorded mean
    # version 1. Omitting that default keeps them byte-identical, so existing
    # finalized plans still match; any later version is always written.
    fields: dict[str, object] = {"plate_format_id": selected_well.plate_format_id}
    if selected_well.plate_format_version != 1:
        fields["plate_format_version"] = selected_well.plate_format_version
    return fields


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def saved_plan_status(
    last_revision: PlanRevision | None,
    last_revision_snapshot: str | None,
    current_snapshot: str | None,
) -> PlanStatus:
    if last_revision is not None and last_revision_snapshot == current_snapshot:
        finalized = f"Finalized r{last_revision.revision}"
        return PlanStatus(finalized, finalized, finalized=True)
    if last_revision is not None:
        changed = f"Draft changes after r{last_revision.revision}"
        return PlanStatus(changed, changed)
    return PlanStatus("Draft · saved", "Draft")


def restored_plan_status(
    last_revision: PlanRevision | None,
    current_snapshot: str | None,
    selection_owned: bool,
) -> PlanStatus | None:
    """Status of a plan reopened from the database, or None for a plain draft."""
    if not selection_owned:
        return PlanStatus(
            "Legacy Draft · selection needs review", "Legacy · Review selection"
        )
    if last_revision is None:
        return None
    if last_revision.snapshot_json == current_snapshot:
        finalized = f"Finalized r{last_revision.revision}"
        return PlanStatus(finalized, finalized, finalized=True)
    changed = f"Draft changes after r{last_revision.revision}"
    return PlanStatus(changed, changed)


class PlanningService:
    def __init__(self, store: PlanningStorePort) -> None:
        self.store = store

    def save_draft(self, draft: PlanningDraft) -> None:
        self.store.save_planning_draft(draft)

    def latest_revision(self, plan_id: str) -> PlanRevision | None:
        revisions = self.store.list_plan_revisions(plan_id)
        return revisions[-1] if revisions else None

    def save_selection_snapshot(
        self,
        plan_id: str,
        plan_name: str,
        plan_type: PlanType,
        selection: CrystalSelection,
        created_at: datetime,
    ) -> None:
        """Fix the selected wells a plan works on, independent of later review."""
        self.store.save_experiment_project(
            ExperimentProject(
                plan_id,
                plan_name,
                selection,
                ExperimentPlan(
                    f"{plan_id}:plan", plan_id, plan_type, created_at, created_at
                ),
                created_at,
                created_at,
            )
        )

    def suggest_experiment_id(
        self,
        plan_type: PlanType,
        protein: str,
        also_reserved: set[str] = frozenset(),
        now: datetime | None = None,
    ) -> str:
        reserved = self.store.reserved_experiment_ids() | set(also_reserved)
        return suggest_experiment_id(
            EXPERIMENT_ID_PREFIXES[plan_type], protein, reserved, now
        )

    def finalize(
        self,
        plan_id: str,
        plan_type: PlanType,
        protein: str,
        snapshot: str,
        assigned_experiment_id: str | None,
        finalized_by: str,
        also_reserved: set[str] = frozenset(),
    ) -> PlanRevision:
        """Store a new revision; a plan keeps its experiment ID across revisions."""
        experiment_id = assigned_experiment_id or self.suggest_experiment_id(
            plan_type, protein, also_reserved
        )
        return self.store.finalize_plan_revision(
            PlanRevision(
                str(uuid4()), plan_id, 0, experiment_id, snapshot, finalized_by,
                datetime.now(timezone.utc),
            )
        )
