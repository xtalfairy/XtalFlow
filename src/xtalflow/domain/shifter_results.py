"""Reading back a SHIFTER worksheet after the harvest, to see what was picked.

XtalFlow writes the worksheet with its harvest columns empty; the SHIFTER fills
them as each crystal is picked. Those columns hold the only record of when a
crystal was harvested and which puck it went into, and nobody else writes them
into MxLive, so this is where that information has to come from.

Nothing here reinterprets what the instrument wrote. A time stays the string
the SHIFTER put in the file and a puck position stays its text, trimmed of the
whitespace the separator leaves behind: a value shown to somebody and sent on
unchanged can be checked against the instrument, while a value this module has
reformatted cannot.

What a row says is believed only for the wells the plan actually sent. A row
naming a well this plan never soaked is reported and never used - the MxLive
upsert would create a record for it, and a record there cannot be deleted.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from io import StringIO

from .worksheets import SHIFTER_HEADER, ShifterWorksheetRow

__all__ = [
    "SHIFTER_HEADER",
    "HarvestedCrystal",
    "HarvestReport",
    "collect_harvest",
    "expected_wells",
    "read_shifter_results",
    "wells_in_file",
]

_WELL_PATTERN = re.compile(r"^[A-H]\d{2}[a-z]$")

# The columns the SHIFTER fills in. A row is a harvest only if the first two
# are both there: a time without a puck says the crystal was picked up and
# not stored, which is not something to record against a puck position.
TIME_COLUMN = "TimeDeparture"
PUCK_COLUMN = "DestinationName"
POSITION_COLUMN = "DestinationLocation"
COMMENT_COLUMN = "Comment"
ARRIVAL_COLUMN = "TimeArrival"


@dataclass(frozen=True)
class HarvestedCrystal:
    """One crystal the SHIFTER reports as picked, as it wrote it."""

    plate_code: str
    well_address: str
    harvest_time: str
    puck_name: str
    puck_position: str
    comment: str = ""
    arrival_time: str = ""
    source: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.plate_code, self.well_address)

    def same_harvest_as(self, other: HarvestedCrystal) -> bool:
        """Whether two rows for one well say the same thing, whatever file they are in.

        The same worksheet is saved to both SHIFTER stations and again on every
        later save, so one well legitimately appears several times. Two rows
        that agree are one harvest seen twice; two that disagree cannot be
        resolved here.
        """
        return (
            self.harvest_time == other.harvest_time
            and self.puck_name == other.puck_name
            and self.puck_position == other.puck_position
        )

    def describe(self) -> str:
        position = f" position {self.puck_position}" if self.puck_position else ""
        return (
            f"{self.plate_code} {self.well_address} · {self.harvest_time} · "
            f"{self.puck_name}{position}"
        )


@dataclass(frozen=True)
class HarvestReport:
    """What a set of returned worksheets says about one revision's crystals."""

    # Matched to a well this revision sent, and agreed on wherever it appears.
    harvested: tuple[HarvestedCrystal, ...] = ()
    # Wells the revision sent that no file reports as picked.
    not_harvested: tuple[str, ...] = ()
    # Harvest rows for wells this revision never sent. Never acted on.
    unexpected: tuple[str, ...] = ()
    # One well reported twice with different values, so neither can be trusted.
    conflicts: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def can_be_recorded(self) -> bool:
        """Whether anything may be sent: a conflict or a stray row stops all of it.

        Partial sending would leave somebody to work out which half went, and
        the one thing MxLive cannot do is take a record back.
        """
        return bool(self.harvested) and not self.conflicts and not self.unexpected


def expected_wells(rows: tuple[ShifterWorksheetRow, ...]) -> tuple[tuple[str, str], ...]:
    """(plate, well) for each row of a worksheet, in the order it was written.

    The worksheet is rebuilt from the finalized revision's snapshot, so this is
    what the file sent to the instrument must contain, not what is on disk now.
    """
    return tuple(
        (row.plate_id, f"{row.plate_row}{int(row.plate_column):02d}{row.subwell}")
        for row in rows
    )


def wells_in_file(text: str) -> tuple[tuple[str, str], ...]:
    """(plate, well) for each readable row of a returned file, in file order.

    Only the columns XtalFlow wrote are read. Comparing these with
    ``expected_wells`` is what tells us the file at a recorded path is still
    the worksheet this revision sent, since nothing inside the file names the
    experiment or the revision.
    """
    found = []
    for row in _rows(text):
        well = _well_address(row)
        if well is not None:
            found.append((row.get("PlateID", "").strip(), well))
    return tuple(found)


