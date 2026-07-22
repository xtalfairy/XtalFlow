from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
from uuid import uuid4

from .crystal_workflow import CrystalTarget, SelectedCrystal


_WELL_PATTERN = re.compile(r"^[A-H]\d{2}[a-z]$")


@dataclass(frozen=True)
class SoakingPosition:
    """One liquid-dispensing position inside a selected image/drop."""

    id: str
    selected_well_id: str
    source_target_id: str
    position_order: int
    x_mm: Decimal
    y_mm: Decimal
    selected_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.selected_well_id or not self.source_target_id:
            raise ValueError("soaking position identity must not be empty")
        if self.position_order < 1:
            raise ValueError("soaking position order must be positive")
        if self.selected_at.tzinfo is None:
            raise ValueError("soaking position time must include a timezone")


@dataclass(frozen=True)
class SelectedWell:
    """One selected plate well/image, containing zero or more dispense positions."""

    id: str
    crystal_selection_id: str
    image_key: str
    plate_code: str
    well_address: str
    selection_order: int
    selected_at: datetime
    soaking_positions: tuple[SoakingPosition, ...]
    image_set_id: str | None = None
    image_path: str = ""
    batch_id: int | None = None
    profile: str = ""
    plate_format_id: str = ""
    plate_format_version: int = 1

    def __post_init__(self) -> None:
        required = (self.id, self.crystal_selection_id, self.image_key,
                    self.plate_code, self.well_address)
        if any(not value for value in required):
            raise ValueError("selected well identity must not be empty")
        if _WELL_PATTERN.fullmatch(self.well_address) is None:
            raise ValueError(f"invalid selected well address: {self.well_address}")
        if self.selection_order < 1:
            raise ValueError("selected well order must be positive")
        if self.selected_at.tzinfo is None:
            raise ValueError("selected well time must include a timezone")
        if self.plate_format_version < 1:
            raise ValueError("plate format version must be positive")
        if any(
            position.selected_well_id != self.id
            for position in self.soaking_positions
        ):
            raise ValueError("all soaking positions must belong to the selected well")
        position_orders = [
            position.position_order for position in self.soaking_positions
        ]
        if len(position_orders) != len(set(position_orders)):
            raise ValueError("soaking position order must be unique within a well")


@dataclass(frozen=True)
class CrystalSelection:
    """The immutable selected-well snapshot owned by one experiment project."""

    id: str
    project_id: str
    wells: tuple[SelectedWell, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.project_id:
            raise ValueError("crystal selection identity must not be empty")
        if not self.wells:
            raise ValueError("crystal selection must contain at least one well")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("crystal selection times must include a timezone")
        if any(well.crystal_selection_id != self.id for well in self.wells):
            raise ValueError("all selected wells must belong to the crystal selection")
        image_keys = [well.image_key for well in self.wells]
        if len(image_keys) != len(set(image_keys)):
            raise ValueError("an image/drop may appear only once in a crystal selection")
        selection_orders = [well.selection_order for well in self.wells]
        if len(selection_orders) != len(set(selection_orders)):
            raise ValueError("selected well order must be unique within a selection")


def crystal_selection_from_selected_crystals(
    project_id: str,
    crystals: tuple[SelectedCrystal, ...],
    *,
    selection_id: str | None = None,
    created_at: datetime | None = None,
) -> CrystalSelection:
    """Bridge the current click workflow to the new selected-well model."""
    if not crystals:
        raise ValueError("at least one selected well is required")
    resolved_selection_id = selection_id or str(uuid4())
    ordered_crystals = tuple(sorted(crystals, key=lambda item: item.selected_at))
    wells: list[SelectedWell] = []
    for well_order, crystal in enumerate(ordered_crystals, start=1):
        well_id = str(uuid4())
        ordered_targets = tuple(
            sorted(crystal.targets, key=lambda target: target.selected_at)
        )
        positions = tuple(
            SoakingPosition(
                str(uuid4()), well_id, target.target_id, position_order,
                target.x_mm, target.y_mm, target.selected_at,
            )
            for position_order, target in enumerate(ordered_targets, start=1)
        )
        wells.append(
            SelectedWell(
                well_id,
                resolved_selection_id,
                crystal.image_key,
                crystal.destination_plate,
                crystal.destination_well,
                well_order,
                crystal.selected_at,
                positions,
                image_path=crystal.image_path,
                plate_format_id=crystal.plate_format_id,
            )
        )
    timestamp = created_at or min(well.selected_at for well in wells)
    return CrystalSelection(
        resolved_selection_id, project_id, tuple(wells), timestamp, timestamp
    )


def selected_crystals_from_crystal_selection(
    selection: CrystalSelection,
) -> tuple[SelectedCrystal, ...]:
    """Adapt an owned snapshot back to plan engines during the transition."""
    return tuple(
        SelectedCrystal(
            well.image_key,
            well.plate_code,
            well.well_address,
            tuple(
                CrystalTarget(
                    position.source_target_id,
                    position.x_mm,
                    position.y_mm,
                    position.selected_at,
                )
                for position in well.soaking_positions
            ),
            well.plate_format_id,
            well.image_path,
        )
        for well in sorted(selection.wells, key=lambda item: item.selection_order)
    )
