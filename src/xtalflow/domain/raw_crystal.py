from __future__ import annotations

from dataclasses import dataclass

from .crystal_workflow import AssignmentOrder, SelectedCrystal
from .crystal_selection import (
    CrystalSelection,
    SelectedWell,
    SoakingPosition,
    crystal_selection_from_selected_crystals,
    order_selected_wells,
)


@dataclass(frozen=True)
class RawCrystalSelection:
    selected_well: SelectedWell
    position: SoakingPosition


@dataclass(frozen=True)
class RawCrystalPlan:
    selections: tuple[RawCrystalSelection, ...]
    assignment_order: AssignmentOrder

    @property
    def selected_wells(self) -> tuple[SelectedWell, ...]:
        ordered: list[SelectedWell] = []
        seen: set[str] = set()
        for selection in self.selections:
            if selection.selected_well.id in seen:
                continue
            seen.add(selection.selected_well.id)
            ordered.append(selection.selected_well)
        return tuple(ordered)


def build_raw_crystal_plan(
    selection: CrystalSelection | tuple[SelectedCrystal, ...],
    assignment_order: AssignmentOrder = AssignmentOrder.SELECTION,
) -> RawCrystalPlan:
    if isinstance(selection, tuple):
        selection = crystal_selection_from_selected_crystals(
            "legacy-raw-plan", selection
        )
    ordered_wells = order_selected_wells(selection, assignment_order)
    ordered = tuple(
        RawCrystalSelection(well, position)
        for well in ordered_wells
        for position in sorted(
            well.soaking_positions, key=lambda item: item.position_order
        )
    )
    return RawCrystalPlan(ordered, assignment_order)
