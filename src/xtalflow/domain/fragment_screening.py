from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from .crystal_workflow import (
    AssignmentOrder,
    CrystalTarget,
    SelectedCrystal,
    order_crystals,
)


TRANSFER_INCREMENT_NL = Decimal("2.5")


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class FragmentTransfer:
    target: CrystalTarget
    volume_nl: Decimal


@dataclass(frozen=True, slots=True)
class FragmentAssignment:
    crystal: SelectedCrystal
    fragment: Fragment
    transfers: tuple[FragmentTransfer, ...]

    @property
    def total_volume_nl(self) -> Decimal:
        return sum((item.volume_nl for item in self.transfers), Decimal("0"))


@dataclass(frozen=True, slots=True)
class FragmentScreenPlan:
    library: FragmentLibrary
    assignments: tuple[FragmentAssignment, ...]
    volume_per_crystal_nl: Decimal
    assignment_order: AssignmentOrder


def build_fragment_screen_plan(
    library: FragmentLibrary,
    crystals: tuple[SelectedCrystal, ...],
    volume_per_crystal_nl: Decimal,
    assignment_order: AssignmentOrder = AssignmentOrder.SELECTION,
) -> FragmentScreenPlan:
    if not crystals:
        raise ValueError("at least one selected crystal is required")
    if volume_per_crystal_nl <= 0:
        raise ValueError("volume per crystal must be positive")
    units = volume_per_crystal_nl / TRANSFER_INCREMENT_NL
    if units != units.to_integral_value():
        raise ValueError("volume per crystal must use 2.5 nL increments")
    if len(library.fragments) < len(crystals):
        raise ValueError("the library does not contain enough fragments")

    ordered_crystals = order_crystals(crystals, assignment_order)
    assignments: list[FragmentAssignment] = []
    total_units = int(units)
    selected_fragments = library.fragments[: len(ordered_crystals)]
    for crystal, fragment in zip(ordered_crystals, selected_fragments, strict=True):
        target_count = len(crystal.targets)
        if total_units < target_count:
            raise ValueError(
                f"{crystal.image_key} needs at least "
                f"{target_count * TRANSFER_INCREMENT_NL} nL"
            )
        quota, remainder = divmod(total_units, target_count)
        transfers = tuple(
            FragmentTransfer(
                target=target,
                volume_nl=TRANSFER_INCREMENT_NL
                * (quota + (remainder if index == target_count - 1 else 0)),
            )
            for index, target in enumerate(crystal.targets)
        )
        assignments.append(FragmentAssignment(crystal, fragment, transfers))

    return FragmentScreenPlan(
        library, tuple(assignments), volume_per_crystal_nl, assignment_order
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

