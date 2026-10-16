from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class AssignmentOrder(str, Enum):
    SELECTION = "selection"
    PLATE_WELL = "plate_well"


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class SelectedCrystal:
    image_key: str
    destination_plate: str
    destination_well: str
    targets: tuple[CrystalTarget, ...]
    plate_format_id: str = ""
    image_path: str = ""
    plate_format_version: int = 1
    image_set_id: str | None = None
    batch_id: int | None = None
    profile: str = ""

    def __post_init__(self) -> None:
        if not self.image_key or not self.destination_plate or not self.destination_well:
            raise ValueError("crystal destination identity must not be empty")
        if not self.targets:
            raise ValueError("a selected crystal must contain at least one target")
        if self.plate_format_version < 1:
            raise ValueError("plate format version must be positive")

    @property
    def selected_at(self) -> datetime:
        return min(target.selected_at for target in self.targets)

