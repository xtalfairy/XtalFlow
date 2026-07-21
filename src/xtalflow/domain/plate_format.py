from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WellAddress:
    row: str
    column: int
    lens: str

    def __post_init__(self) -> None:
        if self.row not in "ABCDEFGH" or not 1 <= self.column <= 12:
            raise ValueError("well address must be inside an 8 x 12 plate")
        if len(self.lens) != 1 or not self.lens.islower():
            raise ValueError("lens suffix must be one lowercase letter")

    def __str__(self) -> str:
        return f"{self.row}{self.column:02d}{self.lens}"


@dataclass(frozen=True)
class LensDefinition:
    drop_number: int
    suffix: str
    physical_diameter_mm: float
    echo_x_correction_um: int = 0
    echo_y_correction_um: int = 0
    destination_row_offset: int = 0
    destination_column_offset: int = 0


@dataclass(frozen=True)
class PlateFormat:
    id: str
    version: int
    display_name: str
    lenses: tuple[LensDefinition, ...]
    rows: int = 8
    columns: int = 12
    instrument_name: str = ""

    def address_for(self, well_number: int, drop_number: int) -> WellAddress:
        if not 1 <= well_number <= self.rows * self.columns:
            raise ValueError(
                f"well number must be between 1 and {self.rows * self.columns}"
            )
        lens = next(
            (item for item in self.lenses if item.drop_number == drop_number), None
        )
        if lens is None:
            raise ValueError(
                f"drop d{drop_number} is not valid for {self.display_name}"
            )
        index = well_number - 1
        return WellAddress(
            chr(ord("A") + index // self.columns),
            index % self.columns + 1,
            lens.suffix,
        )

    def lens_for(self, drop_number: int) -> LensDefinition:
        lens = next(
            (item for item in self.lenses if item.drop_number == drop_number), None
        )
        if lens is None:
            raise ValueError(f"drop d{drop_number} is not valid for {self.display_name}")
        return lens

    def echo_offset_um(
        self, well_address: WellAddress | str, x_mm: float, y_mm: float
    ) -> tuple[float, float]:
        suffix = (
            well_address.lens
            if isinstance(well_address, WellAddress)
            else well_address[-1:]
        )
        lens = next((item for item in self.lenses if item.suffix == suffix), None)
        if lens is None:
            raise ValueError(
                f"subwell {suffix!r} is not valid for {self.display_name}"
            )
        return (
            x_mm * 1000 + lens.echo_x_correction_um,
            y_mm * 1000 + lens.echo_y_correction_um,
        )

    def echo_destination_well(self, well_address: WellAddress | str) -> str:
        if isinstance(well_address, str):
            if len(well_address) != 4:
                raise ValueError(f"invalid well address: {well_address}")
            address = WellAddress(
                well_address[0], int(well_address[1:3]), well_address[3]
            )
        else:
            address = well_address
        lens = next(
            (item for item in self.lenses if item.suffix == address.lens), None
        )
        if lens is None:
            raise ValueError(
                f"subwell {address.lens!r} is not valid for {self.display_name}"
            )
        destination_row = (
            (ord(address.row) - ord("A")) * 2 + lens.destination_row_offset
        )
        destination_column = (
            (address.column - 1) * 2 + 1 + lens.destination_column_offset
        )
        if not 0 <= destination_row < 16 or not 1 <= destination_column <= 24:
            raise ValueError("mapped destination is outside a 384-well plate")
        return f"{chr(ord('A') + destination_row)}{destination_column:02d}"

SWISSCI_MIDI_3_LENS = PlateFormat(
    "swissci-midi-3-lens-hr3-194",
    1,
    "Swissci Midi 3 Lens (HR3-194)",
    (
        LensDefinition(1, "a", 2.77),
        LensDefinition(2, "c", 2.77, destination_row_offset=1),
        # The physical c-to-d pitch is 3.8 mm, while the mapped 384-well
        # destination pitch is 4.5 mm.  The d lens is therefore 0.7 mm left
        # of its mapped ECHO destination-well centre.
        LensDefinition(
            3,
            "d",
            2.77,
            echo_x_correction_um=-700,
            destination_row_offset=1,
            destination_column_offset=1,
        ),
    ),
    instrument_name="SwissCI-MRC-3d",
)

SWISSCI_MRC_2_WELL = PlateFormat(
    "swissci-mrc-2-well-3-082-083",
    1,
    "Swissci MRC 2 Well (3-082/083)",
    (
        LensDefinition(1, "a", 2.8, destination_column_offset=1),
        LensDefinition(
            2,
            "b",
            2.8,
            destination_row_offset=1,
            destination_column_offset=1,
        ),
    ),
    instrument_name="SwissCI-MRC-2d",
)

PLATE_FORMATS = (SWISSCI_MIDI_3_LENS, SWISSCI_MRC_2_WELL)


def plate_format_by_id(format_id: str | None) -> PlateFormat | None:
    if format_id is None:
        return None
    return next((item for item in PLATE_FORMATS if item.id == format_id), None)
