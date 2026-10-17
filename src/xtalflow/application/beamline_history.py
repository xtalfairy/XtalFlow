"""Past experiments as MxLive remembers them.

MxLive keeps one record per crystal.  This folds a year of them into one row
per experiment, using only what the records actually carry: XtalFlow's own
database knows nothing about an experiment made on another computer, or made
before XtalFlow existed.

Nothing here is written anywhere.  A past experiment is a reading, not a plan:
MxLive has no way to update or delete a labwork, so an experiment ID that has
been uploaded once must never be uploaded again.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from xtalflow.application.planning_service import EXPERIMENT_ID_PREFIXES
from xtalflow.domain.experiment_project import PlanType
from xtalflow.domain.mxlive import MxLiveLabwork


XTALFLOW = "XtalFlow"
ELSEWHERE = "XtalViewer / other"

# FragSC-202607-BRD4-01: the prefix gives the kind of experiment, the six
# digits the month the ID was handed out. Neither is a promise.
_EXPERIMENT_ID = re.compile(r"^([A-Za-z]+)-(\d{4})(\d{2})-")
_PLAN_TYPES = {prefix: plan for plan, prefix in EXPERIMENT_ID_PREFIXES.items()}


@dataclass(frozen=True)
class PastExperiment:
    """One experiment on MxLive, as far as its records tell."""

    experiment_id: str
    protein: str = ""
    plate_codes: tuple[str, ...] = ()
    crystals: int = 0
    harvested: int = 0
    pucks: tuple[str, ...] = ()
    month: str = ""
    plan_type: PlanType | None = None
    # The prefix as written when it is not one XtalFlow hands out.
    type_name: str = ""
    source: str = ELSEWHERE
    # The login name on the computer that uploaded it, not a person's name.
    uploaded_by: str = ""
    records: tuple[MxLiveLabwork, ...] = ()

    @property
    def fully_harvested(self) -> bool:
        return self.crystals > 0 and self.harvested == self.crystals


def past_experiments(
    labworks: Iterable[MxLiveLabwork],
) -> tuple[PastExperiment, ...]:
    """Fold records into experiments, newest month first."""
    grouped: dict[str, list[MxLiveLabwork]] = {}
    for item in labworks:
        grouped.setdefault(item.experiment_id, []).append(item)
    experiments = [
        _experiment(experiment_id, tuple(records))
        for experiment_id, records in grouped.items()
    ]
    experiments.sort(key=lambda item: (item.month, item.experiment_id), reverse=True)
    return tuple(experiments)


def _experiment(experiment_id: str, records: tuple[MxLiveLabwork, ...]) -> PastExperiment:
    match = _EXPERIMENT_ID.match(experiment_id)
    prefix = match.group(1) if match is not None else ""
    plan_type = _PLAN_TYPES.get(prefix)
    first = records[0]
    return PastExperiment(
        experiment_id,
        _common(record.protein_name for record in records),
        tuple(sorted({
            record.plate_code for record in records if record.plate_code
        })),
        len(records),
        sum(1 for record in records if record.harvest_time),
        tuple(sorted({record.puck_name for record in records if record.puck_name})),
        f"{match.group(2)}-{match.group(3)}" if match is not None else "",
        plan_type,
        "" if plan_type is not None else prefix,
        XTALFLOW if _from_xtalflow(records) else ELSEWHERE,
        first.raw.get("name") or "" if isinstance(first.raw.get("name"), str) else "",
        records,
    )


def _from_xtalflow(records: tuple[MxLiveLabwork, ...]) -> bool:
    """XtalFlow signs its uploads in the one field it is allowed to write."""
    return any(
        str(record.raw.get("staff_comments") or "").startswith("Uploaded by XtalFlow")
        for record in records
    )


def _common(values: Iterable[str | None]) -> str:
    """The one value the records agree on, or the first of several."""
    seen = [value for value in dict.fromkeys(values) if value]
    return seen[0] if seen else ""
