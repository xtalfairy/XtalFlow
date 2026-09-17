"""Instruments that receive worksheets from XtalFlow."""

from __future__ import annotations

from dataclasses import dataclass


ECHO_650 = "echo650"
SHIFTER_1 = "shifter1"
SHIFTER_2 = "shifter2"

INSTRUMENT_LABELS = {
    ECHO_650: "ECHO 650",
    SHIFTER_1: "SHIFTER 1",
    SHIFTER_2: "SHIFTER 2",
}


@dataclass(frozen=True)
class InstrumentOutput:
    """One worksheet file delivered to one instrument's folder."""

    instrument: str
    path: str

    def __post_init__(self) -> None:
        if not self.instrument or not self.path:
            raise ValueError("instrument output needs an instrument and a path")

    @property
    def label(self) -> str:
        return INSTRUMENT_LABELS.get(self.instrument, self.instrument)
