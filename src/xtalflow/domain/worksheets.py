from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .crystal_selection import SelectedWell
from .fragment_screening import FragmentScreenPlan
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
        selected_well = assignment.selected_well
        plate_format = plate_format_by_id(selected_well.plate_format_id)
        if plate_format is None:
            raise ValueError(
                f"unsupported plate format for {selected_well.image_key}"
            )
        destination_well = plate_format.echo_destination_well(
            selected_well.well_address
        )
        for transfer in assignment.transfers:
            x_um, y_um = plate_format.echo_offset_um(
                selected_well.well_address,
                float(transfer.position.x_mm),
                float(transfer.position.y_mm),
            )
            rows.append(
                EchoWorksheetRow(
                    assignment.fragment.source_plate,
                    assignment.fragment.source_well,
                    transfer.volume_nl,
                    selected_well.plate_code,
                    selected_well.well_address,
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
        selected_wells = plan.selected_wells
    else:
        selected_wells = tuple(
            assignment.selected_well for assignment in plan.assignments
        )
    return build_shifter_worksheet_for_selected_wells(selected_wells)


def build_shifter_worksheet_for_selected_wells(
    selected_wells: tuple[SelectedWell, ...],
) -> tuple[ShifterWorksheetRow, ...]:
    rows: list[ShifterWorksheetRow] = []
    for selected_well in selected_wells:
        plate_format = plate_format_by_id(selected_well.plate_format_id)
        if plate_format is None:
            raise ValueError(
                f"unsupported plate format for {selected_well.image_key}"
            )
        well = selected_well.well_address
        rows.append(
            ShifterWorksheetRow(
                plate_format.instrument_name or plate_format.id,
                selected_well.plate_code,
                well[0],
                str(int(well[1:3])),
                well[3],
            )
        )
    return tuple(rows)
