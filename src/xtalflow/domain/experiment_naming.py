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


def _folded(taken) -> set[str]:
    """The taken IDs, compared the way MxLive's write compares them.

    MxLive finds the record to update with
    ``filter(project_id, expri_id, protein_name, plate_well)`` and ``=``,
    which on MySQL follows the column's collation - and the default
    collations there ignore case. So if MxLive already holds an experiment
    whose ID differs from ours only in case, our upload's lookup finds that
    experiment's records and the update rewrites them, every column, our
    plate code and compound names included. There is no delete.

    An ID this program builds carries YYYYMM, and case does not change a
    digit, so any ID that folds onto one of ours holds that same stamp and so
    comes back in the year read this set is built from. That is what makes
    comparing folded here enough.

    It has to be here, and it cannot be done later. MxLive's read is
    ``expri_id__contains``, which Django compiles to ``LIKE BINARY`` on
    MySQL - case-sensitive whatever the collation is, and measured so on this
    beamline: reading a known ID in lower case and in upper case returned
    nothing while the ID as written returned its seven records. So the record
    at risk is invisible to every guard that reads MxLive by our experiment
    ID, which is all of them: the check before an upload, the read before a
    harvest, the re-read that verifies a write. Widening any of those
    comparisons would be code that can never fire. The one read that does see
    such a record is the year read, because a year is digits.

    Folding costs nothing when nothing folds, and when something does it only
    moves the sequence number along - nobody is refused and nothing is
    renamed. The comparison that must stay exact is the one in
    ``harvest_record.IDENTITY_COLUMNS``: it decides whether a record is ours
    to write, and folding that would hand somebody else's record to our
    payload.
    """
    return {str(item).casefold() for item in taken}


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


# The shape this app builds: PreTest-202611-BRD4-01. Anything else was not
# built here, and cannot be read for the name it was built from.
_OUR_ID = re.compile(r"[A-Za-z0-9]+-\d{6}-.+-\d{2,}")


def next_id_beside(experiment_id: str, taken: set[str]) -> str:
    """The next free ID beside this one, differing only in its sequence number.

    For a second experiment that belongs with one that already exists - the
    late half of a plate going to MxLive under its own name. Built from the
    ID itself rather than from the protein and today's date, because both
    move: a plate finalized on the last of the month and uploaded the next
    morning would otherwise be given next month's stamp and a sequence of
    01, which is a stranger to the experiment it belongs beside. An
    overnight soak makes that the ordinary case, not an edge.

    Taking the name apart also takes nothing back: a protein name that was
    allowed when the experiment was named stays allowed here, where asking
    again could only strand work already underway.

    Raises ValueError for an ID this cannot read, because inventing a name
    for a record that goes to MxLive under it is worse than refusing.
    """
    match = re.fullmatch(r"(.*-)(\d{2,})", experiment_id.strip())
    if not match:
        raise ValueError(
            f"{experiment_id!r} does not end in a sequence number, so there is "
            f"no next one beside it"
        )
    base, digits = match.group(1), match.group(2)
    width = len(digits)
    sequence = int(digits)
    folded = _folded(taken)
    while True:
        sequence += 1
        candidate = f"{base}{sequence:0{width}d}"
        if candidate.casefold() not in folded:
            return candidate


def experiment_id_holds_protein(experiment_id: str, protein: str) -> bool:
    """Whether this ID was built from this protein name.

    The rules for a name are allowed to tighten, and an experiment already
    underway must not be stranded by that: its ID is fixed and every later
    revision reuses it, so judging its name again takes nothing back. But
    "has an ID" is the wrong question to ask for that leniency, because it
    also excuses a name typed *after* the ID was fixed - finalize as BRD4,
    rename to BRD4.v2, and the record goes up as BRD4.v2 under
    PreTest-202611-BRD4-01. That is the ID and the record disagreeing, which
    is the very thing the normalizing was for.

    So the question is whether the ID carries this name. An ID of some other
    shape is not ours to read, and not held against anybody: leniency
    stands, because stranding somebody mid-beamtime is the worse mistake.
    """
    if not experiment_id:
        return False
    if not _OUR_ID.fullmatch(experiment_id):
        return True
    try:
        name = normalize_protein_name(protein, already_named=True)
    except ValueError:
        return False
    return bool(re.fullmatch(rf".*-{re.escape(name)}-\d{{2,}}", experiment_id))


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
    taken = _folded(existing_ids)
    sequence = 1
    while f"{base}-{sequence:02d}".casefold() in taken:
        sequence += 1
    return f"{base}-{sequence:02d}"
