from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class MxLiveReadError(RuntimeError):
    """MxLive could not be read safely or returned an invalid response."""


@dataclass(frozen=True)
class MxLiveLabwork:
    experiment_id: str
    protein_name: str | None
    plate_code: str | None
    plate_well: str | None
    puck_name: str | None
    raw: Mapping[str, Any]

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
            dict(data),
        )


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


class MxLiveReader(Protocol):
    def labworks(self, experiment_or_year: str) -> tuple[MxLiveLabwork, ...]: ...

    def experiment_ids(self, year: int) -> tuple[str, ...]: ...

    def samples(self) -> tuple[MxLiveSample, ...]: ...


def _optional_text(value: object) -> str | None:
    if value is None or value == "None":
        return None
    return str(value)

