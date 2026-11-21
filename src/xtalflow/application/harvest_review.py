"""Gather what is needed to say where an experiment's harvest stands.

Read-only. It opens the files the audit says were written, asks MxLive once
what it already holds, and hands back one row per crystal. Nothing here sends
anything, and no step of it writes to disk.

The order is deliberate: the local audit first, because it decides which
revision and which paths are even in question; then the files, because a file
that cannot be read may be the one holding a contradiction; then MxLive, which
has the last word on what is already recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..text import counted
from .labwork_upload import SUCCEEDED as UPLOAD_SUCCEEDED
from .labwork_upload import payloads_by_well
from .worksheet_export import SUCCEEDED as EXPORT_SUCCEEDED
from ..domain.harvest_record import (
    HarvestOutcome,
    HarvestRow,
    plan_harvest_records,
    stray_records,
)
from ..domain.mxlive import MxLiveLabwork
from ..domain.plan_lifecycle import WebDBUploadEvent, WorksheetExportEvent
from ..domain.shifter_results import (
    HarvestReport,
    collect_harvest,
    expected_wells,
    file_is_one_of,
    worksheets_a_revision_sent,
)

__all__ = [
    "HarvestReview",
    "HarvestReviewError",
    "HarvestSources",
    "prepare_harvest_review",
    "read_harvest",
]


class HarvestReviewError(Exception):
    """Why the harvest of this experiment cannot be looked at yet."""


@dataclass(frozen=True)
class HarvestReview:
    """Where one experiment's harvest stands, as far as reading can tell."""

    experiment_id: str
    revision_id: str
    account_username: str
    rows: tuple[HarvestRow, ...] = ()
    report: HarvestReport = field(default_factory=HarvestReport)
    # Files the audit named, and what became of each: read, unreadable, or
    # not one of this revision's worksheets.
    sources: tuple[tuple[str, str], ...] = ()
    # Everything there is to tell somebody, in one list: what reading the
    # files ran into, and the report's own notes, which are copied in here
    # rather than shown from there. So a screen reads this and nothing else -
    # reading both would say each of the report's notes twice.
    warnings: tuple[str, ...] = ()
    # Records MxLive holds under this experiment ID that the revision never
    # sent. Named rather than counted, because the first question is which.
    strays: tuple[str, ...] = ()

    @property
    def sendable(self) -> tuple[HarvestRow, ...]:
        return tuple(row for row in self.rows if row.outcome.is_sendable)

    @property
    def can_record(self) -> bool:
        """Whether anything may be sent at all.

        A conflict, a stray row or one well address on two plates stops the
        whole experiment, not just its own row: partial sending would leave
        somebody to work out which half went, and MxLive cannot take a record
        back.

        A stray record on MxLive stops it for a further reason: MxLive's
        update filter leaves out ``plate_code`` while the dict it applies
        carries it, so a record we did not send that shares a well address
        with one we did is not merely written by the call that writes ours -
        it is replaced by it, column for column, until the two are the same
        row. No per-well check can see that, because it is not one of our
        wells, and MxLive has no delete to undo it with.
        """
        return (
            bool(self.sendable)
            and self.report.can_be_recorded
            and not self.strays
        )

    def count(self, outcome: HarvestOutcome) -> int:
        return sum(1 for row in self.rows if row.outcome is outcome)

    def summary(self) -> str:
        parts = [f"{counted(len(self.sendable), 'harvest')} to record"]
        for outcome, word in (
            (HarvestOutcome.ALREADY_RECORDED, "already recorded"),
            (HarvestOutcome.PICK_FAILED, "picks failed"),
            (HarvestOutcome.NOT_PICKED, "not picked yet"),
        ):
            found = self.count(outcome)
            if found:
                parts.append(f"{found} {word}")
        attention = sum(1 for row in self.rows if row.outcome.needs_attention)
        if attention:
            parts.append(f"{attention} needing a look")
        return " · ".join(parts)


class HarvestReviewPort(Protocol):
    """What reading a harvest needs from the world outside the domain."""

    def list_webdb_uploads_for_experiment(
        self, experiment_id: str
    ) -> Sequence[WebDBUploadEvent]: ...

    def list_worksheet_exports(
        self, revision_id: str
    ) -> Sequence[WorksheetExportEvent]: ...


# Said of a file that belongs to the other half of a plate uploaded under
# two experiment IDs. Not a problem, so the screen leaves it out.
ANOTHER_PART = "a worksheet of this plate's other half"


@dataclass(frozen=True)
class HarvestSources:
    """What the local database says to read, and what to compare it against.

    Separate from reading it because the review database belongs to the thread
    that opened it - SQLite connections are not shared - while the files and
    MxLive have to be read off the interface thread, since an unreachable
    share or server would otherwise freeze the window.
    """

    experiment_id: str
    revision_id: str
    account_username: str
    expected: tuple[tuple[str, str], ...]
    # Each file's wells, for telling one of this revision's worksheets from
    # another experiment's file at the same path.
    candidates: tuple[tuple[tuple[str, str], ...], ...]
    paths: tuple[str, ...]
    payloads: Mapping[tuple[str, str], Mapping[str, Any]]
    # The wells of every worksheet of the whole plate, when this experiment
    # is one part of one uploaded under two IDs. A file of the other part is
    # a correct file in the right place, so it is named as that and not as
    # something to look into.
    plate_candidates: tuple[tuple[tuple[str, str], ...], ...] = ()


