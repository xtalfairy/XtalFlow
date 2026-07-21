from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CrystalImage:
    plate_code: str
    batch_id: int
    well_number: int
    drop_number: int
    profile: str
    path: Path

    def __post_init__(self) -> None:
        if not self.plate_code.isdigit():
            raise ValueError("plate_code must contain digits only")
        if self.batch_id < 0:
            raise ValueError("batch_id must be non-negative")
        if self.well_number < 1:
            raise ValueError("well_number must be positive")
        if self.drop_number < 1:
            raise ValueError("drop_number must be positive")
        if not self.profile:
            raise ValueError("profile must not be empty")

    @property
    def navigation_label(self) -> str:
        return f"Plate {self.plate_code} · Well {self.well_number} · Drop {self.drop_number}"

    @property
    def image_key(self) -> str:
        """Stable logical identity, independent of the local filesystem path."""
        return (
            f"{self.plate_code}:{self.batch_id}:{self.well_number}:"
            f"{self.drop_number}:{self.profile}"
        )


@dataclass(frozen=True)
class PlateImages:
    plate_code: str
    batch_id: int
    profile: str
    images: tuple[CrystalImage, ...]

    def __post_init__(self) -> None:
        if not self.images:
            raise ValueError("a plate must contain at least one image")
        if any(image.plate_code != self.plate_code for image in self.images):
            raise ValueError("all images must belong to the same plate")
        if any(image.batch_id != self.batch_id for image in self.images):
            raise ValueError("all images must belong to the selected batch")
        if any(image.profile != self.profile for image in self.images):
            raise ValueError("all images must use the selected profile")

    @property
    def well_numbers(self) -> tuple[int, ...]:
        return tuple(sorted({image.well_number for image in self.images}))
