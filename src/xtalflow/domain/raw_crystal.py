from __future__ import annotations

from dataclasses import dataclass

from .crystal_workflow import AssignmentOrder, CrystalTarget, SelectedCrystal


@dataclass(frozen=True)
class RawCrystalSelection:
    crystal: SelectedCrystal
    target: CrystalTarget


@dataclass(frozen=True)
class RawCrystalPlan:
    selections: tuple[RawCrystalSelection, ...]
    assignment_order: AssignmentOrder

    @property
    def crystals(self) -> tuple[SelectedCrystal, ...]:
        """Return each selected image/drop once in the plan's chosen order."""
        ordered: list[SelectedCrystal] = []
        seen: set[str] = set()
        for selection in self.selections:
            if selection.crystal.image_key in seen:
                continue
            seen.add(selection.crystal.image_key)
            ordered.append(selection.crystal)
        return tuple(ordered)


def build_raw_crystal_plan(
    crystals: tuple[SelectedCrystal, ...],
    assignment_order: AssignmentOrder = AssignmentOrder.SELECTION,
) -> RawCrystalPlan:
    if not crystals:
        raise ValueError("at least one selected crystal is required")
    selections = tuple(
        RawCrystalSelection(crystal, target)
        for crystal in crystals
        for target in crystal.targets
    )
    if assignment_order is AssignmentOrder.SELECTION:
        ordered = tuple(sorted(selections, key=lambda item: item.target.selected_at))
    elif assignment_order is AssignmentOrder.PLATE_WELL:
        ordered = tuple(sorted(selections, key=_plate_well_target_key))
    else:
        raise ValueError(f"unsupported assignment order: {assignment_order}")
    return RawCrystalPlan(ordered, assignment_order)


def _plate_well_target_key(selection: RawCrystalSelection) -> tuple[object, ...]:
    crystal = selection.crystal
    plate = crystal.destination_plate
    plate_key: tuple[int, object] = (0, int(plate)) if plate.isdigit() else (1, plate)
    well = crystal.destination_well
    if len(well) != 4 or not well[1:3].isdigit():
        raise ValueError(f"invalid destination well: {well}")
    return (*plate_key, ord(well[0]) - ord("A"), int(well[1:3]), well[3],
            selection.target.selected_at, selection.target.target_id)
