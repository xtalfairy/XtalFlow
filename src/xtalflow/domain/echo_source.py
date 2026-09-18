"""ECHO source plates: how much each source well must hold, and how many wells.

A source well cannot be drawn below its dead volume, nor filled above its
maximum. Transfers are therefore spread over as many wells as needed, and each
well is filled with its dead volume plus what it gives, plus a margin.

Working ranges, whose lower end is the dead volume, per plate and calibration:
- HKU CPOS Echo650 SOP, Table 1 (calibration protocols by liquid):
  https://info.cpos.hku.hk/wp-content/uploads/2024/12/bs-spi-SOP_Backman_Echo650.pdf
- Beckman Coulter, Echo Qualified Consumables brochure: 384PP "20-65 uL",
  1536LDV "1-5.5 uL", both "may vary slightly depending on fluid class".
No 1536LDV range for aqueous or glycerol solutions was found, so 1536LDV is
offered for DMSO only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal

# Added to the volume each well gives, so pipetting losses do not reach the dead volume.
SOURCE_FILL_MARGIN = Decimal("0.10")
_FILL_STEP_UL = Decimal("0.1")
_WELL = re.compile(r"^([A-Z]{1,2})0*(\d{1,2})$")


@dataclass(frozen=True)
class SourcePlateType:
    id: str
    label: str
    part_number: str
    well_format: int
    dead_ul: Decimal
    max_ul: Decimal
    # What this calibration is for, as the SOP lists it.
    fluids: str = ""

    @property
    def capacity_nl(self) -> Decimal:
        """What one well can give while its fill, margin included, stays at most the maximum."""
        return (self.max_ul - self.dead_ul) * 1000 / (1 + SOURCE_FILL_MARGIN)


# Beckman part numbers, with the older Labcyte ones the plates were also sold under.
PP_384_PART = "001-14555 (PP-0200)"
LDV_1536_PART = "001-6969 (LP-0400)"

SOURCE_PLATE_TYPES = (
    SourcePlateType(
        "384PP_DMSO2", "384PP · DMSO", PP_384_PART, 384, Decimal("15"), Decimal("65"),
        "DMSO, 70–100 %",
    ),
    SourcePlateType(
        "384PP_AQ_GP3", "384PP · buffer, protein", PP_384_PART, 384, Decimal("15"), Decimal("65"),
        "Reagents without surfactant: small molecules in water or buffer; DNA and RNA; "
        "proteins in water, buffer or glycerol storage solution; cell culture medium",
    ),
    SourcePlateType(
        "384PP_AQ_SP2", "384PP · surfactant, protein", PP_384_PART, 384,
        Decimal("15"), Decimal("65"),
        "Buffers containing surfactant, e.g. PCR mixes, lysis buffer, serum and plasma",
    ),
    SourcePlateType(
        "384PP_AQ_CP", "384PP · crystallization reagents", PP_384_PART, 384,
        Decimal("20"), Decimal("50"),
        "Crystallization reagents and salts without surfactant: MPD, PEGs, osmotic solutions",
    ),
    SourcePlateType(
        "1536LDV_DMSO", "1536LDV · DMSO", LDV_1536_PART, 1536, Decimal("1"), Decimal("5.5"),
        "DMSO, 70–100 %",
    ),
)


def source_plate_type(type_id: str) -> SourcePlateType:
    found = next((item for item in SOURCE_PLATE_TYPES if item.id == type_id), None)
    if found is None:
        raise ValueError(f"'{type_id}' is not a known source plate type")
    return found


def _number(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _layout(well_format: int) -> tuple[int, int]:
    return (16, 24) if well_format == 384 else (32, 48)


def _row_name(index: int) -> str:
    return chr(65 + index) if index < 26 else "A" + chr(65 + index - 26)


def _row_index(name: str) -> int:
    """A–Z, then AA–AF on a 1536-well plate; anything else is off the plate."""
    if len(name) == 1:
        return ord(name) - 65
    if name[0] == "A":
        return 26 + ord(name[1]) - 65
    return 99


def normalize_well(address: str, well_format: int) -> str:
    """"a01" → "A1"; an address not on this plate is refused."""
    text = address.strip().upper()
    found = _WELL.match(text)
    rows, columns = _layout(well_format)
    if found is not None:
        row, column = _row_index(found.group(1)), int(found.group(2))
        if row < rows and 1 <= column <= columns:
            return f"{found.group(1)}{column}"
    raise ValueError(f"'{address.strip()}' is not a well on a {well_format}-well plate")


def parse_wells(text: str, well_format: int) -> tuple[str, ...]:
    """Wells typed as "A1, A2" or "A1 A2", in order and without repeats."""
    wells: list[str] = []
    for item in re.split(r"[,\s]+", text.strip()):
        if item:
            well = normalize_well(item, well_format)
            if well not in wells:
                wells.append(well)
    return tuple(wells)


def plate_order(well_format: int) -> tuple[str, ...]:
    """The order wells are used in: down each column, A1 → P1, then A2."""
    rows, columns = _layout(well_format)
    return tuple(f"{_row_name(row)}{column}" for column in range(1, columns + 1) for row in range(rows))


def suggest_wells(
    well_format: int, after: str | None, count: int, taken: Iterable[str] = ()
) -> tuple[str, ...]:
    """The next free wells in plate order after the last one entered."""
    order = plate_order(well_format)
    start = order.index(after) + 1 if after in order else 0
    unavailable = set(taken)
    return tuple(well for well in order[start:] if well not in unavailable)[:count]


USED = "used"
SKIPPED = "skipped"
SKIP_REASONS = ("Dust or debris", "Damaged", "Used outside XtalFlow", "Other")


@dataclass(frozen=True)
class PlateRecordEntry:
    """One line of a shared source plate's record: wells used by an experiment, or skipped."""

    kind: str
    wells: tuple[str, ...]
    user: str
    time: datetime
    experiment_id: str = ""
    additive: str = ""
    reason: str = ""

    def describe(self, well: str, plate: str) -> str:
        when = f"{self.time:%Y-%m-%d}"
        if self.kind == SKIPPED:
            return f"{plate} {well} was skipped by {self.user} on {when}: {self.reason or 'no reason'}"
        return (
            f"{plate} {well} was used by {self.user} on {when}"
            + (f" ({self.experiment_id})" if self.experiment_id else "")
        )


