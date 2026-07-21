from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class LensDefinition:
    drop_number: int
    suffix: str
    physical_diameter_mm: float


@dataclass(frozen=True, slots=True)
class PlateFormat:
    id: str
    version: int
    display_name: str
    lenses: tuple[LensDefinition, ...]
    rows: int = 8
    columns: int = 12

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

SWISSCI_MIDI_3_LENS = PlateFormat(
    "swissci-midi-3-lens-hr3-194",
    1,
    "Swissci Midi 3 Lens (HR3-194)",
    (
        LensDefinition(1, "a", 2.77),
        LensDefinition(2, "c", 2.77),
        LensDefinition(3, "d", 2.77),
    ),
)

SWISSCI_MRC_2_WELL = PlateFormat(
    "swissci-mrc-2-well-3-082-083",
    1,
    "Swissci MRC 2 Well (3-082/083)",
    (LensDefinition(1, "a", 2.8), LensDefinition(2, "b", 2.8)),
)

PLATE_FORMATS = (SWISSCI_MIDI_3_LENS, SWISSCI_MRC_2_WELL)


def plate_format_by_id(format_id: str | None) -> PlateFormat | None:
    if format_id is None:
        return None
    return next((item for item in PLATE_FORMATS if item.id == format_id), None)
