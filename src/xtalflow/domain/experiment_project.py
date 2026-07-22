from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .crystal_selection import CrystalSelection


class PlanType(str, Enum):
    RAW_CRYSTAL = "raw_crystal"
    FRAGMENT_SCREENING = "fragment_screening"


@dataclass(frozen=True)
class ExperimentPlan:
    id: str
    project_id: str
    plan_type: PlanType
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.project_id:
            raise ValueError("experiment plan identity must not be empty")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("experiment plan times must include a timezone")


@dataclass(frozen=True)
class ExperimentProject:
    """One selected-well group combined with exactly one experiment plan."""

    id: str
    name: str
    crystal_selection: CrystalSelection
    plan: ExperimentPlan
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.name.strip():
            raise ValueError("experiment project identity must not be empty")
        if self.crystal_selection.project_id != self.id:
            raise ValueError("crystal selection must belong to the project")
        if self.plan.project_id != self.id:
            raise ValueError("experiment plan must belong to the project")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("experiment project times must include a timezone")


@dataclass(frozen=True)
class SelectedWellUsage:
    project_id: str
    project_name: str
    plan_type: PlanType
    status: str
