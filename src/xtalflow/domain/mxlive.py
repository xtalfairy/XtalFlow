from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Mapping


class MxLiveReadError(RuntimeError):
    """MxLive could not be read safely or returned an invalid response."""


class MxLiveWriteError(RuntimeError):
    """MxLive rejected or could not safely accept an upload."""


class MxLiveUncertainWriteError(MxLiveWriteError):
    """The request may have been stored even though no valid response arrived."""


class MxLivePartialWriteError(MxLiveWriteError):
    """Some records were accepted before a later record failed."""

    def __init__(
        self, completed_count: int, total_count: int, cause: str,
        outcome_uncertain: bool = False,
    ) -> None:
        self.completed_count = completed_count
        self.total_count = total_count
        self.outcome_uncertain = outcome_uncertain
        accepted = "at least " if outcome_uncertain else ""
        super().__init__(
            f"MxLive accepted {accepted}{completed_count} of {total_count} records "
            f"before the upload failed ({cause}); do not retry until WebDB is reviewed"
        )


@dataclass(frozen=True)
class MxLiveLabwork:
    experiment_id: str
    protein_name: str | None
    plate_code: str | None
    plate_well: str | None
    puck_name: str | None
    # Filled by the beamline when the crystal is harvested; empty until then.
    harvest_time: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MxLiveLabwork:
        experiment_id = data.get("expri_id")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise MxLiveReadError("MxLive labwork is missing expri_id")
        return cls(
            experiment_id.strip(),
            _optional_text(data.get("protein_name")),
            _optional_text(data.get("plate_code")),
            _optional_text(data.get("plate_well")),
            _optional_text(data.get("puck_name")),
            _optional_text(data.get("harvest_time")),
            dict(data),
        )


@dataclass(frozen=True)
class BeamlineProgress:
    """What the beamline has done with one experiment's crystals.

    MxLive has no field for accepting an experiment.  What it does carry is
    the result of the steps its staff take on each labwork: a harvest time
    and the puck a crystal went into.  Those are the only two things anyone
    outside XtalFlow writes into a record it can read.
    """

    records: int = 0
    harvested: int = 0
    pucks: tuple[str, ...] = ()

    @property
    def fully_harvested(self) -> bool:
        return self.records > 0 and self.harvested == self.records


def beamline_progress(
    labworks: "Iterable[MxLiveLabwork]",
) -> dict[str, BeamlineProgress]:
    """One answer per experiment, folded from its records."""
    records: dict[str, int] = {}
    harvested: dict[str, int] = {}
    pucks: dict[str, set[str]] = {}
    for item in labworks:
        records[item.experiment_id] = records.get(item.experiment_id, 0) + 1
        if item.harvest_time:
            harvested[item.experiment_id] = harvested.get(item.experiment_id, 0) + 1
        if item.puck_name:
            pucks.setdefault(item.experiment_id, set()).add(item.puck_name)
    return {
        experiment_id: BeamlineProgress(
            count,
            harvested.get(experiment_id, 0),
            tuple(sorted(pucks.get(experiment_id, ()))),
        )
        for experiment_id, count in records.items()
    }


@dataclass(frozen=True)
class MxLiveSample:
    name: str
    sample_id: str | None
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MxLiveSample:
        name = data.get("name") or data.get("sample_name")
        if not isinstance(name, str) or not name.strip():
            raise MxLiveReadError("MxLive sample is missing a name")
        identifier = data.get("id") or data.get("sample_id")
        return cls(name.strip(), str(identifier) if identifier is not None else None,
                   dict(data))


def _optional_text(value: object) -> str | None:
    if value is None or value == "None":
        return None
    return str(value)
