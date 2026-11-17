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

from .worksheets import (
    SHIFTER_HEADER,
    ShifterWorksheetRow,
    build_shifter_worksheet,
    shifter_worksheet_groups,
)

__all__ = [
    "SHIFTER_HEADER",
    "WRITTEN_COLUMNS",
    "file_is_one_of",
    "worksheets_a_revision_sent",
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
    # Reached and lost: picked, with no puck to record it against.
    attempted: tuple[HarvestedCrystal, ...] = ()
    # One well address on two plates in the same experiment. MxLive's update
    # filter leaves the plate out, so writing one of them writes both.
    shared_addresses: tuple[str, ...] = ()

    @property
    def can_be_recorded(self) -> bool:
        """Whether anything may be sent: a conflict or a stray row stops all of it.

        Partial sending would leave somebody to work out which half went, and
        the one thing MxLive cannot do is take a record back.
        """
        return (
            bool(self.harvested)
            and not self.conflicts
            and not self.unexpected
            and not self.shared_addresses
        )


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

    Only the columns XtalFlow wrote are read. Use ``file_holds_these_wells``
    to compare with a revision rather than this order: see there.
    """
    found = []
    for row in _rows(text).rows:
        well = _well_address(row)
        if well is not None:
            found.append((row.get("PlateID", "").strip(), well))
    return tuple(found)


def file_holds_these_wells(
    expected: tuple[tuple[str, str], ...], text: str
) -> bool:
    """Whether a returned file holds exactly the wells a revision sent.

    This is how we know the file at a recorded path is still the worksheet
    this revision sent: nothing inside names the experiment or the revision,
    so the plates and wells are the fingerprint.

    Compared with their counts but not in order. The SHIFTER rewrites our file
    in place and keeps the rows it has not picked, so the set is complete, but
    whether it keeps the original row order has not been verified against a
    copy of what we wrote.
    """
    return sorted(wells_in_file(text)) == sorted(expected)


WRITTEN_COLUMNS = (
    "PlateType", "PlateID", "LocationShifter", "PlateRow", "PlateColumn",
    "PositionSubWell",
)
"""The columns XtalFlow fills; the rest go out empty for the instrument."""


def worksheets_a_revision_sent(plan) -> tuple[tuple[tuple[str, str], ...], ...]:
    """The wells of each file this plan can have gone out as.

    A condition test harvested at several times goes out as one file per
    harvest time, and a plan exported before that was the rule went out whole.
    The export audit records paths, not which shape they were, so both are
    offered and the file itself decides by its content.
    """
    whole = expected_wells(build_shifter_worksheet(plan))
    groups = tuple(
        expected_wells(rows) for _minute, rows in shifter_worksheet_groups(plan)
    )
    return (whole, *(group for group in groups if group != whole))


def file_is_one_of(
    candidates: tuple[tuple[tuple[str, str], ...], ...], text: str
) -> bool:
    """Whether a returned file is one of those worksheets, whole or one group.

    Any other set of wells is refused: a file holding some of a group's wells
    is not a worksheet of ours, and a file holding wells from two groups is a
    worksheet nobody wrote.
    """
    return any(file_holds_these_wells(wells, text) for wells in candidates)


@dataclass(frozen=True)
class ShifterFile:
    """What one returned worksheet says."""

    # Picked and stored: a time and a puck, so there is something to record.
    harvested: tuple[HarvestedCrystal, ...] = ()
    # Picked and not stored - a crystal cracked, melted or was dropped. The
    # instrument records a time but no puck. Worth showing: it is the only
    # record that the crystal was reached and lost.
    attempted: tuple[HarvestedCrystal, ...] = ()
    notes: tuple[str, ...] = field(default=())


def read_shifter_results(text: str, source: str = "") -> ShifterFile:
    """The harvests one returned file reports, and what was passed over.

    A row needs both a time and a puck to be a harvest. A time without a puck
    is a pick that failed, which is reported separately; neither time nor puck
    is a crystal nobody has reached yet, and those are only counted, because a
    plate half harvested is the normal state of a plate mid-session.
    """
    harvested: list[HarvestedCrystal] = []
    attempted: list[HarvestedCrystal] = []
    notes: list[str] = []
    read = _rows(text)
    unpicked = 0
    for number, row in enumerate(read.rows, start=read.first_line):
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
        if not time and not puck:
            unpicked += 1
            continue
        crystal = HarvestedCrystal(
            _cell(row, "PlateID"), well, time, puck,
            _cell(row, POSITION_COLUMN), _cell(row, COMMENT_COLUMN),
            _cell(row, ARRIVAL_COLUMN), source,
        )
        (harvested if time and puck else attempted).append(crystal)
    if unpicked:
        notes.append(
            f"{_where(source)}{unpicked} "
            f"{'row' if unpicked == 1 else 'rows'} not picked yet."
        )
    if not read.rows:
        notes.append(f"{_where(source)}not a worksheet XtalFlow wrote.")
    return ShifterFile(tuple(harvested), tuple(attempted), tuple(notes))


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
    attempted: list[HarvestedCrystal] = []
    for source, text in files:
        read = read_shifter_results(text, source)
        notes.extend(read.notes)
        attempted.extend(read.attempted)
        for row in read.harvested:
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
        tuple(row for row in attempted if row.key in wanted),
        _addresses_on_more_than_one_plate(expected),
    )


def _addresses_on_more_than_one_plate(
    expected: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    """Well addresses this revision sent from more than one plate.

    MxLive finds a record by project, experiment, protein, plate and well, but
    the update it then applies filters on everything except the plate (its
    ``AddLabwork`` view). So writing a harvest onto one of two records sharing
    a well address writes it onto both, and the wrong crystal is then said to
    be in that puck position. Such an experiment is refused rather than
    half-written.
    """
    plates: dict[str, set[str]] = {}
    for plate, well in expected:
        plates.setdefault(well, set()).add(plate)
    return tuple(
        f"{well} is on {', '.join(sorted(found))}"
        for well, found in plates.items() if len(found) > 1
    )


@dataclass(frozen=True)
class _Read:
    rows: list[dict[str, str]]
    # Which line of the file the first row is on, for naming a bad one.
    first_line: int = 1


def _rows(text: str) -> _Read:
    """Rows keyed by the column names the worksheet was written with.

    The SHIFTER rewrites our file in place and **drops the header line**, so a
    returned file begins with data. The names are then taken from the order
    XtalFlow wrote.

    A row is read only if it has at least the columns XtalFlow writes and no
    more than the header's, because the fields are a puck name and a time and
    mistaking one for the other would be recorded as fact. A row wider than
    the header is a layout nobody here has seen, so the file is refused rather
    than read crookedly.

    A short row is padded, which leaves its harvest columns empty and so
    reports the well as not picked. Files from 2022 on this beamline hold
    their unharvested rows that way, six fields and nothing after
    ``SwissCI-MRC-3d,768,AM,A,1,a``, and a file where the instrument filled
    only the rows it picked would otherwise be refused whole and its harvest
    lost.
    """
    names = [name.lstrip(";") for name in SHIFTER_HEADER]
    authored = len(WRITTEN_COLUMNS)
    reader = csv.reader(StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return _Read([])
    if [cell.strip().lstrip(";") for cell in rows[0]][: len(names)] == names:
        return _Read([_named(names, row) for row in rows[1:]], 2)
    if all(authored <= len(row) <= len(names) for row in rows):
        return _Read([_named(names, row) for row in rows])
    return _Read([])


def _named(names: list[str], row: list[str]) -> dict[str, str]:
    return dict(zip(names, (*row, *[""] * (len(names) - len(row)))))


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