def prepare_harvest_review(
    experiment_id: str,
    store: HarvestReviewPort,
    plan_for_revision: Callable[[str], Any],
    *,
    username: str,
    worksheets_also_from: Sequence[str] = (),
    plate_plan: Any = None,
) -> HarvestSources:
    """Everything the local database knows: which revision, and which files.

    ``plan_for_revision`` rebuilds the plan from the finalized revision's
    snapshot, so the wells compared against are the ones that were sent, not
    whatever the plan says now.

    ``worksheets_also_from`` names further revisions whose exports hold this
    one's worksheets. An upload split across two experiment IDs sits on a
    part of a revision, while the worksheets were saved from the whole one,
    so without this the files would never be found and every crystal would
    read as not picked yet. Which file belongs to which part is answered by
    its contents either way.
    """
    upload = _succeeded_upload(experiment_id, store)
    if upload.username != username:
        raise HarvestReviewError(
            f"{experiment_id} was uploaded from the account {upload.username!r}; "
            f"only that account's records can be added to."
        )
    plan = plan_for_revision(upload.revision_id)
    return HarvestSources(
        experiment_id, upload.revision_id, upload.username,
        expected_wells(_whole_worksheet(plan)),
        worksheets_a_revision_sent(plan),
        _shifter_paths((upload.revision_id, *worksheets_also_from), store),
        _payloads_by_well(upload),
        worksheets_a_revision_sent(plate_plan) if plate_plan is not None else (),
    )


def read_harvest(
    sources: HarvestSources,
    read_file: Callable[[str], str],
    labworks: Callable[[str], Sequence[MxLiveLabwork]],
) -> HarvestReview:
    """The files and MxLive, read off the interface thread.

    ``read_file`` raises for a file it cannot read, and that is reported
    rather than skipped: an unreadable file may be the one that disagrees.
    """
    files: list[tuple[str, str]] = []
    read_states: list[tuple[str, str]] = []
    warnings: list[str] = []
    for path in sources.paths:
        try:
            text = read_file(path)
        except OSError as error:
            read_states.append((path, "cannot be read"))
            warnings.append(f"{path} cannot be read ({error.strerror or error}).")
            continue
        if not file_is_one_of(sources.candidates, text):
            if sources.plate_candidates and file_is_one_of(
                sources.plate_candidates, text
            ):
                # The other half of a plate split across two experiment IDs.
                # Nothing is wrong with it and nobody has to look at it, so
                # it must not be reported as a problem: a correct split
                # would otherwise raise an attention note every time.
                read_states.append((path, ANOTHER_PART))
                continue
            # Not refused outright: a worksheet saved twice, or one the
            # instrument has not touched yet, both land here.
            read_states.append((path, "not a worksheet of this revision"))
            continue
        read_states.append((path, "read"))
        files.append((path, text))

    report = collect_harvest(sources.expected, tuple(files))
    records = tuple(labworks(sources.experiment_id))
    rows = plan_harvest_records(
        sources.expected, report, records, sources.payloads
    )
    strays = stray_records(sources.expected, records)
    if strays:
        warnings.append(
            f"MxLive holds {counted(len(strays), 'record')} under "
            f"{sources.experiment_id} that this revision never sent "
            f"({', '.join(strays)}). Nothing will be recorded until it is "
            f"clear what they are: writing one of our wells writes any "
            f"record sharing its address."
        )
    return HarvestReview(
        sources.experiment_id, sources.revision_id, sources.account_username,
        rows, report, tuple(read_states), tuple(warnings) + report.notes,
        strays,
    )


def _succeeded_upload(
    experiment_id: str, store: HarvestReviewPort
) -> WebDBUploadEvent:
    events = [
        event for event in store.list_webdb_uploads_for_experiment(experiment_id)
        if event.status == UPLOAD_SUCCEEDED
    ]
    if not events:
        raise HarvestReviewError(
            f"{experiment_id} has no successful upload in this review database, "
            f"so there is nothing here to add a harvest to."
        )
    # The newest, because a later upload is the one MxLive now holds.
    return max(events, key=lambda event: event.attempted_at)


def _shifter_paths(
    revision_ids: Sequence[str], store: HarvestReviewPort
) -> tuple[str, ...]:
    """Every SHIFTER file these revisions were saved to, newest save first.

    Every station is listed, and every save of them, because the operator
    loads whichever one is in front of them and a second save is a legitimate
    copy of the same worksheet. Which file was used is answered by its
    contents, not by its name or its age.
    """
    paths: list[str] = []
    events = [
        event for revision_id in revision_ids
        for event in store.list_worksheet_exports(revision_id)
    ]
    for event in sorted(
        events, key=lambda event: event.exported_at, reverse=True,
    ):
        if event.status != EXPORT_SUCCEEDED:
            continue
        for output in event.outputs:
            if "shifter" in output.instrument.lower() and output.path not in paths:
                paths.append(output.path)
    return tuple(paths)


def _payloads_by_well(
    upload: WebDBUploadEvent,
) -> Mapping[tuple[str, str], Mapping[str, Any]]:
    """The records as they were first sent, keyed by plate and well.

    This is the only proof of who owns a record and what its other columns
    held. MxLive's write endpoint nulls every column it is not sent and
    validates nothing, so a harvest write that invented them would be
    accepted.

    The same reading as the one the upload's own verification uses, and
    deliberately the same function: if these two ever disagreed about which
    wells an attempt sent, an attempt could be called complete on one
    reading while the harvest was judged on another.
    """
    return payloads_by_well(upload.payload_json)


def _whole_worksheet(plan: Any):
    from ..domain.worksheets import build_shifter_worksheet

    return build_shifter_worksheet(plan)