def well_history(entries: Iterable[PlateRecordEntry]) -> dict[str, PlateRecordEntry]:
    """Why each recorded well is no longer free; the first record of a well wins."""
    history: dict[str, PlateRecordEntry] = {}
    for entry in entries:
        for well in entry.wells:
            history.setdefault(well, entry)
    return history


def next_free_wells(
    well_format: int,
    entries: Iterable[PlateRecordEntry],
    count: int,
    exclude: Iterable[str] = (),
) -> tuple[str, ...]:
    """Wells after the last one used on the plate, skipping any recorded or excluded."""
    entries = tuple(entries)
    order = plate_order(well_format)
    positions = [
        order.index(well) for entry in entries if entry.kind == USED
        for well in entry.wells if well in order
    ]
    start = max(positions) + 1 if positions else 0
    unavailable = set(well_history(entries)) | set(exclude)
    return tuple(well for well in order[start:] if well not in unavailable)[:count]


@dataclass(frozen=True)
class WellFill:
    address: str | None
    transferred_nl: Decimal
    fill_ul: Decimal


@dataclass(frozen=True)
class SourceAllocation:
    plate_type: SourcePlateType
    # For each transfer, in order, the well it is drawn from (None when not entered yet).
    transfer_wells: tuple[str | None, ...]
    wells: tuple[WellFill, ...]
    entered_wells: tuple[str, ...]

    @property
    def wells_needed(self) -> int:
        return len(self.wells)

    @property
    def missing(self) -> int:
        return max(0, self.wells_needed - len(self.entered_wells))

    @property
    def unused_wells(self) -> tuple[str, ...]:
        return self.entered_wells[self.wells_needed:]

    @property
    def fill_note(self) -> str:
        """What each fill is made of, so the margin is never a surprise."""
        return (
            f"{_number(self.plate_type.dead_ul)} µL dead volume + transfers + "
            f"{_number(SOURCE_FILL_MARGIN * 100)} % margin"
        )

    def fills_text(self) -> str:
        return " · ".join(
            f"{well.address or '?'} {_number(well.fill_ul)} µL" for well in self.wells
        )

    def instruction(self) -> str:
        """How to fill the source wells, naming the dead volume and the margin."""
        margin = f"{_number(SOURCE_FILL_MARGIN * 100)} % margin"
        if not self.wells:
            return "Nothing is transferred from this source."
        if len(self.wells) == 1:
            (well,) = self.wells
            text = (
                f"Fill {well.address} with {_number(well.fill_ul)} µL — "
                f"{_number(self.plate_type.dead_ul)} µL dead volume + "
                f"{_ul_text(well.transferred_nl)} µL to transfer + {margin}."
            )
        else:
            text = f"Needs {len(self.wells)} wells: {self.fills_text()} ({self.fill_note} each)."
        unused = self.unused_wells
        if unused:
            names = unused[0] if len(unused) == 1 else f"{', '.join(unused[:-1])} and {unused[-1]}"
            text += f" {names} {'is' if len(unused) == 1 else 'are'} not needed."
        return text


def _ul_text(nl: Decimal) -> str:
    return _number((nl / 1000).quantize(Decimal("0.01"), ROUND_CEILING))


def allocate_source_wells(
    plate_type: SourcePlateType, transfers_nl: Sequence[Decimal], wells: Sequence[str]
) -> SourceAllocation:
    """Draw transfers in order from the first well until the next would take it too low."""
    capacity = plate_type.capacity_nl
    indices: list[int] = []
    totals: list[Decimal] = []
    for volume in transfers_nl:
        if volume > capacity:
            raise ValueError(
                f"one transfer of {_number(volume)} nL is more than a {plate_type.label} "
                f"well can give ({_number(capacity.quantize(Decimal('0.1')))} nL)"
            )
        if not totals or totals[-1] + volume > capacity:
            totals.append(Decimal("0"))
        totals[-1] += volume
        indices.append(len(totals) - 1)
    fills = []
    for index, transferred in enumerate(totals):
        fill = plate_type.dead_ul + transferred / 1000 * (1 + SOURCE_FILL_MARGIN)
        fill = min(
            plate_type.max_ul, (fill / _FILL_STEP_UL).to_integral_value(ROUND_CEILING) * _FILL_STEP_UL
        )
        address = wells[index] if index < len(wells) else None
        fills.append(WellFill(address, transferred, fill))
    return SourceAllocation(
        plate_type,
        tuple(wells[index] if index < len(wells) else None for index in indices),
        tuple(fills),
        tuple(wells),
    )