def read_shifter_results(
    text: str, source: str = ""
) -> tuple[tuple[HarvestedCrystal, ...], tuple[str, ...]]:
    """The harvests one returned file reports, and what was passed over.

    A row with no time or no puck is not a harvest: it is a crystal the
    operator has not picked, or has not stored. Those are counted, not
    reported one by one, because a plate left half-harvested is the normal
    state of a plate in the middle of a session.
    """
    harvested: list[HarvestedCrystal] = []
    notes: list[str] = []
    unpicked = 0
    for number, row in enumerate(_rows(text), start=2):
        well = _well_address(row)
        if well is None:
            notes.append(
                f"{_where(source)}line {number}: "
                f"{_cell(row, 'PlateRow')}{_cell(row, 'PlateColumn')}"
                f"{_cell(row, 'PositionSubWell')} is not a well address."
            )
            continue
        time = _cell(row, TIME_COLUMN)
        puck = _cell(row, PUCK_COLUMN)
        if not time or not puck:
            unpicked += 1
            continue
        harvested.append(HarvestedCrystal(
            _cell(row, "PlateID"), well, time, puck,
            _cell(row, POSITION_COLUMN), _cell(row, COMMENT_COLUMN),
            _cell(row, ARRIVAL_COLUMN), source,
        ))
    if unpicked:
        notes.append(
            f"{_where(source)}{unpicked} "
            f"{'row' if unpicked == 1 else 'rows'} not picked yet."
        )
    return tuple(harvested), tuple(notes)


def collect_harvest(
    expected: tuple[tuple[str, str], ...],
    files: tuple[tuple[str, str], ...],
) -> HarvestReport:
    """Fold every returned file into one answer for this revision's crystals.

    ``expected`` is (plate, well) for the wells the revision sent; ``files`` is
    (source, text) for each returned worksheet. The same worksheet goes to both
    stations and again on every save, so a well may be reported several times:
    agreeing rows are one harvest, disagreeing ones are a conflict.
    """
    wanted = dict.fromkeys(expected)
    seen: dict[tuple[str, str], HarvestedCrystal] = {}
    conflicts: list[str] = []
    unexpected: list[str] = []
    notes: list[str] = []
    for source, text in files:
        rows, file_notes = read_shifter_results(text, source)
        notes.extend(file_notes)
        for row in rows:
            if row.key not in wanted:
                unexpected.append(f"{row.describe()} · {_name(source)} · not in this plan")
                continue
            held = seen.get(row.key)
            if held is None:
                seen[row.key] = row
            elif not held.same_harvest_as(row):
                conflicts.append(
                    f"{row.plate_code} {row.well_address} · "
                    f"{_name(held.source)} says {held.harvest_time} {held.puck_name}"
                    f"{_position(held)} · {_name(source)} says {row.harvest_time} "
                    f"{row.puck_name}{_position(row)}"
                )
    return HarvestReport(
        tuple(seen[key] for key in wanted if key in seen),
        tuple(f"{plate} {well}" for plate, well in wanted if (plate, well) not in seen),
        tuple(dict.fromkeys(unexpected)),
        tuple(dict.fromkeys(conflicts)),
        tuple(notes),
    )


def _rows(text: str) -> list[dict[str, str]]:
    """Rows keyed by the column names XtalFlow wrote the file with.

    Read by name and not by position: a column the instrument adds or moves
    would otherwise shift a puck name into a time.
    """
    reader = csv.reader(StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip().lstrip(";") for cell in rows[0]]
    if header[: len(SHIFTER_HEADER)] != [
        name.lstrip(";") for name in SHIFTER_HEADER
    ]:
        # Not a worksheet of ours. Nothing is guessed from it.
        return []
    return [dict(zip(header, row)) for row in rows[1:]]


def _cell(row: dict[str, str], column: str) -> str:
    return row.get(column, "").strip()


def _well_address(row: dict[str, str]) -> str | None:
    """A01a, from the row, column and subwell the worksheet was written with."""
    column = _cell(row, "PlateColumn")
    if not column.isdigit():
        return None
    well = f"{_cell(row, 'PlateRow')}{int(column):02d}{_cell(row, 'PositionSubWell')}"
    return well if _WELL_PATTERN.match(well) else None


def _name(source: str) -> str:
    return source.rsplit("/", 1)[-1] or source


def _where(source: str) -> str:
    return f"{_name(source)}: " if source else ""


def _position(row: HarvestedCrystal) -> str:
    return f" position {row.puck_position}" if row.puck_position else ""
