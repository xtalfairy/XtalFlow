from __future__ import annotations

import re
from datetime import datetime


# Letters, digits, - and _ only. The name goes into the experiment ID, which
# becomes a file name on the instruments and an identifier in MxLive, and a
# dot in either is the kind of thing that gets read as an extension.
_SAFE_PROTEIN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")


def normalize_protein_name(value: str) -> str:
    normalized = re.sub(r"\s+", "-", value.strip().upper())
    if not normalized:
        raise ValueError("protein name is required")
    if not _SAFE_PROTEIN.fullmatch(normalized):
        raise ValueError(
            "protein name may contain letters, numbers, underscore, and hyphen"
        )
    return normalized


def suggest_experiment_id(
    prefix: str,
    protein: str,
    existing_ids: set[str],
    now: datetime | None = None,
) -> str:
    normalized = normalize_protein_name(protein)
    timestamp = now or datetime.now()
    base = f"{prefix}-{timestamp:%Y%m}-{normalized}"
    sequence = 1
    while f"{base}-{sequence:02d}" in existing_ids:
        sequence += 1
    return f"{base}-{sequence:02d}"
