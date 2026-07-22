from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .fragment_screening import FragmentScreenPlan
from .plate_format import plate_format_by_id
from .raw_crystal import RawCrystalPlan


LABWORK_COLUMNS = (
    "crystal_no", "expri_id", "protein_name", "plate_type", "plate_code",
    "plate_well", "plate_x", "plate_y", "plate_imgpath", "soak_id",
    "soak_smile", "project_id",
)


def _mxlive_plate_type(plate_format_id: str) -> str:
    plate_format = plate_format_by_id(plate_format_id)
    if plate_format is None:
        # Preserve the source value for old or externally supplied formats so
        # the preview remains useful instead of silently losing information.
        return plate_format_id
    return plate_format.instrument_name or plate_format.display_name


@dataclass(frozen=True)
class LabworkRecord:
    name: str
    experiment_id: str
    protein_name: str
    plate_type: str
    plate_code: str
    plate_image_path: str
    plate_well: str
    plate_x: Decimal
    plate_y: Decimal
    crystal_number: int
    soak_plate: str
    soak_well: str
    soak_volume_nl: Decimal
    soak_id: str
    soak_smiles: str
    mxlive_account_id: str
    staff_comments: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "staff_comments": self.staff_comments,
            "status": 5,
            "attachment": "null",
            "expri_id": self.experiment_id,
            "protein_name": self.protein_name,
            "plate_type": self.plate_type,
            "plate_code": self.plate_code,
            "plate_imgpath": self.plate_image_path,
            "plate_well": self.plate_well,
            "plate_x": float(self.plate_x),
            "plate_y": float(self.plate_y),
            "crystal_no": self.crystal_number,
            "soak_plate": self.soak_plate,
            "soak_well": self.soak_well,
            "soak_vol": float(self.soak_volume_nl),
            "soak_id": self.soak_id,
            "soak_smile": self.soak_smiles,
            # MxLive's historical API name; this identifies the account owner.
            "project_id": self.mxlive_account_id,
        }


def build_fragment_labworks(
    plan: FragmentScreenPlan, *, experiment_id: str, protein_name: str,
    username: str, account_id: str,
) -> tuple[LabworkRecord, ...]:
    records: list[LabworkRecord] = []
    for index, assignment in enumerate(plan.assignments, start=1):
        records.append(LabworkRecord(
            username, experiment_id, protein_name,
            _mxlive_plate_type(assignment.crystal.plate_format_id),
            assignment.crystal.destination_plate,
            assignment.crystal.image_path or assignment.crystal.image_key,
            assignment.crystal.destination_well,
            Decimal("0"), Decimal("0"), index,
            assignment.fragment.source_plate,
            assignment.fragment.source_well,
            assignment.total_volume_nl,
            assignment.fragment.compound_id,
            assignment.fragment.smiles,
            account_id,
            "Uploaded by XtalFlow · Fragment Screening",
        ))
    return tuple(records)


def build_raw_crystal_labworks(
    plan: RawCrystalPlan, *, experiment_id: str, protein_name: str,
    username: str, account_id: str,
) -> tuple[LabworkRecord, ...]:
    return tuple(
        LabworkRecord(
            username, experiment_id, protein_name,
            _mxlive_plate_type(crystal.plate_format_id),
            crystal.destination_plate,
            crystal.image_path or crystal.image_key,
            crystal.destination_well, Decimal("0"), Decimal("0"), index,
            "", "", Decimal("0"), "", "",
            account_id, "Uploaded by XtalFlow · Raw Crystal Plan",
        )
        for index, crystal in enumerate(plan.crystals, start=1)
    )
