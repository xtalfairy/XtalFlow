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

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from .labwork_upload import SUCCEEDED as UPLOAD_SUCCEEDED
from .worksheet_export import SUCCEEDED as EXPORT_SUCCEEDED
from ..domain.harvest_record import HarvestOutcome, HarvestRow, plan_harvest_records
from ..domain.mxlive import MxLiveLabwork
from ..domain.plan_lifecycle import WebDBUploadEvent, WorksheetExportEvent
from ..domain.shifter_results import (
    HarvestReport,
    collect_harvest,
    expected_wells,
    file_is_one_of,
    worksheets_a_revision_sent,
)

__all__ = ["HarvestReview", "HarvestReviewError", "review_harvest"]


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
    warnings: tuple[str, ...] = ()

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
        """
        return bool(self.sendable) and self.report.can_be_recorded

    def count(self, outcome: HarvestOutcome) -> int:
        return sum(1 for row in self.rows if row.outcome is outcome)

    def summary(self) -> str:
        parts = [f"{len(self.sendable)} to record"]
        for outcome, word in (
            (HarvestOutcome.ALREADY_RECORDED, "already on MxLive"),
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


def review_harvest(
    experiment_id: str,
    store: HarvestReviewPort,
    plan_for_revision: Callable[[str], Any],
    read_file: Callable[[str], str],
    labworks: Callable[[str], Sequence[MxLiveLabwork]],
    *,
    username: str,
) -> HarvestReview:
    """One experiment's harvest, read from the audit, the files and MxLive.

    ``plan_for_revision`` rebuilds the plan from the finalized revision's
    snapshot, so the wells compared against are the ones that were sent, not
    whatever the plan says now. ``read_file`` raises for a file it cannot read,
    and that is reported rather than skipped: an unreadable file may be the one
    that disagrees.
    """
    upload = _succeeded_upload(experiment_id, store)
    if upload.username != username:
        raise HarvestReviewError(
            f"{experiment_id} was uploaded from the account {upload.username!r}; "
            f"only that account's records can be added to."
        )
    plan = plan_for_revision(upload.revision_id)
    candidates = worksheets_a_revision_sent(plan)
    expected = expected_wells(_whole_worksheet(plan))
    payloads = _payloads_by_well(upload)

    files: list[tuple[str, str]] = []
    sources: list[tuple[str, str]] = []
    warnings: list[str] = []
    for path in _shifter_paths(upload.revision_id, store):
        try:
            text = read_file(path)
        except OSError as error:
            sources.append((path, "cannot be read"))
            warnings.append(f"{path} cannot be read ({error.strerror or error}).")
            continue
        if not file_is_one_of(candidates, text):
            # Not refused outright: a worksheet saved twice, or one the
            # instrument has not touched yet, both land here.
            sources.append((path, "not a worksheet of this revision"))
            continue
        sources.append((path, "read"))
        files.append((path, text))

    report = collect_harvest(expected, tuple(files))
    records = tuple(labworks(experiment_id))
    rows = plan_harvest_records(expected, report, records, payloads)
    return HarvestReview(
        experiment_id, upload.revision_id, upload.username, rows, report,
        tuple(sources), tuple(warnings) + report.notes,
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


def _shifter_paths(revision_id: str, store: HarvestReviewPort) -> tuple[str, ...]:
    """Every SHIFTER file this revision was saved to, newest save first.

    Both stations are listed, and every save of them, because the operator
    loads whichever one is in front of them and a second save is a legitimate
    copy of the same worksheet. Which file was used is answered by its
    contents, not by its name or its age.
    """
    paths: list[str] = []
    for event in sorted(
        store.list_worksheet_exports(revision_id),
        key=lambda event: event.exported_at, reverse=True,
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
    """
    try:
        payloads = json.loads(upload.payload_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payloads, list):
        return {}
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        key = (str(payload.get("plate_code") or ""), str(payload.get("plate_well") or ""))
        if all(key):
            found[key] = payload
    return found


def _whole_worksheet(plan: Any):
    from ..domain.worksheets import build_shifter_worksheet

    return build_shifter_worksheet(plan)
