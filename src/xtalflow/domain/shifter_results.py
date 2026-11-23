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
    "HarvestConflict",
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

# Columns no worksheet of ours has, written by an instrument that answers in
# its own layout rather than filling ours in.
WELL_COLUMN = "Well"
"""Row, column and subwell in one unpadded cell: A1a, B6c."""
MOUNTED_COLUMN = "Mounted"
MOUNTED_INTO_A_PUCK = "mount"
"""Said of a row with a puck. Fail is said of one with both times and none."""

_COMBINED_WELL = re.compile(r"^([A-H])(\d{1,2})([a-z])$")

# Our column for each name an instrument may write for it, folded.
#
# One of this beamline's three SHIFTERs does not fill our file in at all. It
# writes the harvest out in its own thirteen columns, under the same name, and
# moves the file we sent into an original/ subfolder beside it. Measured on the
# file it returned on 2026-11-23: a header line carrying a byte-order mark and
# no leading semicolon, 320 rows, and - once the addresses are padded - exactly
# the wells of the worksheet we had sent.
#
# Which is why this is read by name and never by position. Before it, a file
# in that layout fell through to the positional read, where XtalNo landed in
# PlateColumn and PuckID a column away from DestinationName. The only thing
# that kept a crystal from being recorded into a puck nobody filled was that
# the address then came out unreadable and the file was refused whole - so the
# harvest of a whole station was lost instead, reported as "not a worksheet of
# this revision", which is the one thing it was not.
#
# Both spellings of PositionSubWell are here because files on this beamline
# carry the header both ways, mixed, 288 of 502 with a header at all. A header
# the match missed was read as a data row; every harvest still landed in the
# right place, but the note said the header "is not a well address", and a
# note that cries wolf teaches somebody to stop reading the notes.
_COLUMN_ALIASES = {
    "platetype": ";PlateType",
    "plateid": "PlateID",
    "locationshifter": "LocationShifter",
    "platerow": "PlateRow",
    "platecolumn": "PlateColumn",
    "positionsubwell": "PositionSubWell",
    "comment": COMMENT_COLUMN,
    "crystalid": "CrystalID",
    "timearrival": ARRIVAL_COLUMN,
    "timedeparture": TIME_COLUMN,
    "pickduration": "PickDuration",
    "destinationname": PUCK_COLUMN,
    "destinationlocation": POSITION_COLUMN,
    "barcode": "Barcode",
    "externalcomment": "ExternalComment",
    # The thirteen the other instrument writes.
    "well": WELL_COLUMN,
    "xtalno": "CrystalID",
    "arr.time": ARRIVAL_COLUMN,
    "dept.time": TIME_COLUMN,
    "duration(s)": "PickDuration",
    "puckid": PUCK_COLUMN,
    "position": POSITION_COLUMN,
    "mounted": MOUNTED_COLUMN,
}

