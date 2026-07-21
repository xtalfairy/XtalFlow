from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re


_WELL_PATTERN = re.compile(r"^([A-H])(\d{2})([a-z])$")


class AssignmentOrder(str, Enum):
    SELECTION = "selection"
    PLATE_WELL = "plate_well"


@dataclass(frozen=True, slots=True)
class CrystalTarget:
    target_id: str
    x_mm: Decimal
    y_mm: Decimal
    selected_at: datetime

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("target id must not be empty")
        if self.selected_at.tzinfo is None:
            raise ValueError("target selection time must include a timezone")


@dataclass(frozen=True, slots=True)
class SelectedCrystal:
    image_key: str
    destination_plate: str
    destination_well: str
    targets: tuple[CrystalTarget, ...]
    plate_format_id: str = ""

    def __post_init__(self) -> None:
        if not self.image_key or not self.destination_plate or not self.destination_well:
            raise ValueError("crystal destination identity must not be empty")
        if not self.targets:
            raise ValueError("a selected crystal must contain at least one target")

    @property
    def selected_at(self) -> datetime:
        return min(target.selected_at for target in self.targets)


def order_crystals(
    crystals: tuple[SelectedCrystal, ...], assignment_order: AssignmentOrder
) -> tuple[SelectedCrystal, ...]:
    if assignment_order is AssignmentOrder.SELECTION:
        return tuple(sorted(crystals, key=lambda crystal: crystal.selected_at))
    if assignment_order is AssignmentOrder.PLATE_WELL:
        return tuple(sorted(crystals, key=_plate_well_sort_key))
    raise ValueError(f"unsupported assignment order: {assignment_order}")


def _plate_well_sort_key(crystal: SelectedCrystal) -> tuple[object, ...]:
    plate = crystal.destination_plate
    plate_key: tuple[int, object] = (0, int(plate)) if plate.isdigit() else (1, plate)
    match = _WELL_PATTERN.fullmatch(crystal.destination_well)
    if match is None:
        raise ValueError(f"invalid destination well: {crystal.destination_well}")
    row, column, suffix = match.groups()
    return (*plate_key, ord(row) - ord("A"), int(column), suffix)

