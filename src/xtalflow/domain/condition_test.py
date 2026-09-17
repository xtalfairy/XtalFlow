"""Condition tests: how crystals tolerate additives by final concentration and time.

A test is written like a lab-notebook table. Each series (one tab) varies one
additive's final concentration against soak time, optionally after fixed
earlier treatments. Every included cell is a condition with stable identity,
repeated on as many crystals as its replicate count.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from .crystal_selection import CrystalSelection, SelectedWell, order_selected_wells
from .crystal_workflow import AssignmentOrder
from .fragment_screening import TRANSFER_INCREMENT_NL

HUNDRED = Decimal("100")


def format_minutes(minutes: int, compact: bool = False) -> str:
    hours, rest = divmod(minutes, 60)
    space = "" if compact else " "
    if hours and rest:
        return f"{hours}{space}h{space}{rest}{space}min"
    if hours:
        return f"{hours}{space}h"
    return f"{minutes}{space}min"


def parse_minutes(text: str) -> int:
    """Soak times as a researcher writes them: "30", "30 min", "1h", "1 h 30 min"."""
    value = text.strip().lower().replace(" ", "")
    if not value:
        raise ValueError("enter a soak time")
    hours = minutes = 0
    if "h" in value:
        head, _, value = value.partition("h")
        hours = int(head) if head else 0
    value = value.removesuffix("min").removesuffix("m")
    if value:
        minutes = int(value)
    total = hours * 60 + minutes
    if total < 0:
        raise ValueError("soak time cannot be negative")
    return total


def _percent_text(value: Decimal) -> str:
    return f"{value.normalize():f}"


@dataclass(frozen=True)
class Additive:
    id: str
    name: str
    stock_percent: Decimal = HUNDRED
    smiles: str = ""
    source_plate: str = ""
    source_well: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("additive name must not be empty")
        if not 0 < self.stock_percent <= HUNDRED:
            raise ValueError(f"{self.name} stock must be above 0% and at most 100%")


@dataclass(frozen=True)
class Treatment:
    """Bring one additive to a final concentration, then wait before the next step."""

    additive_id: str
    final_percent: Decimal
    minutes: int

    def __post_init__(self) -> None:
        if self.final_percent < 0:
            raise ValueError("final concentration cannot be negative")
        if self.minutes < 0:
            raise ValueError("soak time cannot be negative")


@dataclass(frozen=True)
class MatrixCell:
    """One cell of a series table. The id stays with the cell so results can attach later."""

    id: str
    percent: Decimal
    minutes: int
    included: bool = True
    replicates: int = 2
    note: str = ""

    def __post_init__(self) -> None:
        if self.replicates < 1:
            raise ValueError("replicates must be at least 1")


@dataclass(frozen=True)
class Condition:
    id: str
    series_id: str
    treatments: tuple[Treatment, ...]
    replicates: int
    note: str = ""

    @property
    def total_minutes(self) -> int:
        return sum(treatment.minutes for treatment in self.treatments)


@dataclass(frozen=True)
class ConditionSeries:
    id: str
    additive_id: str
    percents: tuple[Decimal, ...]
    times: tuple[int, ...]
    cells: tuple[MatrixCell, ...]
    # Fixed treatments applied, in order, before this series' additive.
    before: tuple[Treatment, ...] = ()

    def cell(self, percent: Decimal, minutes: int) -> MatrixCell | None:
        return next(
            (item for item in self.cells if item.percent == percent and item.minutes == minutes),
            None,
        )

    def with_axes(
        self,
        percents: tuple[Decimal, ...],
        times: tuple[int, ...],
        default_replicates: int = 2,
    ) -> ConditionSeries:
        """Change the table's columns or rows, keeping every existing cell's identity."""
        percents = tuple(sorted(set(percents)))
        times = tuple(sorted(set(times)))
        cells = tuple(
            self.cell(percent, minutes)
            or MatrixCell(str(uuid4()), percent, minutes, True, default_replicates)
            for minutes in times
            for percent in percents
        )
        return replace(self, percents=percents, times=times, cells=cells)

    def with_cell(self, cell: MatrixCell) -> ConditionSeries:
        return replace(
            self, cells=tuple(cell if item.id == cell.id else item for item in self.cells)
        )

    def conditions(self) -> tuple[Condition, ...]:
        return tuple(
            Condition(
                cell.id,
                self.id,
                (*self.before, Treatment(self.additive_id, cell.percent, cell.minutes)),
                cell.replicates,
                cell.note,
            )
            for minutes in self.times
            for percent in self.percents
            if (cell := self.cell(percent, minutes)) is not None and cell.included
        )


@dataclass(frozen=True)
class ConditionTestDesign:
    additives: tuple[Additive, ...]
    series: tuple[ConditionSeries, ...]
    drop_volume_nl: Decimal | None = None
    purpose: str = ""
    # Crystals the researcher can spend; None when they have not said.
    available_crystals: int | None = None

    def additive(self, additive_id: str) -> Additive:
        found = next((item for item in self.additives if item.id == additive_id), None)
        if found is None:
            raise ValueError("a treatment refers to an additive that was removed")
        return found

    def conditions(self) -> tuple[Condition, ...]:
        return tuple(condition for series in self.series for condition in series.conditions())

    @property
    def required_crystals(self) -> int:
        return sum(condition.replicates for condition in self.conditions())

    def label(self, condition: Condition, compact: bool = False) -> str:
        """"DMSO 10% · 1 h", or steps joined by → for combined treatments."""
        parts = []
        for treatment in condition.treatments:
            time = format_minutes(treatment.minutes, compact)
            if treatment.final_percent == 0:
                name = "Control"
                parts.append(f"{name}-{time}" if compact else f"{name} · {time}")
                continue
            name = self.additive(treatment.additive_id).name
            percent = _percent_text(treatment.final_percent)
            parts.append(
                f"{name}{percent}%-{time}" if compact else f"{name} {percent}% · {time}"
            )
        return ("+" if compact else " → ").join(parts)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": 1,
                "drop_volume_nl": (
                    str(self.drop_volume_nl) if self.drop_volume_nl is not None else None
                ),
                "purpose": self.purpose,
                "available_crystals": self.available_crystals,
                "additives": [
                    {
                        "id": item.id, "name": item.name,
                        "stock_percent": str(item.stock_percent), "smiles": item.smiles,
                        "source_plate": item.source_plate, "source_well": item.source_well,
                    }
                    for item in self.additives
                ],
                "series": [
                    {
                        "id": series.id,
                        "additive_id": series.additive_id,
                        "percents": [str(value) for value in series.percents],
                        "times": list(series.times),
                        "before": [_treatment_payload(item) for item in series.before],
                        "cells": [
                            {
                                "id": cell.id, "percent": str(cell.percent),
                                "minutes": cell.minutes, "included": cell.included,
                                "replicates": cell.replicates, "note": cell.note,
                            }
                            for cell in series.cells
                        ],
                    }
                    for series in self.series
                ],
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> ConditionTestDesign:
        data = json.loads(text)
        drop = data.get("drop_volume_nl")
        return cls(
            tuple(
                Additive(
                    item["id"], item["name"], Decimal(item["stock_percent"]),
                    item.get("smiles", ""), item.get("source_plate", ""),
                    item.get("source_well", ""),
                )
                for item in data["additives"]
            ),
            tuple(
                ConditionSeries(
                    series["id"],
                    series["additive_id"],
                    tuple(Decimal(value) for value in series["percents"]),
                    tuple(int(value) for value in series["times"]),
                    tuple(
                        MatrixCell(
                            cell["id"], Decimal(cell["percent"]), int(cell["minutes"]),
                            bool(cell["included"]), int(cell["replicates"]),
                            cell.get("note", ""),
                        )
                        for cell in series["cells"]
                    ),
                    tuple(_treatment_from_payload(item) for item in series.get("before", ())),
                )
                for series in data["series"]
            ),
            Decimal(drop) if drop is not None else None,
            data.get("purpose", ""),
            data.get("available_crystals"),
        )


def _treatment_payload(treatment: Treatment) -> dict:
    return {
        "additive_id": treatment.additive_id,
        "final_percent": str(treatment.final_percent),
        "minutes": treatment.minutes,
    }


def _treatment_from_payload(item: dict) -> Treatment:
    return Treatment(item["additive_id"], Decimal(item["final_percent"]), int(item["minutes"]))


def new_series(
    additive_id: str,
    percents: tuple[Decimal, ...],
    times: tuple[int, ...],
    replicates: int = 2,
    before: tuple[Treatment, ...] = (),
) -> ConditionSeries:
    return ConditionSeries(str(uuid4()), additive_id, (), (), (), before).with_axes(
        percents, times, replicates
    )


def solvent_test_template() -> ConditionTestDesign:
    dmso = Additive(str(uuid4()), "DMSO", HUNDRED, "CS(=O)C")
    return ConditionTestDesign(
        (dmso,),
        (new_series(dmso.id, tuple(Decimal(v) for v in ("0", "5", "10", "20")), (0, 30, 60, 120)),),
    )


def cryo_test_template() -> ConditionTestDesign:
    glycerol = Additive(str(uuid4()), "Glycerol", HUNDRED, "OCC(O)CO")
    return ConditionTestDesign(
        (glycerol,),
        (new_series(glycerol.id, tuple(Decimal(v) for v in ("0", "10", "20", "25")), (0,)),),
    )


TEMPLATES = {"solvent": solvent_test_template, "cryo": cryo_test_template}


@dataclass(frozen=True)
class Dose:
    """One addition to a well: total volume, the concentration it reaches, and when."""

    additive_id: str
    volume_nl: Decimal
    actual_percent: Decimal
    start_minutes: int


def compute_doses(design: ConditionTestDesign, condition: Condition) -> tuple[Dose, ...]:
    """Volumes that bring each additive to its final concentration in the drop.

    For an additive at stock S added to a drop of volume V already holding a nL
    of pure additive, reaching c needs Va = (c·V − 100·a) / (S − c). Volumes are
    rounded to the dispenser's 2.5 nL step and the concentration actually
    reached is reported. Earlier additions enlarge the drop for later ones.
    """
    if design.drop_volume_nl is None or design.drop_volume_nl <= 0:
        raise ValueError("Enter the drop volume in Setup.")
    volume = design.drop_volume_nl
    pure: dict[str, Decimal] = {}
    start = 0
    doses: list[Dose] = []
    for treatment in condition.treatments:
        if treatment.final_percent > 0:
            additive = design.additive(treatment.additive_id)
            target = treatment.final_percent
            already = pure.get(additive.id, Decimal("0"))
            label = f"{additive.name} {_percent_text(target)}%"
            if target >= additive.stock_percent:
                raise ValueError(
                    f"{label} is not below its {_percent_text(additive.stock_percent)}% stock."
                )
            exact = (target * volume - HUNDRED * already) / (additive.stock_percent - target)
            if exact <= 0:
                raise ValueError(f"{label}: the drop is already at or above that concentration.")
            steps = (exact / TRANSFER_INCREMENT_NL).quantize(Decimal("1"), ROUND_HALF_UP)
            if steps < 1:
                smallest = HUNDRED * additive.stock_percent * TRANSFER_INCREMENT_NL / HUNDRED / (
                    volume + TRANSFER_INCREMENT_NL
                )
                raise ValueError(
                    f"{label} needs less than {TRANSFER_INCREMENT_NL} nL; the lowest "
                    f"possible is about {smallest.quantize(Decimal('0.1'))}%."
                )
            dispensed = TRANSFER_INCREMENT_NL * steps
            pure[additive.id] = already + additive.stock_percent * dispensed / HUNDRED
            volume += dispensed
            actual = (HUNDRED * pure[additive.id] / volume).quantize(Decimal("0.1"))
            doses.append(Dose(additive.id, dispensed, actual, start))
        start += treatment.minutes
    return tuple(doses)


@dataclass(frozen=True)
class ConditionAssignment:
    selected_well: SelectedWell
    condition: Condition
    replicate: int
    doses: tuple[Dose, ...]

    @property
    def harvest_minutes(self) -> int:
        return self.condition.total_minutes

    @property
    def total_volume_nl(self) -> Decimal:
        return sum((dose.volume_nl for dose in self.doses), Decimal("0"))


@dataclass(frozen=True)
class ConditionTestPlan:
    selection: CrystalSelection
    design: ConditionTestDesign
    assignments: tuple[ConditionAssignment, ...]
    assignment_order: AssignmentOrder
    unused_wells: tuple[SelectedWell, ...] = field(default=())

    @property
    def dispense_rounds(self) -> tuple[tuple[int, tuple[tuple[ConditionAssignment, Dose], ...]], ...]:
        """Additions grouped by the minute they are dispensed, earliest first."""
        rounds: dict[int, list[tuple[ConditionAssignment, Dose]]] = {}
        for assignment in self.assignments:
            for dose in assignment.doses:
                rounds.setdefault(dose.start_minutes, []).append((assignment, dose))
        return tuple((minute, tuple(rounds[minute])) for minute in sorted(rounds))

    @property
    def harvest_groups(self) -> tuple[tuple[int, tuple[ConditionAssignment, ...]], ...]:
        groups: dict[int, list[ConditionAssignment]] = {}
        for assignment in self.assignments:
            groups.setdefault(assignment.harvest_minutes, []).append(assignment)
        return tuple((minute, tuple(groups[minute])) for minute in sorted(groups))

    def additive_totals(self) -> tuple[tuple[Additive, Decimal], ...]:
        """How much of each additive the source wells must hold, before dead volume."""
        totals: dict[str, Decimal] = {}
        for assignment in self.assignments:
            for dose in assignment.doses:
                totals[dose.additive_id] = totals.get(dose.additive_id, Decimal("0")) + dose.volume_nl
        return tuple(
            (additive, totals[additive.id])
            for additive in self.design.additives if additive.id in totals
        )


def build_condition_test_plan(
    design: ConditionTestDesign,
    selection: CrystalSelection,
    assignment_order: AssignmentOrder = AssignmentOrder.SELECTION,
) -> ConditionTestPlan:
    """Give each replicate of each condition its own well, keeping a condition's wells together."""
    conditions = design.conditions()
    if not conditions:
        raise ValueError("Include at least one condition.")
    doses_by_condition = {condition.id: compute_doses(design, condition) for condition in conditions}
    for condition in conditions:
        for dose in doses_by_condition[condition.id]:
            additive = design.additive(dose.additive_id)
            if not additive.source_plate.strip() or not additive.source_well.strip():
                raise ValueError(f"Enter the source plate and well for {additive.name}.")
    slots = [
        (condition, replicate)
        for condition in conditions
        for replicate in range(1, condition.replicates + 1)
    ]
    wells = order_selected_wells(selection, assignment_order)
    if len(wells) < len(slots):
        raise ValueError(
            f"{len(slots)} crystals are needed; {len(wells)} "
            f"well{'s are' if len(wells) != 1 else ' is'} selected."
        )
    assignments = []
    for (condition, replicate), well in zip(slots, wells):
        count = len(well.soaking_positions)
        if not count:
            raise ValueError(f"{well.well_address} needs at least one soaking position.")
        for dose in doses_by_condition[condition.id]:
            if (dose.volume_nl / TRANSFER_INCREMENT_NL) % count:
                raise ValueError(
                    f"{design.label(condition)}: {dose.volume_nl.normalize():f} nL cannot be "
                    f"split equally between {count} positions in {well.well_address}. "
                    "Use one position per well for this condition."
                )
        assignments.append(
            ConditionAssignment(well, condition, replicate, doses_by_condition[condition.id])
        )
    return ConditionTestPlan(
        selection, design, tuple(assignments), assignment_order, tuple(wells[len(slots):])
    )
