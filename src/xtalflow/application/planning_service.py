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
from decimal import Decimal
from typing import Protocol, Union
from uuid import NAMESPACE_URL, uuid4, uuid5

from xtalflow.domain.crystal_selection import (
    CrystalSelection,
    SelectedWell,
    SoakingPosition,
)
from xtalflow.domain.crystal_workflow import AssignmentOrder
from xtalflow.domain.experiment_naming import suggest_experiment_id
from xtalflow.domain.experiment_project import ExperimentPlan, ExperimentProject, PlanType
from xtalflow.domain.fragment_screening import (
    Fragment,
    FragmentAssignment,
    FragmentLibrary,
    FragmentScreenPlan,
    FragmentTransfer,
)
from xtalflow.domain.plan_lifecycle import PlanningDraft, PlanRevision
from xtalflow.domain.raw_crystal import RawCrystalPlan, RawCrystalSelection

Plan = Union[FragmentScreenPlan, RawCrystalPlan]


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
    """How an experiment's plan lifecycle is shown."""

    label: str
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


def selection_from_snapshot(
    plan_id: str,
    payload: dict,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> CrystalSelection:
    """Rebuild the selected wells recorded in a revision snapshot.

    IDs are derived from the plan and source targets, so the same snapshot
    always yields the same selection.
    """
    plan_type = PlanType(payload.get("plan_type"))
    items = payload["assignments" if plan_type is PlanType.FRAGMENT_SCREENING else "selections"]
    if not isinstance(items, list) or not items:
        raise ValueError("snapshot contains no selected wells")
    grouped: dict[str, dict] = {}
    for item in items:
        image_key = str(item["image_key"])
        targets = item["targets"] if plan_type is PlanType.FRAGMENT_SCREENING else [item["target"]]
        entry = grouped.setdefault(
            image_key,
            {
                "image_key": image_key,
                "image_path": str(item.get("image_path") or ""),
                "plate": str(item["plate"]),
                "well": str(item["well"]),
                "plate_format_id": str(item.get("plate_format_id") or ""),
                "plate_format_version": int(item.get("plate_format_version") or 1),
                "targets": [],
            },
        )
        entry["targets"].extend(targets)

    selection_id = str(uuid5(NAMESPACE_URL, f"xtalflow:{plan_id}:selection"))
    wells: list[SelectedWell] = []
    for well_order, entry in enumerate(grouped.values(), start=1):
        well_id = str(uuid5(NAMESPACE_URL, f"xtalflow:{plan_id}:well:{entry['image_key']}"))
        positions = tuple(
            SoakingPosition(
                str(uuid5(
                    NAMESPACE_URL,
                    f"xtalflow:{plan_id}:position:{target['id']}:{position_order}",
                )),
                well_id,
                str(target["id"]),
                position_order,
                Decimal(str(target["x_mm"])),
                Decimal(str(target["y_mm"])),
                datetime.fromisoformat(str(target["selected_at"])),
            )
            for position_order, target in enumerate(entry["targets"], start=1)
        )
        if not positions:
            raise ValueError(f"{entry['image_key']} contains no soaking positions")
        wells.append(
            SelectedWell(
                id=well_id,
                crystal_selection_id=selection_id,
                image_key=entry["image_key"],
                plate_code=entry["plate"],
                well_address=entry["well"],
                selection_order=well_order,
                selected_at=min(position.selected_at for position in positions),
                soaking_positions=positions,
                image_path=entry["image_path"],
                plate_format_id=entry["plate_format_id"],
                plate_format_version=entry["plate_format_version"],
            )
        )
    first_selected = min(well.selected_at for well in wells)
    return CrystalSelection(
        selection_id, plan_id, tuple(wells),
        created_at or first_selected, updated_at or created_at or first_selected,
    )


def plan_from_snapshot(plan_id: str, snapshot_json: str) -> Plan:
    """The exact plan a revision fixed, independent of editors and current rules.

    Worksheets and MxLive records are built from this, so a revision finalized
    under earlier volume rules is delivered exactly as it was finalized.
    """
    payload = json.loads(snapshot_json)
    if not isinstance(payload, dict):
        raise ValueError("snapshot root must be an object")
    selection = selection_from_snapshot(plan_id, payload)
    wells = {well.image_key: well for well in selection.wells}
    order = AssignmentOrder(payload["assignment_order"])
    if PlanType(payload["plan_type"]) is PlanType.RAW_CRYSTAL:
        consumed: dict[str, int] = {}
        selections = []
        for item in payload["selections"]:
            well = wells[str(item["image_key"])]
            index = consumed.get(well.image_key, 0)
            consumed[well.image_key] = index + 1
            selections.append(RawCrystalSelection(well, well.soaking_positions[index]))
        return RawCrystalPlan(tuple(selections), order)
    assignments = []
    fragments = []
    for item in payload["assignments"]:
        well = wells[str(item["image_key"])]
        data = item["fragment"]
        fragment = Fragment(
            data["vendor"], data["library"], data["number"], data["compound_id"],
            data["formula"], Decimal(data["molecular_weight"]), data["smiles"],
            Decimal(data["concentration_mm"]), data["solvent"],
            data["source_plate"], data["source_well"],
        )
        fragments.append(fragment)
        transfers = tuple(
            FragmentTransfer(position, Decimal(str(target["volume_nl"])))
            for position, target in zip(well.soaking_positions, item["targets"])
        )
        assignments.append(FragmentAssignment(well, fragment, transfers))
    return FragmentScreenPlan(
        selection,
        FragmentLibrary(str(payload["library_name"]), tuple(fragments)),
        tuple(assignments),
        Decimal(str(payload["volume_per_crystal_nl"])),
        order,
    )


def saved_plan_status(
    last_revision: PlanRevision | None,
    last_revision_snapshot: str | None,
    current_snapshot: str | None,
) -> PlanStatus:
    if last_revision is not None and last_revision_snapshot == current_snapshot:
        finalized = f"Finalized r{last_revision.revision}"
        return PlanStatus(finalized, finalized=True)
    if last_revision is not None:
        return PlanStatus(f"Draft changes after r{last_revision.revision}")
    return PlanStatus("Draft · saved")


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
