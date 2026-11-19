from __future__ import annotations

import re
from datetime import datetime


# Letters, digits, - and _ only. The name goes into the experiment ID, which
# becomes a file name on the instruments and an identifier in MxLive, and a
# dot in either is the kind of thing that gets read as an extension.
_SAFE_PROTEIN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
# An experiment that already has an ID is not choosing a name any more; its
# ID is fixed and reused for every later revision. A dot was allowed when
# that name was taken, so refusing it now would not take the dot out of the
# ID already written - it would only stop the work, mid-beamtime, over a
# name nothing new is built from.
_NAMED_PROTEIN = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")


def normalize_protein_name(value: str, *, already_named: bool = False) -> str:
    text = value.strip()
    if not text:
        raise ValueError("protein name is required")
    # A space is the one character that was accepted here and turned into
    # something else: the experiment ID got a hyphen, and MxLive got the
    # space. Data processing at the beamline builds paths from what MxLive
    # holds, and a space breaks it there, where nobody is watching. Asked
    # for here, where the answer costs one keystroke.
    if not already_named and re.search(r"\s", text):
        raise ValueError("protein name may not contain spaces; use - instead")
    normalized = re.sub(r"\s+", "-", text.upper())
    allowed = _NAMED_PROTEIN if already_named else _SAFE_PROTEIN
    if not allowed.fullmatch(normalized):
        raise ValueError(
            "protein name may contain letters, numbers, underscore, and hyphen"
        )
    return normalized


def protein_name_for_record(value: str) -> str:
    """The protein name as it is recorded, which is as the experiment ID has it.

    The ID has always replaced a space with a hyphen, while the record kept
    the space, so one experiment went to MxLive under two spellings - and the
    spelling with the space is the one that breaks data processing at the
    beamline. Normalizing here means the record cannot carry a space whatever
    is stored, including in an experiment named before this was asked for.

    A name too broken to normalize is returned as it stands. That is the
    preview's case only: the Setup step will not let such an experiment
    finalize, so nothing is uploaded under it.
    """
    try:
        return normalize_protein_name(value, already_named=True)
    except ValueError:
        return value.strip()


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
