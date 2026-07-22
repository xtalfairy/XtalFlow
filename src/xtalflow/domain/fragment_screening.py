from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from .crystal_workflow import (
    AssignmentOrder,
    CrystalTarget,
    SelectedCrystal,
)
from .crystal_selection import (
    CrystalSelection,
    SelectedWell,
    SoakingPosition,
    crystal_selection_from_selected_crystals,
    order_selected_wells,
)


TRANSFER_INCREMENT_NL = Decimal("2.5")


@dataclass(frozen=True)
class Fragment:
    vendor: str
    library: str
    number: str
    compound_id: str
    formula: str
    molecular_weight: Decimal
    smiles: str
    concentration_mm: Decimal
    solvent: str
    source_plate: str
    source_well: str

    def __post_init__(self) -> None:
        required = (
            self.vendor,
            self.library,
            self.number,
            self.compound_id,
            self.solvent,
            self.source_plate,
            self.source_well,
        )
        if any(not value.strip() for value in required):
            raise ValueError("fragment identity and source fields must not be empty")
        if self.molecular_weight <= 0:
            raise ValueError("molecular weight must be positive")
        if self.concentration_mm <= 0:
            raise ValueError("fragment concentration must be positive")


@dataclass(frozen=True)
class FragmentLibrary:
    name: str
    fragments: tuple[Fragment, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("library name must not be empty")
        if not self.fragments:
            raise ValueError("fragment library must not be empty")
        source_locations = [
            (fragment.source_plate, fragment.source_well)
            for fragment in self.fragments
        ]
        if len(source_locations) != len(set(source_locations)):
            raise ValueError("fragment source locations must be unique")

    def select_rows(self, expression: str) -> FragmentLibrary:
        row_numbers = parse_library_rows(expression, len(self.fragments))
        return FragmentLibrary(
            self.name,
            tuple(self.fragments[row_number - 1] for row_number in row_numbers),
        )


@dataclass(frozen=True)
class FragmentTransfer:
    position: SoakingPosition
    volume_nl: Decimal


@dataclass(frozen=True)
class FragmentAssignment:
    selected_well: SelectedWell
    fragment: Fragment
    transfers: tuple[FragmentTransfer, ...]

    @property
    def total_volume_nl(self) -> Decimal:
        return sum((item.volume_nl for item in self.transfers), Decimal("0"))


@dataclass(frozen=True)
class FragmentScreenPlan:
    selection: CrystalSelection
    library: FragmentLibrary
    assignments: tuple[FragmentAssignment, ...]
    volume_per_crystal_nl: Decimal
    assignment_order: AssignmentOrder


def build_fragment_screen_plan(
    library: FragmentLibrary,
    selection: CrystalSelection | tuple[SelectedCrystal, ...],
    volume_per_crystal_nl: Decimal,
    assignment_order: AssignmentOrder = AssignmentOrder.SELECTION,
) -> FragmentScreenPlan:
    if isinstance(selection, tuple):
        selection = crystal_selection_from_selected_crystals(
            "legacy-fragment-plan", selection
        )
    if volume_per_crystal_nl <= 0:
        raise ValueError("volume per crystal must be positive")
    units = volume_per_crystal_nl / TRANSFER_INCREMENT_NL
    if units != units.to_integral_value():
        raise ValueError("volume per crystal must use 2.5 nL increments")
    if len(library.fragments) < len(selection.wells):
        raise ValueError("the library does not contain enough fragments")

    ordered_wells = order_selected_wells(selection, assignment_order)
    assignments: list[FragmentAssignment] = []
    total_units = int(units)
    selected_fragments = library.fragments[: len(ordered_wells)]
    for selected_well, fragment in zip(ordered_wells, selected_fragments):
        position_count = len(selected_well.soaking_positions)
        if not position_count:
            raise ValueError(
                f"{selected_well.image_key} needs at least one soaking position"
            )
        if total_units < position_count:
            raise ValueError(
                f"{selected_well.image_key} needs at least "
                f"{position_count * TRANSFER_INCREMENT_NL} nL"
            )
        quota, remainder = divmod(total_units, position_count)
        transfers = tuple(
            FragmentTransfer(
                position=position,
                volume_nl=TRANSFER_INCREMENT_NL
                * (quota + (remainder if index == position_count - 1 else 0)),
            )
            for index, position in enumerate(selected_well.soaking_positions)
        )
        assignments.append(FragmentAssignment(selected_well, fragment, transfers))

    return FragmentScreenPlan(
        selection, library, tuple(assignments), volume_per_crystal_nl,
        assignment_order,
    )


def parse_library_rows(expression: str, row_count: int) -> tuple[int, ...]:
    """Parse one-based CSV data-row selections such as ``1-10, 15, 20-22``."""
    if row_count < 1:
        raise ValueError("library must contain at least one data row")
    tokens = [token.strip() for token in expression.split(",")]
    if not expression.strip() or any(not token for token in tokens):
        raise ValueError("library row selection must not be empty")
    selected: list[int] = []
    for token in tokens:
        match = re.fullmatch(r"(\d+)(?:\s*[-~]\s*(\d+))?", token)
        if match is None:
            raise ValueError(f"invalid library row selection: {token!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end:
            raise ValueError(f"library row range must be ascending: {token!r}")
        if start < 1 or end > row_count:
            raise ValueError(
                f"library rows must be between 1 and {row_count}: {token!r}"
            )
        selected.extend(range(start, end + 1))
    if len(selected) != len(set(selected)):
        raise ValueError("library row selection must not contain duplicates")
    return tuple(selected)
