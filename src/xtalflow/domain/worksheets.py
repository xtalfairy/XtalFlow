from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .fragment_screening import FragmentScreenPlan
from .crystal_workflow import SelectedCrystal
from .raw_crystal import RawCrystalPlan
from .plate_format import plate_format_by_id


ECHO_HEADER = (
    "Source plate name",
    "Source well",
    "Transfer Volume",
    "Destination plate name",
    "Targeting Well (for View)",
    "Destination Well",
    "Destination Well X offset",
    "Destination Well Y offset",
)

SHIFTER_HEADER = (
    ";PlateType",
    "PlateID",
    "LocationShifter",
    "PlateRow",
    "PlateColumn",
    "PositionSubWell",
    "Comment",
    "CrystalID",
    "TimeArrival",
    "TimeDeparture",
    "PickDuration",
    "DestinationName",
    "DestinationLocation",
    "Barcode",
    "ExternalComment",
)


@dataclass(frozen=True, slots=True)
class EchoWorksheetRow:
    source_plate: str
    source_well: str
    transfer_volume_nl: Decimal
    destination_plate: str
    targeting_well: str
    destination_well: str
    x_offset_um: Decimal
    y_offset_um: Decimal

    def values(self) -> tuple[str, ...]:
        return (
            self.source_plate,
            self.source_well,
            str(self.transfer_volume_nl),
            self.destination_plate,
            self.targeting_well,
            self.destination_well,
            str(self.x_offset_um),
            str(self.y_offset_um),
        )


@dataclass(frozen=True, slots=True)
class ShifterWorksheetRow:
    plate_type: str
    plate_id: str
    plate_row: str
    plate_column: str
    subwell: str

    def values(self) -> tuple[str, ...]:
        return (
            self.plate_type,
            self.plate_id,
            "AM",
            self.plate_row,
            self.plate_column,
            self.subwell,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        )


def build_echo_worksheet(plan: FragmentScreenPlan) -> tuple[EchoWorksheetRow, ...]:
    rows: list[EchoWorksheetRow] = []
    for assignment in plan.assignments:
        crystal = assignment.crystal
        plate_format = plate_format_by_id(crystal.plate_format_id)
        if plate_format is None:
            raise ValueError(f"unsupported plate format for {crystal.image_key}")
        destination_well = plate_format.echo_destination_well(
            crystal.destination_well
        )
        for transfer in assignment.transfers:
            x_um, y_um = plate_format.echo_offset_um(
                crystal.destination_well,
                float(transfer.target.x_mm),
                float(transfer.target.y_mm),
            )
            rows.append(
                EchoWorksheetRow(
                    assignment.fragment.source_plate,
                    assignment.fragment.source_well,
                    transfer.volume_nl,
                    crystal.destination_plate,
                    crystal.destination_well,
                    destination_well,
                    Decimal(str(round(x_um, 6))),
                    Decimal(str(round(y_um, 6))),
                )
            )
    return tuple(rows)


def build_shifter_worksheet(
    plan: FragmentScreenPlan | RawCrystalPlan,
) -> tuple[ShifterWorksheetRow, ...]:
    if isinstance(plan, RawCrystalPlan):
        crystals = tuple(selection.crystal for selection in plan.selections)
    else:
        crystals = tuple(assignment.crystal for assignment in plan.assignments)
    return build_shifter_worksheet_for_crystals(crystals)


def build_shifter_worksheet_for_crystals(
    crystals: tuple[SelectedCrystal, ...],
) -> tuple[ShifterWorksheetRow, ...]:
    rows: list[ShifterWorksheetRow] = []
    for crystal in crystals:
        plate_format = plate_format_by_id(crystal.plate_format_id)
        if plate_format is None:
            raise ValueError(f"unsupported plate format for {crystal.image_key}")
        well = crystal.destination_well
        rows.append(
            ShifterWorksheetRow(
                plate_format.instrument_name or plate_format.id,
                crystal.destination_plate,
                well[0],
                str(int(well[1:3])),
                well[3],
            )
        )
    return tuple(rows)
