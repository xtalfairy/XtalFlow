"""Experiment plan lifecycle: drafts, revision snapshots, and finalization.

A finalized revision stores a canonical JSON snapshot of the exact plan that
worksheets and MxLive records are generated from.  The snapshot schema lives
here, independent of the widgets that edit a plan, so the same rules apply to
any user interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, Union
from uuid import NAMESPACE_URL, uuid4, uuid5

from xtalflow.domain.soaking import split_between_positions
from xtalflow.domain.crystal_selection import (
    CrystalSelection,
    SelectedWell,
    SoakingPosition,
)
from xtalflow.domain.condition_test import (
    ConditionAssignment,
    ConditionTestDesign,
    ConditionTestPlan,
    Dose,
)
from xtalflow.domain.crystal_workflow import AssignmentOrder
from xtalflow.domain.experiment_naming import next_id_beside, suggest_experiment_id
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

Plan = Union[FragmentScreenPlan, RawCrystalPlan, ConditionTestPlan]


EXPERIMENT_ID_PREFIXES = {
    PlanType.FRAGMENT_SCREENING: "FragSC",
    PlanType.RAW_CRYSTAL: "RawCrystal",
    # Earlier XtalViewer solvent and cryo pretests used this prefix on MxLive.
    PlanType.CONDITION_TEST: "PreTest",
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


def condition_test_plan_snapshot(plan: ConditionTestPlan, protein: str) -> str:
    return _canonical_json({
        "schema": 1,
        "plan_type": PlanType.CONDITION_TEST.value,
        "protein": protein,
        "assignment_order": plan.assignment_order.value,
        "design": json.loads(plan.design.to_json()),
        "assignments": [
            {
                "image_key": item.selected_well.image_key,
                "image_path": item.selected_well.image_path,
                "plate": item.selected_well.plate_code,
                "well": item.selected_well.well_address,
                **_plate_format_fields(item.selected_well),
                "condition_id": item.condition.id,
                "replicate": item.replicate,
                "targets": [
                    {"id": position.source_target_id,
                     "x_mm": str(position.x_mm),
                     "y_mm": str(position.y_mm),
                     "selected_at": position.selected_at.isoformat()}
                    for position in item.selected_well.soaking_positions
                ],
                "doses": [
                    {"additive_id": dose.additive_id,
                     "volume_nl": str(dose.volume_nl),
                     "actual_percent": str(dose.actual_percent),
                     "start_minutes": dose.start_minutes}
                    for dose in item.doses
                ],
            }
            for item in plan.assignments
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
    items = payload["selections" if plan_type is PlanType.RAW_CRYSTAL else "assignments"]
    if not isinstance(items, list) or not items:
        raise ValueError("snapshot contains no selected wells")
    grouped: dict[str, dict] = {}
    for item in items:
        image_key = str(item["image_key"])
        targets = [item["target"]] if plan_type is PlanType.RAW_CRYSTAL else item["targets"]
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
    if PlanType(payload["plan_type"]) is PlanType.CONDITION_TEST:
        design = ConditionTestDesign.from_json(json.dumps(payload["design"]))
        conditions = {condition.id: condition for condition in design.conditions()}
        assignments = tuple(
            ConditionAssignment(
                wells[str(item["image_key"])],
                conditions[str(item["condition_id"])],
                int(item["replicate"]),
                tuple(
                    Dose(
                        str(dose["additive_id"]), Decimal(str(dose["volume_nl"])),
                        Decimal(str(dose["actual_percent"])), int(dose["start_minutes"]),
                    )
                    for dose in item["doses"]
                ),
            )
            for item in payload["assignments"]
        )
        return ConditionTestPlan(selection, design, assignments, order)
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


class PlanComparison(str, Enum):
    """How a stored revision stands against the plan the editor builds now."""

    SAME = "same"
    DIVISION_ONLY = "division_only"
    DIFFERENT = "different"


def _without_position_shares(snapshot: dict) -> tuple[dict, list]:
    """The snapshot with each position's share taken out, and those shares.

    By subtraction rather than by listing what to compare, so a field nobody
    thought of here - a protein name, a library row - still counts as a
    difference. Only the one value this is about is removed.
    """
    stripped = json.loads(json.dumps(snapshot))
    shares = []
    for assignment in stripped.get("assignments", []):
        if not isinstance(assignment, dict):
            continue
        well = []
        for target in assignment.get("targets", []):
            if isinstance(target, dict) and "volume_nl" in target:
                well.append(target.pop("volume_nl"))
        shares.append(well)
    return stripped, shares


def _same_totals(one: list, other: list) -> bool:
    """Whether each well receives the same amount, however it is divided."""
    if len(one) != len(other) or not any(one):
        return False
    for left, right in zip(one, other):
        if not left or not right:
            return False
        try:
            if sum(Decimal(value) for value in left) != sum(
                Decimal(value) for value in right
            ):
                return False
        except (ArithmeticError, TypeError, ValueError):
            return False
    return True


def compare_plans(one: str | None, other: str | None) -> PlanComparison:
    """Whether two snapshots are the same plan, and if not, how they differ.

    One place answers this, because it used to be answered in five with
    three different rules: this function, a bare ``==`` behind the sentence
    on the Review step, and another ``==`` behind the buttons. They
    disagreed, so moving a fragment library's folder left the header saying
    "Finalized r1" while the step beside it said the plan had changed and
    the save button was dead.

    DIVISION_ONLY is a revision that differs only in how each well's dose is
    spread between its positions. The amount reaching each drop is the same,
    and nothing a person can edit here produces it - there is no control for
    one position's share - so it means the rule that divides a dose is not
    the rule that divided this one. It does not say why. An older build,
    another machine and a hand-edited row all look alike from here.
    """
    if one == other:
        return PlanComparison.SAME
    if one is None or other is None:
        return PlanComparison.DIFFERENT
    try:
        left, right = json.loads(one), json.loads(other)
    except ValueError:
        return PlanComparison.DIFFERENT
    if not isinstance(left, dict) or not isinstance(right, dict):
        return PlanComparison.DIFFERENT
    left.pop("library_id", None)
    right.pop("library_id", None)
    if left == right:
        return PlanComparison.SAME
    bare_left, shares_left = _without_position_shares(left)
    bare_right, shares_right = _without_position_shares(right)
    if bare_left == bare_right and _same_totals(shares_left, shares_right):
        return PlanComparison.DIVISION_ONLY
    return PlanComparison.DIFFERENT


def division_is_current(snapshot_json: str | None) -> bool:
    """Whether a stored revision divides its doses the way this build does.

    Asked of one snapshot alone, with no editor and no library, because the
    place that needs it is the one where the editor can build nothing: a
    revision whose library has moved composes to None, and "nothing to
    compare" was being read as "unchanged". That let a revision divided by
    a rule this build no longer uses write its worksheet, which is the one
    thing that must not happen - the file goes to the instrument and the
    liquid goes where the file says.

    A snapshot that holds no per-position shares, such as a condition test's,
    divides nothing here and is current by default.
    """
    if not snapshot_json:
        return True
    try:
        snapshot = json.loads(snapshot_json)
    except ValueError:
        return False
    if not isinstance(snapshot, dict):
        return False
    for assignment in snapshot.get("assignments", []):
        if not isinstance(assignment, dict):
            continue
        shares = [
            target["volume_nl"]
            for target in assignment.get("targets", [])
            if isinstance(target, dict) and "volume_nl" in target
        ]
        if not shares:
            continue
        try:
            amounts = [Decimal(value) for value in shares]
            if tuple(amounts) != split_between_positions(
                sum(amounts), len(amounts)
            ):
                return False
        except (ArithmeticError, TypeError, ValueError):
            return False
    return True


def plans_match(one: str | None, other: str | None) -> bool:
    """Whether two snapshots fix the same plan.

    ``library_id`` is where a fragment library was read from, not part of
    the plan. The same file in a renamed folder is the same plan, and
    counting the path as a change turned a finalized revision back into a
    draft - which refuses to save its worksheets or upload it, for an
    experiment whose liquid may already be in the wells.

    It stays in the snapshot: taking it out would change the canonical JSON
    of every revision already stored, and they would all read as changed.
    """
    return compare_plans(one, other) is PlanComparison.SAME


# A part revision records that some of a finalized revision's crystals went
# to MxLive under an experiment ID of their own. It lives in the snapshot
# and not in a column of its own, because plan_revision's schema is shared
# with XtalFlow 0.9 and widening it is a one-way door.
PART_OF_KEY = "part_of"
# Which half a part is. The late half's ID belongs to the plan rather than
# to one split, so a plan finalized again can find the name its late
# crystals already went to MxLive under instead of minting a second one.
PART_ROLE_KEY = "part_role"
AHEAD_PART = "harvested_soonest"
LATE_PART = "harvested_late"


def is_part_revision(revision) -> bool:
    """Whether this revision is a part of another, rather than a plan of its own.

    A part is not the plan: it holds some of a whole revision's assignments
    so that they can be uploaded under their own experiment ID, and the
    whole revision is still what the worksheets and the source wells came
    from. Anything asking "what is this plan now" has to skip parts.

    Unreadable snapshots answer False, because the alternative is hiding a
    revision somebody finalized.
    """
    try:
        return bool(json.loads(revision.snapshot_json).get(PART_OF_KEY))
    except (AttributeError, TypeError, ValueError):
        return False


def snapshot_for_part(
    snapshot_json: str, condition_ids: frozenset[str], part_of: str, role: str
) -> str:
    """A whole revision's snapshot holding only these conditions' crystals.

    The design is left whole on both sides. Only the assignments are
    narrowed, because that is what plan_from_snapshot builds the selection
    and the labwork records from, and because a design with conditions
    removed would no longer describe the plate that was actually dispensed.

    Narrowed by condition and not by well: a harvest time is a property of
    the condition, so this is the same cut the SHIFTER worksheets were
    already written on.
    """
    payload = json.loads(snapshot_json)
    whole = payload.get("assignments", ())
    kept = [
        item for item in whole
        if str(item.get("condition_id", "")) in condition_ids
    ]
    if not kept:
        raise ValueError("a part with no crystals is not a part of anything")
    payload["assignments"] = kept
    payload[PART_OF_KEY] = part_of
    payload[PART_ROLE_KEY] = role
    return _canonical_json(payload)


def compound_ids_in_snapshot(snapshot_json: str | None) -> tuple[str, ...]:
    """The compound IDs a finalized fragment screen actually used.

    Read from the revision's own snapshot, which holds every fragment it
    was finalized with, so it can be compared against a library found
    somewhere other than where it was recorded.
    """
    if not snapshot_json:
        return ()
    try:
        payload = json.loads(snapshot_json)
    except ValueError:
        return ()
    if not isinstance(payload, dict):
        return ()
    found: list[str] = []
    for item in payload.get("assignments") or ():
        fragment = item.get("fragment") if isinstance(item, dict) else None
        if isinstance(fragment, dict) and isinstance(
            fragment.get("compound_id"), str
        ):
            found.append(fragment["compound_id"])
    return tuple(found)


def saved_plan_status(
    last_revision: PlanRevision | None,
    last_revision_snapshot: str | None,
    current_snapshot: str | None,
) -> PlanStatus:
    if last_revision is not None and plans_match(
        last_revision_snapshot, current_snapshot
    ):
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
        """The plan as it now stands, which is never a part of it.

        A part revision holds some of a whole revision's crystals so they
        could be uploaded under their own experiment ID. It is not the
        plan: the worksheets, the source wells and the draft comparison all
        belong to the whole revision it was taken from, so a part must
        never become what this returns.
        """
        whole = tuple(
            revision for revision in self.store.list_plan_revisions(plan_id)
            if not is_part_revision(revision)
        )
        return whole[-1] if whole else None

    def parts_of(self, revision: PlanRevision) -> tuple[PlanRevision, ...]:
        """The parts this revision was uploaded in, oldest first; empty if whole."""
        return tuple(
            part for part in self.store.list_plan_revisions(revision.plan_id)
            if is_part_revision(part)
            and json.loads(part.snapshot_json).get(PART_OF_KEY) == revision.id
        )

    def all_parts(self, plan_id: str) -> tuple[PlanRevision, ...]:
        """Every part of this plan, of whichever revision, oldest first.

        A plan can be finalized again after a split - to retry an upload, or
        because something was wrong - and the records already on MxLive
        belong to the parts of the revision they were taken from. Anything
        asking what this plan sent has to look at all of them; asking only
        about the newest revision's parts dropped a whole half of a plate
        out of the harvest check, where every crystal then read as not
        picked yet.
        """
        return tuple(
            part for part in self.store.list_plan_revisions(plan_id)
            if is_part_revision(part)
        )

    def whole_revision_id_of(self, part: PlanRevision) -> str | None:
        """Which revision this part was taken from; None if it is not a part."""
        try:
            return json.loads(part.snapshot_json).get(PART_OF_KEY)
        except (AttributeError, TypeError, ValueError):
            return None

    def late_part_id(self, plan_id: str) -> str | None:
        """The ID the late half of this plan already has, if it has one.

        The late ID belongs to the plan and not to one split. A plan can be
        finalized again - to retry an upload, or because something was
        wrong - and a later split must put the late crystals under the name
        they already went out under, or be refused by its lock. Minting a
        fresh one would put the same crystals on MxLive twice, under two
        names, which cannot be taken back.
        """
        for revision in self.store.list_plan_revisions(plan_id):
            if not is_part_revision(revision):
                continue
            if json.loads(revision.snapshot_json).get(PART_ROLE_KEY) == LATE_PART:
                return revision.experiment_id
        return None

    def split_for_upload(
        self,
        revision: PlanRevision,
        held_back: frozenset[str],
        goes_ahead: frozenset[str],
        finalized_by: str,
        also_taken: set[str] = frozenset(),
    ) -> tuple[PlanRevision, PlanRevision]:
        """Two parts of one finalized revision, so each uploads under its own ID.

        Nothing physical is divided: one plate, one dispense, one set of
        source wells, and worksheets that were already written one per
        harvest time. All that differs is the experiment ID the records go
        out under, which is what MxLive makes a shipment per - so the
        crystals that are picked today can be shot today instead of waiting
        for the ones picked tomorrow.

        The part that goes ahead keeps the revision's own ID, so an upload
        of it is the upload this experiment was always going to have. The
        part held back takes the ID beside it, or the one the plan's late
        half already has.

        Both rows are written together. One of them alone would read as a
        finished split and the crystals left out would never be offered
        again, so this is all or nothing - including the naming, which is
        done before anything is written.

        Returned in upload order, soonest first.
        """
        if not held_back or not goes_ahead:
            raise ValueError("a split needs crystals on both sides")
        late_id = self.late_part_id(revision.plan_id) or next_id_beside(
            revision.experiment_id,
            self.store.reserved_experiment_ids() | set(also_taken),
        )
        now = datetime.now(timezone.utc)
        ahead, later = self.store.finalize_plan_revisions(
            (
                PlanRevision(
                    str(uuid4()), revision.plan_id, 0, revision.experiment_id,
                    snapshot_for_part(
                        revision.snapshot_json, goes_ahead, revision.id, AHEAD_PART
                    ),
                    finalized_by, now,
                ),
                PlanRevision(
                    str(uuid4()), revision.plan_id, 0, late_id,
                    snapshot_for_part(
                        revision.snapshot_json, held_back, revision.id, LATE_PART
                    ),
                    finalized_by, now,
                ),
            )
        )
        return ahead, later

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