# Without all of these a header line is not one we can read a harvest from,
# and a layout we cannot read is refused rather than guessed at.
_REQUIRED_COLUMNS = ("PlateID", TIME_COLUMN, PUCK_COLUMN, POSITION_COLUMN)
_ADDRESS_COLUMNS = ("PlateRow", "PlateColumn", "PositionSubWell")


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

        The same worksheet is saved to every SHIFTER station and again on
        every later save, so one well legitimately appears several times. Two rows
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
class HarvestConflict:
    """Two files reporting the same well differently.

    The key is here because every caller needs it - the sentence is for the
    person, and reading a well address back out of a sentence is the kind of
    thing that works until somebody rewords the sentence.
    """

    plate_code: str
    well_address: str
    message: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.plate_code, self.well_address)

    def __str__(self) -> str:
        return self.message


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
    conflicts: tuple[HarvestConflict, ...] = ()
    notes: tuple[str, ...] = field(default=())
    # Reached and lost: picked, with no puck to record it against.
    attempted: tuple[HarvestedCrystal, ...] = ()
    # One well address on two plates in the same experiment. MxLive's update
    # filter leaves the plate out, so writing one of them writes both.
    shared_addresses: tuple[str, ...] = ()

    @property
    def can_be_recorded(self) -> bool:
        """Whether anything may be sent. Three things stop all of it, for two reasons.

        A conflict or an unexpected row stops it because what the files say
        cannot be resolved here, and sending the part that is clear would
        leave somebody to work out which part went. That is not an argument
        against partial sending as such - a harvest is routinely sent while
        wells that were not picked, and picks that failed, are left out, and a
        later run finishes it.

        A well address on two plates stops it for a reason that has nothing to
        do with halves. The write destroys records that are not the well being
        written: MxLive applies the update without the plate and with every
        column, so the siblings become copies of ours. Sending the wells whose
        address is unique would be safe on its own terms, and it is still not
        done - see ``_addresses_on_more_than_one_plate`` for what it would
        leave behind.
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
    # Neither a time nor a puck: nobody has reached these yet, as far as this
    # one file knows. Kept rather than said, because the same worksheet goes
    # to every station and the copies nobody worked from report every well as
    # unpicked - which is not news about the plate, only about the copy.
    unpicked: tuple[tuple[str, str], ...] = ()


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
    unpicked: list[tuple[str, str]] = []
    for number, row in enumerate(read.rows, start=read.first_line):
        well = _well_address(row)
        if well is None:
            notes.append(
                f"{_where(source)}line {number}: "
                f"{_written_well(row)} is not a well address."
            )
            continue
        time = _cell(row, TIME_COLUMN)
        puck = _cell(row, PUCK_COLUMN)
        if not time and not puck:
            unpicked.append((_cell(row, "PlateID"), well))
            continue
        mounted = _cell(row, MOUNTED_COLUMN)
        if puck and mounted and mounted.casefold() != MOUNTED_INTO_A_PUCK:
            # Measured over the three files this instrument wrote on
            # 2026-11-23: Mount on every row with a puck, Fail on every row
            # with both times and no puck, nothing on a row with neither, and
            # no fourth value anywhere. A Fail therefore needs nothing from
            # this guard - it has no puck, so it reads as a failed pick by
            # the same rule as the fifteen-column files, which is what the
            # test beside it pins.
            #
            # The guard is for the value nobody has seen yet. A puck name
            # beside a word we cannot read is not evidence that the crystal
            # went into that puck, and a harvest on MxLive cannot be taken
            # back. So the row is left unrecorded and named, which costs a
            # harvest that waits and buys a record nobody has to undo.
            notes.append(
                f"{_where(source)}line {number}: {well} says Mounted "
                f"\u201c{mounted}\u201d, which nothing here knows the meaning "
                f"of, so its harvest is not read."
            )
            continue
        crystal = HarvestedCrystal(
            _cell(row, "PlateID"), well, time, puck,
            _cell(row, POSITION_COLUMN), _cell(row, COMMENT_COLUMN),
            _cell(row, ARRIVAL_COLUMN), source,
        )
        (harvested if time and puck else attempted).append(crystal)
    if not read.rows:
        notes.append(f"{_where(source)}not a worksheet XtalFlow wrote.")
    return ShifterFile(
        tuple(harvested), tuple(attempted), tuple(notes), tuple(unpicked),
    )


def collect_harvest(
    expected: tuple[tuple[str, str], ...],
    files: tuple[tuple[str, str], ...],
) -> HarvestReport:
    """Fold every returned file into one answer for this revision's crystals.

    ``expected`` is (plate, well) for the wells the revision sent; ``files`` is
    (source, text) for each returned worksheet. The same worksheet goes to every
    station and again on every save, so a well may be reported several times:
    agreeing rows are one harvest, disagreeing ones are a conflict.

    That is also why nothing here says how many rows of a file are unpicked.
    The copy nobody worked from reports every well as unpicked, so a plate
    harvested to the last crystal raised a warning for each of its harvest
    groups on each station - saying of a well that nobody has reached it while
    the row above said it was in a puck. Which wells no copy reports as picked
    is ``not_harvested``, counted once over all of them, and each one says so
    in its own row.
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
                conflicts.append(HarvestConflict(
                    row.plate_code, row.well_address,
                    f"{row.plate_code} {row.well_address} · "
                    f"{_name(held.source)} says {held.harvest_time} {held.puck_name}"
                    f"{_position(held)} · {_name(source)} says {row.harvest_time} "
                    f"{row.puck_name}{_position(row)}",
                ))
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

    Read on 2026-11-23 in the server's own code, ``remote/views.py``, class
    ``AddLabwork`` - the view behind ``upload_labworks/<beamline>/`` - and not
    inferred: it looks a record up by project, experiment, protein, well **and
    plate**, then applies the update filtered by project, experiment, protein
    and well, with the plate left out. It is a ``queryset.update(**details)``,
    and ``details`` is every column of the record, so the rows it reaches do
    not merely gain our harvest: they are overwritten column for column with
    ours, ``plate_code``, ``soak_id`` and the fragment among them. Four
    records sharing an address become four copies of one, and the other three
    plates no longer say which crystal they held.

    (The code was read from a 2026-10-17 install tarball on the beamline. That
    it is what the server is running has not been verified by anyone here.)

    Such an experiment is refused rather than half-written, and it cannot be
    rescued by writing the siblings back afterwards: each write reaches the
    others in turn, so the last one leaves them all alike. No per-well check
    can see it either - the rows it destroys are not the well being written.
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
    """Rows keyed by our column names, however the instrument wrote them.

    A header line is read by name, so a file in another instrument's layout is
    read correctly or refused - never read crookedly. A file with no header at
    all is read by position, as our own columns, because the SHIFTER that
    rewrites our file in place drops the header line: 214 of 502 files on this
    beamline have none.

    Positionally, a row is read only if it has at least the columns XtalFlow
    writes and no more than the header's, because the fields are a puck name
    and a time and mistaking one for the other would be recorded as fact. The
    first row must also be a readable address: a file whose first line is a
    header we do not know is a layout nobody here has seen, and the one thing
    not to do with it is read it as ours.

    A short row is padded, which leaves its harvest columns empty and so
    reports the well as not picked. Files from 2022 on this beamline hold
    their unharvested rows that way, six fields and nothing after
    ``SwissCI-MRC-3d,768,AM,A,1,a``, and a file where the instrument filled
    only the rows it picked would otherwise be refused whole and its harvest
    lost.
    """
    names = [name.lstrip(";") for name in SHIFTER_HEADER]
    authored = len(WRITTEN_COLUMNS)
    reader = csv.reader(StringIO(text.lstrip("\ufeff")))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return _Read([])
    columns = _columns_named_in(rows[0])
    if columns is not None:
        return _Read([_by_column(columns, row) for row in rows[1:]], 2)
    if (
        all(authored <= len(row) <= len(names) for row in rows)
        and _well_address(_named(names, rows[0])) is not None
    ):
        return _Read([_named(names, row) for row in rows])
    return _Read([])


