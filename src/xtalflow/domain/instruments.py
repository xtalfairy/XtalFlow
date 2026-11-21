"""Instruments that receive worksheets from XtalFlow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


ECHO_650 = "echo650"
SHIFTER_0 = "shifter0"
SHIFTER_1 = "shifter1"
SHIFTER_2 = "shifter2"

class WorksheetKind(str, Enum):
    """Worksheet file formats XtalFlow can generate from a plan."""

    ECHO = "echo"
    SHIFTER = "shifter"


INSTRUMENT_LABELS = {
    ECHO_650: "ECHO 650",
    SHIFTER_0: "SHIFTER 0",
    SHIFTER_1: "SHIFTER 1",
    SHIFTER_2: "SHIFTER 2",
}
"""What a station is called on screen when the site file does not say.

The site file may name each one, and the beamline's does. This is what is
read when it does not, and a station missing from here is drawn by its id -
``shifter0`` in the lamp's tooltip and in every sentence that lists the
instruments a save reached.
"""


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