def _columns_named_in(heading: list[str]) -> list[str] | None:
    """Our column for each cell of a header line, or None if it is not one.

    None for a data row as much as for a layout we cannot read, and the two
    cannot be told apart here: a file with no header begins with data, whose
    cells name no column of anybody's.
    """
    columns = [
        _COLUMN_ALIASES.get(
            cell.strip().lstrip("\ufeff").lstrip(";").casefold(), ""
        )
        for cell in heading
    ]
    found = set(columns)
    if not all(column in found for column in _REQUIRED_COLUMNS):
        return None
    if WELL_COLUMN not in found and not all(
        column in found for column in _ADDRESS_COLUMNS
    ):
        return None
    return columns


def _by_column(columns: list[str], row: list[str]) -> dict[str, str]:
    """One row under the names its header gave, ignoring columns we have none for."""
    return {
        column: cell for column, cell in zip(columns, row) if column
    }


def _named(names: list[str], row: list[str]) -> dict[str, str]:
    return dict(zip(names, (*row, *[""] * (len(names) - len(row)))))


def _cell(row: dict[str, str], column: str) -> str:
    return row.get(column, "").strip()


def _well_address(row: dict[str, str]) -> str | None:
    """A01a, however the instrument wrote the row, column and subwell.

    One of them writes all three as one unpadded cell, A1a, so a single-digit
    column is padded here: the padded form is what the worksheet was written
    with and what MxLive holds the record under.
    """
    combined = _cell(row, WELL_COLUMN)
    if combined:
        found = _COMBINED_WELL.match(combined)
        if found is None:
            return None
        letter, column, subwell = found.groups()
    else:
        letter = _cell(row, "PlateRow")
        column = _cell(row, "PlateColumn")
        subwell = _cell(row, "PositionSubWell")
    if not column.isdigit():
        return None
    well = f"{letter}{int(column):02d}{subwell}"
    return well if _WELL_PATTERN.match(well) else None


def _written_well(row: dict[str, str]) -> str:
    """The address cells as the instrument wrote them, for naming a bad row."""
    return _cell(row, WELL_COLUMN) or (
        f"{_cell(row, 'PlateRow')}{_cell(row, 'PlateColumn')}"
        f"{_cell(row, 'PositionSubWell')}"
    )


def _name(source: str) -> str:
    return source.rsplit("/", 1)[-1] or source


def _where(source: str) -> str:
    return f"{_name(source)}: " if source else ""


def _position(row: HarvestedCrystal) -> str:
    return f" position {row.puck_position}" if row.puck_position else ""
