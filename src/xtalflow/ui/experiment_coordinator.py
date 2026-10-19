"""One experiment at a time: its editor, guided steps, drafts and delivery.

The window owns the image review; this owns the planning half of the same
window. It reaches back through ``self.window`` for what the two share — the
project controller, the review on screen, the status bar and the dock of
targets — and holds the editors, the planning service and the MxLive account
itself.
"""

from __future__ import annotations

import getpass
import json
import re
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QWidget

from xtalflow.application import ReviewPersistenceError
from xtalflow.application.project_controller import TargetValidationIssue
from xtalflow.domain.plate_format import plate_format_by_id
from xtalflow.domain import (
    CrystalSelection,
    PlanType,
    crystal_selection_from_selected_crystals,
)
from xtalflow.domain.fragment_screening import FragmentLibrary
from xtalflow.domain.experiment_naming import suggest_experiment_id
from xtalflow.domain.condition_test import TEMPLATES as CONDITION_TEMPLATES
from xtalflow.domain.condition_test import ConditionTestPlan, format_minutes
from xtalflow.domain.plan_lifecycle import (
    PlanningDraft,
    PlanRevision,
    WebDBUploadEvent,
)
from xtalflow.domain.instruments import WorksheetKind
from xtalflow.domain.mxlive import MxLiveReadError
from xtalflow.application.experiment_workflow import (
    STEP_LABELS,
    ExperimentFacts,
    ExperimentProgress,
    ExperimentStatus,
    StepState,
    WorkflowStep,
    evaluate_experiment,
    experiment_progress,
    steps_for,
)
from xtalflow.application.planning_service import (
    EXPERIMENT_ID_PREFIXES,
    PlanningService,
    PlanStatus,
    plan_from_snapshot,
    saved_plan_status,
)
from xtalflow.application.worksheet_export import (
    CANCELLED as WORKSHEETS_CANCELLED,
    FAILED as WORKSHEETS_FAILED,
    SUCCEEDED as WORKSHEETS_SUCCEEDED,
    WorksheetExportService,
)
from xtalflow.application.labwork_upload import (
    FAILED,
    PARTIAL,
    SUCCEEDED,
    LabworkUploadService,
    UploadAvailability,
    UploadLockState,
    send_labworks,
)
from xtalflow.application.beamline_history import past_experiments
from xtalflow.domain.mxlive import (
    BeamlineProgress,
    MxLiveReadError,
    beamline_progress,
)
from xtalflow.infrastructure import (
    LegacyMxLiveReadClient,
    LegacyMxLiveWriteClient,
)
from xtalflow.infrastructure.mxlive_client import labworks_endpoint
from xtalflow.infrastructure.fragment_library_csv import (
    FragmentLibraryCsvError,
    load_fragment_library,
)
from xtalflow.infrastructure.worksheet_exporter import (
    InstrumentLocationUnavailable,
    WorksheetDestinationUnavailable,
    WorksheetExporter,
    pending_root,
    worksheets_for,
)
from xtalflow.infrastructure.source_plate_record import (
    SourcePlateRecord,
    merge_held,
    record_directory,
)
from xtalflow.infrastructure.workspace_store import WORKSPACE_REVIEW
from xtalflow.infrastructure.mxlive_config import (
    MxLiveConfigurationError,
    resolve_mxlive_account,
)
from xtalflow.text import counted
from xtalflow.ui.plan_editors import (
    FragmentScreeningEditor,
    PlanEditorBase,
    RawCrystalEditor,
)
from xtalflow.ui.experiment_steps import SetupStep, WorksheetsStep
from xtalflow.ui.condition_test_editor import ConditionTestEditor
from xtalflow.ui.home_page import (
    EXPERIMENT_CHOICES,
    PLAN_TYPE_TITLES,
    RecentExperiment,
)
from xtalflow.ui import theme
from xtalflow.ui.errors import IMAGE_SOURCE_ERRORS, STORE_ERRORS


STEP_HINTS = {
    WorkflowStep.SETUP: "",
    WorkflowStep.SELECT_WELLS: "",
    WorkflowStep.CONDITIONS: "",
    WorkflowStep.REVIEW: "Finalizing fixes this revision for the worksheets; nothing runs yet.",
    WorkflowStep.WORKSHEETS: "",
}


# One read covers a year of labworks; experiments older than this are not
# looked up, and a read is not repeated while its answer is this fresh.
MXLIVE_YEARS = 2
MXLIVE_CHECK_SECONDS = 300.0
# MxLive at BL-5C has nothing before this, and a wrong year is a wasted GET.
MXLIVE_EARLIEST_YEAR = 2015


def _recent_years(count: int) -> list[int]:
    this_year = datetime.now().year
    return [this_year - offset for offset in range(count)]


def _year_list(years: list[int]) -> str:
    return ", ".join(str(year) for year in sorted(years, reverse=True))

_EXPERIMENT_YEAR = re.compile(r"-(\d{4})\d{2}-")


def _years_of(experiment_ids) -> set[int]:
    """The years in experiment IDs such as FragSC-202607-BRD4-01."""
    found = (_EXPERIMENT_YEAR.search(item or "") for item in experiment_ids)
    return {int(match.group(1)) for match in found if match is not None}


def _when(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _what_the_wells_need(summaries) -> str:
    """Name what the blocked wells need, not just how many there are.

    A count alone cannot be acted on, and the two reasons are not fixed the
    same way: a missing boundary has to be drawn, an unconfirmed one
    confirmed.
    """
    missing = {
        summary.image.image_key for summary in summaries
        if TargetValidationIssue.CALIBRATION_MISSING in summary.validation_issues
    }
    unconfirmed = {
        summary.image.image_key for summary in summaries
        if TargetValidationIssue.CALIBRATION_UNCONFIRMED in summary.validation_issues
    }
    reasons = []
    if missing:
        reasons.append(f"{counted(len(missing), 'well')} without a boundary")
    if unconfirmed:
        reasons.append(f"{counted(len(unconfirmed), 'boundary')} to confirm")
    return " · ".join(reasons)


def _outside_warning(summaries, image_sets) -> str:
    """Name the wells holding a position beyond their boundary, and how far.

    A warning, not a bar: the detected outline is often smaller than the well,
    so whether the position is where it was meant to be is something only the
    person looking at the image can say. Naming the well saves hunting for it.
    """
    outside = [
        summary for summary in summaries
        if TargetValidationIssue.OUTSIDE_WELL in summary.validation_issues
    ]
    if not outside:
        return ""
    wells = []
    beyond_mm = 0.0
    for summary in outside:
        wells.append(_well_address(summary, image_sets))
        calibration = summary.calibration
        if calibration is not None:
            beyond_mm = max(
                beyond_mm,
                calibration.distance_beyond_mm(
                    summary.target.x_px, summary.target.y_px
                ),
            )
    named = ", ".join(dict.fromkeys(wells))
    return (
        f"{counted(len(outside), 'position')} outside the boundary "
        f"(up to {beyond_mm:.2f} mm) · {named}"
    )


def _well_address(summary, image_sets) -> str:
    """The well as the user reads it, or the RockMaker numbers if it cannot be."""
    image_set = image_sets.get(summary.image_set_id)
    plate_format = (
        plate_format_by_id(image_set.plate_format_id, image_set.plate_format_version)
        if image_set is not None else None
    )
    if plate_format is None:
        return f"{summary.image.well_number}/d{summary.image.drop_number}"
    return str(
        plate_format.address_for(
            summary.image.well_number, summary.image.drop_number
        )
    )



class ExperimentCoordinator:
    """Every experiment open in the window, and the steps each one is walking."""

    def __init__(self, window) -> None:
        self.window = window
        review_store = window.review_store
        self.planning_service = (
            PlanningService(review_store.planning) if review_store is not None else None
        )
        self.upload_service = (
            LabworkUploadService(review_store.audit) if review_store is not None else None
        )
        try:
            self.mxlive_account = resolve_mxlive_account(
                window.settings.mxlive_config_path,
                base_url=window.settings.mxlive_base_url,
                beamline=window.settings.mxlive_beamline,
                key_path=window.settings.mxlive_key_path,
                ca_bundle=window.settings.mxlive_ca_bundle,
            )
            self.mxlive_configuration_error: str | None = None
        except MxLiveConfigurationError as error:
            self.mxlive_account = None
            self.mxlive_configuration_error = str(error)
        self._mxlive_experiment_ids: set[str] = set()
        self._editors: dict[str, PlanEditorBase] = {}
        # What the beamline did with each uploaded experiment, read from MxLive
        # in the background. Empty until the first read comes back, and after a
        # read that failed: the list never waits for it.
        self._beamline: dict[str, BeamlineProgress] = {}
        self._beamline_years: set[int] = set()
        self._beamline_checked_at = 0.0
        self._beamline_busy = False
        # One year of labworks read once, shared by the stage in the list and
        # the history page. A year older than the two the list needs is read
        # only when somebody asks for it there.
        self._labworks: dict[int, tuple] = {}
        self.current_editor: PlanEditorBase | None = None
        self._current_step: WorkflowStep | None = None
        self._alternate_worksheet_root: Path | None = None
        # Experiments not on screen keep their editors here.
        self.editor_holder = QWidget(window)
        self.editor_holder.hide()

    def _show_planning_migration_status(self) -> None:
        if self.window.review_store is None:
            return
        report = self.window.review_store.planning_project_migration
        details: list[str] = []
        if report.migrated:
            details.append(
                f"Migrated {report.migrated} finalized legacy project(s)"
            )
        if report.legacy_drafts_without_revision:
            details.append(
                f"{report.legacy_drafts_without_revision} legacy draft(s) still "
                "need selection review"
            )
        if report.invalid_snapshots:
            details.append(
                f"{len(report.invalid_snapshots)} invalid finalized snapshot(s) "
                "left unchanged"
            )
            details.extend(
                f"{plan_id}: {error}"
                for plan_id, error in report.invalid_snapshots
            )
        if self.window.review_store.upgrade_backup_path is not None:
            details.insert(
                0,
                "Review database upgraded · backup saved to "
                f"{self.window.review_store.upgrade_backup_path}",
            )
        if details:
            self.window.status_message_label.show_message(" · ".join(details))

    def show_home(self) -> None:
        editor = self.current_editor
        if editor is not None and self.window.pages.currentWidget() is self.window.experiment_page:
            # Return to the folder the experiment belongs to.
            self.window._home_workspace_id = editor.project_id
            if self._current_step is WorkflowStep.SELECT_WELLS:
                self.window._selection_sync_timer.stop()
                self._sync_editor_selection(editor)
            self._leave_experiment(editor)
        self._set_target_summary_available(False)
        recent = self._recent_experiments()
        self.window.home_page.show_recent_work(recent)
        self.window._refresh_home_workspaces(recent)
        self.window.pages.setCurrentWidget(self.window.home_page)
        self.window.setWindowTitle("XtalFlow")
        self._check_beamline()

    def _recent_experiments(self) -> tuple[RecentExperiment, ...]:
        workspaces = {
            project.id: project.name for project in self.window.project_controller.visible_projects
        }
        if self.window.review_store is None:
            drafts = tuple(self._draft_from_editor(editor) for editor in self._editors.values())
        else:
            try:
                drafts = self.window.review_store.planning.load_recent_drafts(limit=1000)
            except ReviewPersistenceError as error:
                self.window.status_message_label.show_message(f"Recent work unavailable: {error}")
                return ()
        locks = self._upload_locks()
        self._beamline_years = _years_of(
            experiment_id for experiment_id, lock in locks.items()
            if lock.availability is UploadAvailability.UPLOADED
        )
        exports = self._worksheet_exports()
        revisions = self._revision_numbers()
        recent = []
        for draft in drafts:
            if draft.project_id not in workspaces:
                continue
            try:
                plan_type = PlanType(draft.plan_type)
            except ValueError:
                continue
            progress = self._recent_status(draft, locks, exports, revisions)
            recent.append(
                RecentExperiment(
                    draft.project_id, workspaces[draft.project_id], draft.id,
                    draft.name, plan_type, progress.headline,
                    draft.updated_at, draft.created_at,
                    progress.state, progress.detail,
                )
            )
        return tuple(recent)

    def _check_beamline(self) -> None:
        """Ask MxLive what the beamline did, without making the list wait.

        Only uploaded experiments can have records, and one read covers a
        whole year. A read that fails leaves the stages as they are and is
        not tried again for a while: the list is useful without it.
        """
        account = self.mxlive_account
        years = sorted(self._beamline_years, reverse=True)[:MXLIVE_YEARS]
        if (
            not years or self._beamline_busy or account is None
            or not account.upload_ready
            or time.monotonic() - self._beamline_checked_at < MXLIVE_CHECK_SECONDS
        ):
            return
        reader = self._mxlive_reader()
        outcome: dict[str, object] = {}

        self._read_labworks(years)

    def _read_labworks(self, years: list[int]) -> None:
        """Read whole years of labworks on a worker thread, once each."""
        reader = self._mxlive_reader()
        outcome: dict[str, object] = {}

        def work() -> None:
            try:
                outcome["years"] = {
                    year: tuple(reader.labworks(str(year))) for year in years
                }
            except (MxLiveReadError, ValueError, OSError) as error:
                outcome["error"] = error

        self._beamline_busy = True
        self._beamline_checked_at = time.monotonic()
        self._show_history_status(f"Reading {_year_list(years)} from MxLive…")
        worker = threading.Thread(target=work, name="xtalflow-mxlive", daemon=True)
        poll = QTimer(self.window)
        poll.setInterval(200)
        poll.timeout.connect(
            lambda: None if worker.is_alive() else self._beamline_read(poll, outcome)
        )
        worker.start()
        poll.start()

    def _beamline_read(self, poll: QTimer, outcome: dict) -> None:
        poll.stop()
        self._beamline_busy = False
        if "error" in outcome:
            message = f"Beamline progress unavailable: {outcome['error']}"
            self.window.status_message_label.show_message(message, 6000)
            self._show_history_status(str(outcome["error"]), "attention")
            return
        self._show_history_status("")
        self._labworks.update(outcome.get("years") or {})
        progress = beamline_progress(
            item for year in sorted(self._beamline_years, reverse=True)[:MXLIVE_YEARS]
            for item in self._labworks.get(year, ())
        )
        changed = progress != self._beamline
        self._beamline = progress
        current = self.window.pages.currentWidget()
        # Only these two screens show anything that came from MxLive.
        if changed and current is self.window.home_page:
            self.window.home_page.show_recent_work(self._recent_experiments())
        if current is self.window.mxlive_history_page:
            self._show_history()

    def _show_history_status(self, message: str, kind: str = "muted") -> None:
        if self.window.pages.currentWidget() is self.window.mxlive_history_page:
            self.window.mxlive_history_page.show_status(message, kind)

    def show_mxlive_history(self) -> None:
        """The third place to stand: what MxLive holds for this account."""
        account = self.mxlive_account
        page = self.window.mxlive_history_page
        page.show_account(
            account.username if account else "",
            account.beamline if account else "",
        )
        self.window.pages.setCurrentWidget(page)
        self.window.setWindowTitle("XtalFlow · MxLive history")
        self._show_history()
        if account is None or not account.upload_ready:
            page.show_status(
                "No MxLive key is configured, so nothing can be read.", "attention"
            )
            return
        if not self._labworks:
            self._read_labworks(_recent_years(MXLIVE_YEARS))
        elif self._beamline_busy:
            page.show_status("Reading from MxLive…")

    def refresh_mxlive_history(self) -> None:
        """Ask again for the years already on the page."""
        if self._beamline_busy or self.mxlive_account is None:
            return
        self._beamline_checked_at = 0.0
        self._read_labworks(sorted(self._labworks) or _recent_years(MXLIVE_YEARS))

    def load_earlier_mxlive_history(self) -> None:
        """One more year back, which is one more GET."""
        if self._beamline_busy or self.mxlive_account is None:
            return
        earliest = min(self._labworks, default=_recent_years(1)[0])
        if earliest <= MXLIVE_EARLIEST_YEAR:
            self._show_history_status(
                f"MxLive is not read before {MXLIVE_EARLIEST_YEAR}.", "attention"
            )
            return
        self._read_labworks([earliest - 1])

    def _show_history(self) -> None:
        page = self.window.mxlive_history_page
        years = tuple(sorted(self._labworks))
        page.show_years(years, min(years, default=0) > MXLIVE_EARLIEST_YEAR)
        page.show_experiments(past_experiments(
            item for year in years for item in self._labworks[year]
        ))

    def _upload_locks(self) -> dict[str, UploadLockState]:
        """Read the upload history of every experiment once, not row by row."""
        if self.upload_service is None:
            return {}
        try:
            return self.upload_service.lock_states()
        except ReviewPersistenceError as error:
            self.window.status_message_label.show_message(
                f"Upload history unavailable: {error}"
            )
            return {}

    def _worksheet_exports(self) -> dict[str, tuple[tuple[int, object], ...]]:
        """Every plan's save attempts, with the revision each one was for."""
        if self.window.review_store is None:
            return {}
        try:
            return self.window.review_store.audit.list_worksheet_exports_by_plan()
        except ReviewPersistenceError as error:
            self.window.status_message_label.show_message(
                f"Worksheet history unavailable: {error}"
            )
            return {}

    def _revision_numbers(self) -> dict[str, int]:
        """Which revision each stored id is, so an older upload can be named."""
        if self.window.review_store is None:
            return {}
        try:
            return self.window.review_store.planning.revision_numbers()
        except ReviewPersistenceError:
            return {}

    def _recent_status(
        self,
        draft: PlanningDraft,
        locks: dict[str, UploadLockState],
        exports: dict[str, tuple[tuple[int, object], ...]],
        revisions: dict[str, int],
    ) -> ExperimentProgress:
        revision = None
        if self.planning_service is not None:
            try:
                revision = self.planning_service.latest_revision(draft.id)
            except ReviewPersistenceError:
                revision = None
        try:
            step = WorkflowStep(draft.workflow_step)
        except ValueError:
            step = WorkflowStep.SETUP
        started = f"Started {_when(draft.created_at)}."
        if revision is None:
            progress = experiment_progress(step, None)
            return replace(progress, detail=f"{progress.detail}\n\n{started}")
        attempts = exports.get(draft.id, ())
        # The files an instrument holds are from the last save that worked,
        # whichever revision it was for, and a later failure does not undo it.
        delivered = next(
            (item for item in reversed(attempts) if item[1].status == WORKSHEETS_SUCCEEDED),
            None,
        )
        saved = delivered is not None and delivered[0] == revision.revision
        older = delivered[0] if delivered is not None and not saved else None
        last = attempts[-1] if attempts else None
        expected, missing = self._instrument_coverage(delivered[1] if saved else None)
        lock = locks.get(revision.experiment_id or "")
        uploaded_revision = (
            revisions.get(lock.event.revision_id)
            if lock is not None and lock.event is not None else None
        )
        progress = experiment_progress(
            step,
            revision.revision,
            worksheets_saved=saved,
            worksheets_saved_elsewhere=saved and self._saved_elsewhere(delivered[1]),
            instruments_missing=missing,
            instruments_expected=expected,
            save_failed=(
                not saved and last is not None and last[0] == revision.revision
                and last[1].status == WORKSHEETS_FAILED
            ),
            instruments_have_revision=older,
            upload=lock.availability if lock is not None else None,
            uploaded_revision=uploaded_revision,
            beamline=self._beamline.get(revision.experiment_id or ""),
        )
        return replace(
            progress,
            detail="\n\n".join(
                (progress.detail, "\n".join(self._stage_history(
                    started, revision, delivered, lock, missing, uploaded_revision
                )))
            ),
        )

    def _instrument_coverage(self, export) -> tuple[int, tuple[str, ...]]:
        """How many instruments read these worksheets, and which got none.

        The worksheet kinds the plan writes are read back from what was
        delivered: a save reaches at least one instrument of every kind it
        writes for, or it does not happen at all.
        """
        if export is None:
            return 0, ()
        delivered = {
            output.instrument for output in export.outputs
            if self._on_its_instrument(output)
        }
        kinds = {
            destination.worksheet
            for destination in map(self.window.settings.instrument, delivered)
            if destination is not None
        }
        expected = [
            item for item in self.window.settings.instruments if item.worksheet in kinds
        ]
        return len(expected), tuple(
            item.label for item in expected if item.instrument not in delivered
        )

    def _stage_history(
        self, started, revision, delivered, lock, missing=(), uploaded_revision=None
    ) -> list[str]:
        """When each stage happened, and which instruments the files reached."""
        history = [started, f"Finalized r{revision.revision} {_when(revision.finalized_at)}."]
        if delivered is not None:
            number, export = delivered
            where = ", ".join(
                self._instrument_label(output.instrument) for output in export.outputs
            )
            history.append(
                f"Worksheets r{number} saved {_when(export.exported_at)}"
                + (f" \u00b7 {where}." if where else ".")
            )
            if missing:
                history.append(f"No worksheet reached {', '.join(missing)}.")
        if lock is not None and lock.event is not None:
            number = f"r{uploaded_revision} " if uploaded_revision is not None else ""
            history.append(f"Sent {number}to MxLive {_when(lock.event.attempted_at)}.")
        return history

    def _instrument_label(self, instrument: str) -> str:
        destination = self.window.settings.instrument(instrument)
        return destination.label if destination is not None else instrument

    def _experiment_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        if self.window.review_store is not None:
            try:
                names = {
                    draft.id: draft.name
                    for draft in self.window.review_store.planning.load_recent_drafts(limit=10000)
                }
            except ReviewPersistenceError:
                names = {}
        names.update({plan_id: editor.plan_name for plan_id, editor in self._editors.items()})
        return names

    def start_experiment_choice(self, key: str):
        choice = next(item for item in EXPERIMENT_CHOICES if item.key == key)
        return self.start_experiment(choice.plan_type, template=choice.template)

    def start_experiment(
        self, plan_type: PlanType, name: str | None = None, template: str | None = None
    ):
        """Create an empty experiment in the chosen workspace and open its first step."""
        target = self.window._target_workspace()
        if target is not None and not self.window._open_workspace(target.id):
            return None
        if self.window.project_controller.active_project is None:
            try:
                self.window.project_controller.create_project("Untitled Workspace")
            except STORE_ERRORS as error:
                QMessageBox.warning(self.window, "Cannot start experiment", str(error))
                return None
            self.window._show_active_review()
        project = self.window.project_controller.active_project
        editor = self._create_editor(
            plan_type, str(uuid4()),
            name or self._default_experiment_name(plan_type, template),
            project.id, template=template,
        )
        self._offer_workspace_positions(editor)
        self._open_editor(editor, WorkflowStep.SETUP)
        editor.protein_input.setFocus(Qt.OtherFocusReason)
        return editor

    def _default_experiment_name(self, plan_type: PlanType, template: str | None = None) -> str:
        title = next(
            (choice.title for choice in EXPERIMENT_CHOICES
             if choice.plan_type is plan_type and choice.template == template),
            PLAN_TYPE_TITLES[plan_type],
        )
        stem = f"{title} {datetime.now():%Y-%m-%d}"
        existing = set(self._experiment_names().values())
        name, number = stem, 2
        while name in existing:
            name = f"{stem} #{number}"
            number += 1
        return name

    def _offer_workspace_positions(self, editor: PlanEditorBase) -> None:
        """Positions placed before experiments kept their own can seed a new one."""
        if self.window.review_store is None:
            return
        workspace = self.window.review_store.workspace
        try:
            count = workspace.unassigned_workspace_position_count(editor.project_id)
        except ReviewPersistenceError:
            return
        if not count:
            return
        if QMessageBox.question(
            self.window,
            "Use earlier positions?",
            f"This workspace has {counted(count, 'position')} placed before each "
            "experiment kept its own positions.\n\nUse them in this experiment? "
            "Otherwise it starts with no positions.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        try:
            workspace.adopt_workspace_positions(editor.project_id, editor.plan_id)
        except STORE_ERRORS as error:
            QMessageBox.warning(self.window, "Positions were not copied", str(error))

    def resume_experiment(self, workspace_id: str, plan_id: str):
        active = self.window.project_controller.active_project
        if active is None or active.id != workspace_id:
            self.window._open_project_and_show(workspace_id)
        editor = self._editors.get(plan_id)
        if editor is None:
            try:
                if self.window.review_store is None:
                    raise ValueError("this experiment is no longer open")
                draft = next(
                    (
                        item
                        for item in self.window.review_store.planning.load_planning_drafts(
                            workspace_id
                        )
                        if item.id == plan_id
                    ),
                    None,
                )
                if draft is None:
                    raise ValueError("this experiment no longer exists")
                editor = self._create_editor(
                    PlanType(draft.plan_type), draft.id, draft.name, workspace_id, draft
                )
            except STORE_ERRORS as error:
                QMessageBox.warning(self.window, "Cannot open experiment", str(error))
                self.show_home()
                return None
        try:
            step = WorkflowStep(editor.workflow_step)
        except ValueError:
            step = WorkflowStep.SETUP
        self._open_editor(editor, step)
        return editor

    def delete_experiment(self, workspace_id: str, plan_id: str) -> None:
        name = self._experiment_names().get(plan_id, "this experiment")
        if self.window.review_store is not None:
            try:
                has_uploads = self.window.review_store.planning.planning_plan_has_upload_history(
                    plan_id
                )
            except ReviewPersistenceError as error:
                QMessageBox.warning(self.window, "Cannot delete experiment", str(error))
                return
            if has_uploads:
                QMessageBox.information(
                    self.window, "Cannot delete experiment",
                    f"{name} has MxLive upload history, which must stay auditable.",
                )
                return
        if QMessageBox.question(
            self.window,
            "Delete experiment",
            f"Permanently delete '{name}' and its positions?\n\nWorksheet files "
            "already saved to instrument folders are not removed. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self._remove_experiment(plan_id)
        except IMAGE_SOURCE_ERRORS as error:
            QMessageBox.warning(self.window, "Cannot delete experiment", str(error))
            return
        self._refresh_project_well_usage()
        self.show_home()

    def delete_current_experiment(self) -> None:
        editor = self.current_editor
        if editor is None:
            return
        if not editor.persisted:
            self._remove_experiment(editor.plan_id)
            self.show_home()
            return
        self.delete_experiment(editor.project_id, editor.plan_id)

    def _remove_experiment(self, plan_id: str) -> None:
        """Delete an experiment's draft, revisions, and positions without asking."""
        editor = self._editors.get(plan_id)
        if editor is not None:
            editor.autosave_timer.stop()
        if self.window.project_controller.experiment_id == plan_id:
            # Save the open review into its own scope before that scope is removed.
            self.window.project_controller.open_experiment(WORKSPACE_REVIEW)
            self.window._show_active_review()
        if self.window.review_store is not None:
            self.window.review_store.planning.delete_planning_draft(plan_id)
            self.window.review_store.workspace.delete_experiment_positions(plan_id)
        if editor is not None:
            del self._editors[plan_id]
            if editor is self.current_editor:
                self.current_editor = None
                self._current_step = None
                self.window.experiment_page.set_pages({})
            editor.deleteLater()
    @staticmethod

    def _has_content(editor: PlanEditorBase) -> bool:
        """Whether a new experiment holds anything worth keeping as a draft."""
        return bool(
            editor.protein_input.text().strip()
            or editor.plan_name != editor.default_name
            or editor.selected_well_count
            or editor.last_revision is not None
            or editor.has_plan_specific_content()
        )

    def _leave_experiment(self, editor: PlanEditorBase) -> None:
        """Save a draft that has content; drop a new one that was only opened."""
        editor.autosave_timer.stop()
        if editor.persisted or self._has_content(editor):
            self._persist_draft(editor)
            return
        try:
            self._remove_experiment(editor.plan_id)
        except IMAGE_SOURCE_ERRORS:
            # Nothing was saved for it; leftover review marks are harmless.
            self._editors.pop(editor.plan_id, None)

    def _create_editor(
        self,
        plan_type: PlanType,
        plan_id: str,
        name: str,
        workspace_id: str,
        restored: PlanningDraft | None = None,
        template: str | None = None,
    ):
        """One experiment's plan state, lifecycle, and step pages."""
        if plan_type is PlanType.FRAGMENT_SCREENING:
            editor = FragmentScreeningEditor(None, None, self.editor_holder)
            editor.set_library_choices(self._fragment_library_choices())
            editor.refresh_libraries_button.setToolTip(
                str(self.window.settings.fragment_library_directory)
            )
            editor.library_refresh_requested.connect(
                self._refresh_fragment_library_choices
            )
        elif plan_type is PlanType.RAW_CRYSTAL:
            editor = RawCrystalEditor(None, self.editor_holder)
        elif plan_type is PlanType.CONDITION_TEST:
            editor = ConditionTestEditor(
                CONDITION_TEMPLATES[template or "solvent"](), self.editor_holder
            )
            self._merge_held_source_entries()
            editor.set_source_record(self._source_plate_record(), getpass.getuser())
            editor.conditions_requested.connect(
                lambda: self.go_to_step(WorkflowStep.CONDITIONS)
            )
        else:
            raise ValueError(f"{plan_type.value} experiments are not available yet")
        editor.hide()
        editor.plan_type = plan_type
        editor.plan_id = plan_id
        editor.project_id = workspace_id
        editor.plan_name = name
        editor.plan_created_at = (
            restored.created_at if restored else datetime.now(timezone.utc)
        )
        editor.workflow_step = (
            restored.workflow_step if restored and restored.workflow_step
            else WorkflowStep.SETUP.value
        )
        editor.persisted = restored is not None
        editor.default_name = name
        if restored is not None:
            editor.restore_draft(restored)
        editor.remember_opened_state()
        editor.set_title(name)
        editor.autosave_timer = QTimer(editor)
        editor.autosave_timer.setSingleShot(True)
        editor.autosave_timer.setInterval(750)
        editor.autosave_timer.timeout.connect(
            lambda selected_editor=editor: self._persist_draft(selected_editor)
        )
        editor.set_experiment_id_provider(
            lambda protein, selected_type=plan_type: self._suggest_experiment_id(
                selected_type, protein
            )
        )
        if self.mxlive_account is not None:
            editor.set_mxlive_account(self.mxlive_account)
        elif self.mxlive_configuration_error:
            editor.webdb_status_label.setText(self.mxlive_configuration_error)
        editor.webdb_upload_requested.connect(
            lambda selected_editor=editor: self._upload_plan_labworks(selected_editor)
        )
        editor.draft_changed.connect(
            lambda selected_editor=editor: self._plan_draft_changed(selected_editor)
        )
        editor.name_input.textEdited.connect(
            lambda text, selected_editor=editor: self._rename_experiment(
                selected_editor, text
            )
        )
        if restored is not None and self.planning_service is not None:
            editor.last_revision = self.planning_service.latest_revision(plan_id)
            if editor.last_revision is not None:
                editor.last_revision_snapshot = editor.last_revision.snapshot_json
        editor.set_destinations(
            ", ".join(item.label for item in self._instruments_for(plan_type))
            or "no configured instruments"
        )
        editor.setup_step = SetupStep(editor, PLAN_TYPE_TITLES[plan_type])
        editor.worksheets_step = WorksheetsStep(editor)
        editor.worksheets_step.retry_button.clicked.connect(
            lambda _=False, selected_editor=editor: self._save_plan_worksheets(
                selected_editor
            )
        )
        editor.worksheets_step.choose_location_button.clicked.connect(
            lambda _=False, selected_editor=editor: self._save_worksheets_elsewhere(
                selected_editor
            )
        )
        editor.step_pages = {
            WorkflowStep.SETUP: editor.setup_step,
            WorkflowStep.SELECT_WELLS: self.window.select_wells_page,
            WorkflowStep.REVIEW: editor.review_widget(),
            WorkflowStep.WORKSHEETS: editor.worksheets_step,
        }
        conditions = editor.conditions_widget()
        if conditions is not None:
            editor.step_pages[WorkflowStep.CONDITIONS] = conditions
        self._editors[plan_id] = editor
        return editor

    def _open_editor(self, editor: PlanEditorBase, step: WorkflowStep) -> None:
        previous = self.current_editor
        if previous is not None and previous is not editor and previous.plan_id in self._editors:
            self._leave_experiment(previous)
        self.current_editor = editor
        self._current_step = None
        try:
            self.window.project_controller.open_experiment(editor.plan_id)
        except IMAGE_SOURCE_ERRORS as error:
            self.window._show_active_review()
            self.window._show_images_unavailable(error)
        else:
            self.window._show_active_review()
        workspace = next(
            (
                item for item in self.window.project_controller.projects
                if item.id == editor.project_id
            ),
            None,
        )
        editor.setup_step.workspace_label.setText(
            workspace.name if workspace is not None else "Unknown workspace"
        )
        editor.setup_step.refresh()
        self.window.experiment_page.set_pages(editor.step_pages)
        self.window.pages.setCurrentWidget(self.window.experiment_page)
        # The panel highlights the folder this experiment belongs to.
        self.window._home_workspace_id = editor.project_id
        self.window._refresh_home_workspaces()
        self.window.setWindowTitle(f"{editor.plan_name} · XtalFlow")
        self._sync_editor_selection(editor)
        self._show_plan_status(
            editor,
            saved_plan_status(
                editor.last_revision, editor.last_revision_snapshot,
                self._plan_snapshot(editor),
            ),
        )
        self._sync_webdb_upload_state(editor)
        self.go_to_step(step)

    def go_to_step(self, step: WorkflowStep) -> None:
        editor = self.current_editor
        if editor is None:
            return
        steps = steps_for(editor.plan_type)
        if step not in steps:
            step = steps[0]
        if (
            self._current_step is WorkflowStep.SELECT_WELLS
            and step is not WorkflowStep.SELECT_WELLS
        ):
            self.window._selection_sync_timer.stop()
            self._sync_editor_selection(editor)
        self._current_step = step
        self._set_target_summary_available(step is WorkflowStep.SELECT_WELLS)
        self.window.experiment_page.show_step(step, STEP_HINTS[step])
        if step is WorkflowStep.WORKSHEETS:
            self._refresh_worksheets_step(editor)
        elif step is WorkflowStep.SETUP:
            editor.setup_step.refresh()
            editor.setup_step.focus_first_field()
        elif step is WorkflowStep.SELECT_WELLS:
            self.window.image_canvas.setFocus(Qt.OtherFocusReason)
        if editor.workflow_step != step.value:
            editor.workflow_step = step.value
            self._persist_draft(editor)
        self._refresh_experiment_status(editor)
        if step is WorkflowStep.CONDITIONS:
            # Arriving here with wells chosen is the moment a volume that no
            # longer divides can be acted on, and the first one that is not
            # in the middle of clicking crystals.
            check = getattr(editor, "check_volume_splits", None)
            if check is not None:
                check()

    def _go_back(self) -> None:
        editor = self.current_editor
        if editor is None or self._current_step is None:
            return
        steps = steps_for(editor.plan_type)
        index = steps.index(self._current_step)
        if index:
            self.go_to_step(steps[index - 1])

    def _primary_action(self) -> None:
        editor = self.current_editor
        step = self._current_step
        if editor is None or step is None:
            return
        status = editor.experiment_status or evaluate_experiment(
            self._experiment_facts(editor)
        )
        if step is WorkflowStep.REVIEW:
            if status.ready_to_finalize and self._finalize_and_continue(editor) is None:
                return
            self.go_to_step(WorkflowStep.WORKSHEETS)
        elif step is WorkflowStep.WORKSHEETS:
            if status.status_of(WorkflowStep.WORKSHEETS).state is StepState.COMPLETE:
                self.show_home()
            else:
                self._save_plan_worksheets(editor)
        elif step is WorkflowStep.SELECT_WELLS and editor.wells_needing_attention:
            self._review_target_warnings()
        elif (
            step is WorkflowStep.CONDITIONS
            and editor.plan_type is PlanType.CONDITION_TEST
            and self._condition_wells_ready(editor)
        ):
            self.go_to_step(WorkflowStep.REVIEW)
        else:
            steps = steps_for(editor.plan_type)
            self.go_to_step(steps[steps.index(step) + 1])

    def _primary_for(
        self, editor, status: ExperimentStatus, step: WorkflowStep, steps
    ) -> tuple[str, bool]:
        """The next action, named for what it does, and whether it can run now."""
        state = status.status_of(step).state
        if step is WorkflowStep.SETUP:
            next_step = steps[steps.index(step) + 1]
            return (
                "Set conditions" if next_step is WorkflowStep.CONDITIONS else "Select wells",
                state is StepState.COMPLETE,
            )
        if step is WorkflowStep.SELECT_WELLS:
            if editor.wells_needing_attention:
                return f"Check {counted(editor.wells_needing_attention, 'well')}", True
            if editor.wells_unusable:
                # There is nothing in the dock to check, so do not offer to
                # go there: the footer carries the reason instead.
                return f"Use {counted(editor.selected_well_count, 'well')}", False
            if editor.selected_well_count:
                return f"Use {counted(editor.selected_well_count, 'well')}", True
            return "Use wells", False
        if step is WorkflowStep.CONDITIONS:
            if editor.plan_type is PlanType.CONDITION_TEST:
                required = editor.design.required_crystals
                selected = editor.selected_well_count
                if self._condition_wells_ready(editor):
                    return "Review assignments", state is StepState.COMPLETE
                if selected and selected < required:
                    more = required - selected
                    return f"Select {more} more well{'' if more == 1 else 's'}", (
                        state is StepState.COMPLETE
                    )
                return f"Select {counted(required, 'well')}", state is StepState.COMPLETE
            if steps[steps.index(step) + 1] is WorkflowStep.SELECT_WELLS:
                return "Select wells", state is StepState.COMPLETE
            return "Review assignments", state is StepState.COMPLETE
        if step is WorkflowStep.REVIEW:
            revision = editor.last_revision
            next_revision = revision.revision + 1 if revision is not None else 1
            if status.ready_to_finalize:
                return f"Finalize r{next_revision}", True
            if state is StepState.COMPLETE:
                return "Prepare worksheets", True
            return f"Finalize r{next_revision}", False
        if state is StepState.COMPLETE:
            return "Done", True
        finalized = status.status_of(WorkflowStep.REVIEW).state is StepState.COMPLETE
        return "Save worksheets", finalized
    @staticmethod

    def _condition_wells_ready(editor) -> bool:
        """Enough checked wells are already selected, so the next look is the assignments."""
        required = editor.design.required_crystals
        return bool(
            required
            and editor.selected_well_count >= required
            and not editor.wells_needing_attention
        )
    @staticmethod

    def _condition_well_note(editor) -> str:
        """Selected wells against the required count; spare wells are named as unused."""
        required = editor.design.required_crystals
        selected = editor.selected_well_count
        if not selected:
            return "No wells selected yet"
        text = f"Selected {counted(selected, 'well')}"
        if selected > required:
            spare = selected - required
            return f"{text} · {spare} will remain unused"
        if selected < required:
            return f"{text} · {required - selected} more needed"
        return f"{text} · all will be used"

    def _finalize_and_continue(self, editor: PlanEditorBase) -> PlanRevision | None:
        experiment_id = (
            editor.assigned_experiment_id or editor.current_experiment_id
            or "assigned when finalized"
        )
        instruments = ", ".join(
            destination.label for destination in self._instruments_for(editor.plan_type)
        )
        if QMessageBox.question(
            self.window,
            "Finalize experiment",
            f"Finalize {editor.plan_name}?\n\n"
            f"Experiment ID: {experiment_id}\n"
            f"{counted(editor.selected_well_count, 'well')} · "
            f"{counted(editor.selected_position_count, 'position')}\n"
            f"Worksheets for: {instruments or 'no configured instruments'}\n\n"
            "Finalizing fixes what the worksheets will contain. It does not start "
            "the experiment; you can still change the plan and finalize again.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return None
        return self._finalize_plan(editor)

    def _rename_experiment(self, editor: PlanEditorBase, text: str) -> None:
        name = text.strip()
        if not name:
            return
        editor.plan_name = name
        if editor is self.current_editor:
            self.window.setWindowTitle(f"{name} · XtalFlow")

    def _instruments_for(self, plan_type: PlanType):
        kinds = {WorksheetKind.SHIFTER}
        if plan_type in (PlanType.FRAGMENT_SCREENING, PlanType.CONDITION_TEST):
            kinds.add(WorksheetKind.ECHO)
        return tuple(item for item in self.window.settings.instruments if item.worksheet in kinds)

    def _experiment_facts(self, editor: PlanEditorBase) -> ExperimentFacts:
        revision = editor.last_revision
        return editor.facts(
            self._worksheets_saved(revision),
            self._worksheets_saved_elsewhere(revision),
        )

    def _refresh_experiment_status(self, editor: PlanEditorBase) -> None:
        status = evaluate_experiment(self._experiment_facts(editor))
        editor.experiment_status = status
        step = self._current_step
        if editor is not self.current_editor or step is None:
            return
        steps = steps_for(editor.plan_type)
        page = self.window.experiment_page
        workspace = next(
            (
                item for item in self.window.project_controller.projects
                if item.id == editor.project_id
            ),
            None,
        )
        page.show_identity(
            editor.plan_name, PLAN_TYPE_TITLES[editor.plan_type],
            editor.lifecycle_label.text(),
            workspace.name if workspace is not None else "",
        )
        page.show_progress(status.steps, step, compact=self.window.width() < 1360)
        current = status.status_of(step)
        symbol, kind = {
            StepState.COMPLETE: (theme.SYMBOL_OK, "ok"),
            StepState.WARNING: (theme.SYMBOL_ATTENTION, "attention"),
            StepState.ATTENTION: (theme.SYMBOL_ATTENTION, "attention"),
            StepState.INCOMPLETE: ("", "muted"),
        }[current.state]
        text = f"{symbol} {current.message}".strip()
        first = status.first_unfinished
        if (
            step in (WorkflowStep.REVIEW, WorkflowStep.WORKSHEETS)
            and current.state is not StepState.COMPLETE
            and first is not step
            and steps.index(first) < steps.index(step)
        ):
            text += f" · {STEP_LABELS[first]}: {status.status_of(first).message}"
        primary_text, primary_enabled = self._primary_for(editor, status, step, steps)
        # Notes such as unused fragments matter when deciding to finalize.
        notes = (
            " · ".join(note for note in status.notes if note not in text)
            if step is WorkflowStep.REVIEW else ""
        )
        if step is WorkflowStep.CONDITIONS and editor.plan_type is PlanType.CONDITION_TEST:
            # Required comes from the design; selected is what Select wells holds now.
            notes = self._condition_well_note(editor)
        page.show_footer(
            (text, kind), notes, primary_text, primary_enabled,
            step is not steps[0],
        )

    def _sync_editor_selection(self, editor: PlanEditorBase, crystals=None) -> None:
        """Take the experiment's reviewed positions as its selected wells."""
        if crystals is None:
            try:
                summaries = self.window.project_controller.project_target_summaries()
            except IMAGE_SOURCE_ERRORS as error:
                # The wells are safe in the database; they just cannot be read
                # without the images, so the steps say so instead of "none".
                editor.images_unavailable = str(error)
                self.window.status_message_label.show_message(
                    f"Selected wells unavailable: {error}"
                )
                self._refresh_experiment_status(editor)
                return
            editor.images_unavailable = None
            attention = {
                summary.image.image_key for summary in summaries
                if summary.blocks_planning
            }
            editor.wells_attention_reason = _what_the_wells_need(summaries)
            project = self.window.project_controller.active_project
            editor.outside_positions_warning = _outside_warning(
                summaries,
                {item.id: item for item in project.image_sets} if project else {},
            )
            editor.selected_well_count = len(
                {summary.image.image_key for summary in summaries}
            )
            editor.selected_position_count = len(summaries)
            editor.wells_needing_attention = len(attention)
            crystals = ()
            editor.wells_unusable = None
            if summaries and not attention:
                try:
                    crystals = self.window.project_controller.selected_crystals_for_plan()
                except IMAGE_SOURCE_ERRORS as error:
                    # Every well is confirmed and in bounds; something else
                    # stops a plan being made. Counting them all as needing
                    # attention sent the user to a dock with nothing in it,
                    # and no amount of confirming moved a number that was
                    # never about confirming.
                    editor.wells_unusable = str(error)
                    self.window.status_message_label.show_message(str(error))
        else:
            editor.selected_well_count = len(crystals)
            editor.selected_position_count = sum(len(item.targets) for item in crystals)
            editor.wells_needing_attention = 0
            editor.wells_unusable = None
            editor.wells_attention_reason = ""
            editor.outside_positions_warning = ""
        signature = tuple(
            (
                crystal.image_key,
                tuple(
                    (target.target_id, str(target.x_mm), str(target.y_mm))
                    for target in crystal.targets
                ),
            )
            for crystal in crystals
        )
        if signature != editor.selection_signature:
            editor.selection_signature = signature
            selection = (
                crystal_selection_from_selected_crystals(
                    editor.plan_id, crystals, created_at=editor.plan_created_at
                )
                if crystals else None
            )
            editor.set_selection(selection)
            if selection is not None:
                self._save_selection_snapshot(editor, selection)
            self._set_editor_well_usage(editor)
        if isinstance(editor, FragmentScreeningEditor):
            editor.assignment_empty_label.setText(
                f"{theme.SYMBOL_ATTENTION} Check "
                f"{counted(editor.wells_needing_attention, 'well')} in Select wells "
                "to see the assignments."
                if editor.wells_needing_attention
                else "Select wells to see the assignments."
            )
        self._refresh_experiment_status(editor)

    def _sync_current_selection(self) -> None:
        if (
            self.current_editor is not None
            and self._current_step is WorkflowStep.SELECT_WELLS
        ):
            self._sync_editor_selection(self.current_editor)

    def _set_target_summary_available(self, available: bool) -> None:
        """The selected-wells table and image status belong to the Select wells step."""
        self.window.save_status_label.setVisible(available)
        self.window.image_path_status.setVisible(available)
        if available == self.window._target_summary_available:
            if not available:
                self.window.target_summary_dock.hide()
            return
        self.window._target_summary_available = available
        self.window.target_summary_action.setEnabled(available)
        if available:
            if self.window._target_summary_visible_in_review:
                self.window.target_summary_dock.show()
        else:
            self.window._target_summary_visible_in_review = (
                self.window.target_summary_dock.isVisible()
            )
            self.window.target_summary_dock.hide()

    def _refresh_worksheets_step(self, editor: PlanEditorBase) -> None:
        revision = editor.last_revision
        finalized = (
            revision is not None
            and self._plan_snapshot(editor) == editor.last_revision_snapshot
        )
        plan = editor.current_plan
        if finalized:
            try:
                plan = plan_from_snapshot(editor.plan_id, revision.snapshot_json)
            except ValueError:
                plan = None
        worksheets = worksheets_for(plan) if plan is not None else {}
        username = getpass.getuser()
        latest = self._latest_worksheet_export(revision) if finalized else None
        # The files on the instruments are from the last save that worked; an
        # attempt that failed afterwards did not take them away.
        delivered = self._successful_worksheet_export(revision) if finalized else None
        saved: dict[str, list[Path]] = {}
        if delivered is not None:
            for output in delivered.outputs:
                saved.setdefault(output.instrument, []).append(Path(output.path))
        unreachable = self._unreachable_instruments()
        away = unreachable if not finalized or not saved else {}
        # Instruments whose worksheet is written but still waiting for them,
        # because their folder could not be reached when it was saved.
        waiting = (
            {output.instrument for output in self._waiting_outputs(delivered)}
            if delivered is not None else set()
        )
        left_out = tuple(
            destination.instrument
            for destination in self._instruments_for(editor.plan_type)
            if destination.worksheet in worksheets and destination.instrument not in saved
        ) if saved else ()
        # Before saving, the folder each file will go to; after, the files themselves.
        rows = []
        for destination in self._instruments_for(editor.plan_type):
            files = worksheets.get(destination.worksheet, ())
            count = sum(len(item.rows) for item in files)
            kind = destination.worksheet.value.upper()
            paths = saved.get(destination.instrument, [])
            if len(paths) == 1:
                where = str(paths[0])
            elif paths:
                where = f"{paths[0].parent}/{', '.join(path.name for path in paths)}"
            elif destination.instrument in left_out:
                where = "not saved · the instrument could not be reached"
            elif destination.instrument in away:
                # Said before anybody presses save, not only afterwards.
                where = (
                    "not connected · will wait in "
                    f"{pending_root(self.window.settings) / destination.instrument}"
                )
            else:
                where = str(destination.output_directory / username)
            rows.append((
                destination.label,
                f"{kind} × {len(files)}" if len(files) > 1 else kind,
                str(count) if files else "—",
                where,
            ))
        editor.worksheets_step.show_instruments(tuple(rows))
        schedule: list[tuple[int, str]] = []
        if isinstance(plan, ConditionTestPlan):
            rounds = plan.dispense_rounds
            for index, (minute, additions) in enumerate(rounds, start=1):
                name = f"ECHO file _R{index}" if len(rounds) > 1 else "ECHO file"
                schedule.append((minute, f"dispense with the {name} ({counted(len(additions), 'well')})"))
            for minute, group in plan.harvest_groups:
                schedule.append((minute, f"harvest {', '.join(item.selected_well.well_address for item in group)}"))
        schedule.sort(key=lambda item: item[0])
        preparation = ()
        if isinstance(plan, ConditionTestPlan):
            allocations = plan.source_allocations()
            preparation = (
                f"Crystal drops · {len(plan.assignments)}, one per well",
                *(
                    f"{additive.name} · {additive.source_plate} "
                    f"{allocations[additive.id].fills_text()} ({allocations[additive.id].fill_note})"
                    for additive in plan.design.additives if additive.id in allocations
                ),
            )
            if self._echo_is_away(unreachable):
                # The bench instruction that the missing record turns into:
                # nobody can say which wells are free, so start from a plate
                # where every well is.
                preparation = (
                    f"{theme.SYMBOL_ATTENTION} Use a fresh source plate · the shared "
                    "record is away with the ECHO folder, so the wells below are "
                    "taken as free; what this takes waits in "
                    f"{record_directory(pending_root(self.window.settings))}",
                    *preparation,
                )
        editor.worksheets_step.show_schedule(
            tuple(f"T+{format_minutes(minute)} · {action}" for minute, action in schedule),
            preparation,
        )
        if latest is None:
            # Not finalized yet: the footer names that and disables saving.
            editor.worksheets_step.show_result("", "muted")
        elif delivered is not None and latest.status != WORKSHEETS_SUCCEEDED:
            # The instruments keep what the earlier save put there, so the files
            # are still listed above; what failed is only the attempt to redo it.
            again = (
                "was cancelled" if latest.status == WORKSHEETS_CANCELLED
                else f"failed · {latest.error_message or ''}".rstrip(" ·")
            )
            editor.worksheets_step.show_result(
                f"{theme.SYMBOL_ATTENTION} Saved r{revision.revision} · "
                f"{delivered.exported_at:%Y-%m-%d %H:%M} · {delivered.username} · "
                f"saving again {again}; the files above are from that earlier save",
                "attention",
                can_retry=True,
                copy_text="\n".join(output.path for output in delivered.outputs),
            )
        elif latest.status == WORKSHEETS_SUCCEEDED and self._saved_elsewhere(latest):
            editor.worksheets_step.show_result(
                f"{theme.SYMBOL_ATTENTION} Saved r{revision.revision} to the folder you "
                f"chose · {latest.exported_at:%Y-%m-%d %H:%M} · {latest.username} · "
                "not in the instrument folders; copy the files there, or save again "
                "once they can be reached",
                "attention",
                can_retry=True,
                copy_text="\n".join(output.path for output in latest.outputs),
            )
        elif latest.status == WORKSHEETS_SUCCEEDED and waiting:
            # The ordinary state here: an instrument is off and its worksheet
            # waits in a folder of this account's own until somebody carries
            # it across. Nothing is written to an empty mountpoint.
            files = tuple(
                output.path for output in self._waiting_outputs(latest)
            )
            editor.worksheets_step.show_result(
                f"{theme.SYMBOL_ATTENTION} Saved r{revision.revision} · "
                f"{latest.exported_at:%Y-%m-%d %H:%M} · {latest.username} · "
                f"{counted(len(files), 'file')} for "
                f"{self._instrument_names(tuple(sorted(waiting)))} "
                f"{'are' if len(files) > 1 else 'is'} not on the "
                f"{'instruments' if len(waiting) > 1 else 'instrument'} yet · "
                "copy from the folder below before running there",
                "attention",
                can_retry=True,
                copy_text="\n".join(files),
            )
        elif latest.status == WORKSHEETS_SUCCEEDED and left_out:
            editor.worksheets_step.show_result(
                f"{theme.SYMBOL_ATTENTION} Saved r{revision.revision} · "
                f"{latest.exported_at:%Y-%m-%d %H:%M} · {latest.username} · "
                f"{self._instrument_names(left_out)} did not receive the "
                "worksheets; do not run this experiment there",
                "attention",
                can_retry=True,
                copy_text="\n".join(output.path for output in latest.outputs),
            )
        elif latest.status == WORKSHEETS_SUCCEEDED:
            editor.worksheets_step.show_result(
                f"{theme.SYMBOL_OK} Saved r{revision.revision} · "
                f"{latest.exported_at:%Y-%m-%d %H:%M} · {latest.username} · "
                "files are ready; nothing has run on the instruments yet",
                "ok",
                copy_text="\n".join(output.path for output in latest.outputs),
            )
        elif latest.status == WORKSHEETS_CANCELLED:
            editor.worksheets_step.show_result(
                "No worksheets were saved. The save was cancelled.", "muted",
                can_retry=True,
            )
        else:
            editor.worksheets_step.show_result(
                f"{theme.SYMBOL_ERROR} No worksheets were saved. "
                f"{latest.error_message or ''}",
                "error",
                can_retry=True,
            )

    def _worksheets_saved(self, revision) -> bool:
        return self._successful_worksheet_export(revision) is not None

    def _worksheets_saved_elsewhere(self, revision) -> bool:
        """No file reached an instrument, whichever folder they are all in.

        The step cannot be done either way: nothing can run until somebody
        carries them across.
        """
        delivered = self._successful_worksheet_export(revision)
        return (
            delivered is not None
            and bool(delivered.outputs)
            and len(self._waiting_outputs(delivered)) == len(delivered.outputs)
        )

    def _successful_worksheet_export(self, revision):
        """The last save that worked, which is what the instruments hold."""
        return next(
            (
                event for event in reversed(self._worksheet_exports_for(revision))
                if event.status == WORKSHEETS_SUCCEEDED
            ),
            None,
        )

    def _on_its_instrument(self, output) -> bool:
        destination = self.window.settings.instrument(output.instrument)
        return (
            destination is not None
            and destination.output_directory in Path(output.path).parents
        )

    def _waiting_outputs(self, event) -> tuple:
        """Files that are not in their instrument's own folder yet."""
        return tuple(
            output for output in event.outputs if not self._on_its_instrument(output)
        )

    def _saved_elsewhere(self, event) -> bool:
        """True when the whole save went to a folder the user chose.

        Told from the folder worksheets wait in when no instrument can be
        reached: that one is ours and we know where it is.
        """
        waiting = self._waiting_outputs(event)
        if not event.outputs or len(waiting) != len(event.outputs):
            return False
        held = pending_root(self.window.settings)
        return not all(held in Path(output.path).parents for output in waiting)

    def _latest_worksheet_export(self, revision):
        exports = self._worksheet_exports_for(revision)
        return exports[-1] if exports else None

    def _worksheet_exports_for(self, revision) -> tuple:
        if revision is None or self.window.review_store is None:
            return ()
        try:
            return self.window.review_store.audit.list_worksheet_exports(revision.id)
        except ReviewPersistenceError:
            return ()

    def _other_experiments_using_current_image(self) -> tuple[str, ...]:
        image_set = self.window.project_controller.active_image_set
        experiment_id = self.window.project_controller.experiment_id
        if (
            self.window.review_store is None or self.window.controller is None
            or image_set is None or not experiment_id
        ):
            return ()
        try:
            usage = self.window.review_store.workspace.experiments_using_images(
                image_set.id, experiment_id
            )
        except ReviewPersistenceError:
            return ()
        others = usage.get(self.window.controller.current_image.image_key, ())
        if not others:
            return ()
        names = self._experiment_names()
        return tuple(names.get(plan_id, "a deleted experiment") for plan_id in others)

    def _set_editor_well_usage(self, editor: PlanEditorBase) -> None:
        if self.window.review_store is None or editor.selection is None:
            editor.set_well_usage({})
            return
        image_keys = tuple(well.image_key for well in editor.selection.wells)
        try:
            usage = self.window.review_store.planning.prior_selected_well_usage(
                editor.plan_id, image_keys
            )
        except ReviewPersistenceError as error:
            editor.error_label.setText(f"Reuse history unavailable: {error}")
            return
        editor.set_well_usage(usage)

    def _refresh_project_well_usage(self) -> None:
        for editor in self._editors.values():
            self._set_editor_well_usage(editor)

    def _plan_draft_changed(self, editor: PlanEditorBase) -> None:
        if editor.autosave_timer is None:
            return
        editor.lifecycle_label.setText("Draft · saving…")
        editor.webdb_upload_button.setEnabled(False)
        editor.autosave_timer.start()
        self._refresh_experiment_status(editor)

    def _save_selection_snapshot(
        self, editor: PlanEditorBase, selection: CrystalSelection
    ) -> bool:
        if self.planning_service is None:
            return True
        try:
            self.planning_service.save_selection_snapshot(
                editor.plan_id, editor.plan_name, editor.plan_type, selection,
                editor.plan_created_at,
            )
        except ReviewPersistenceError as error:
            self.window._show_persistence_error(error)
            return False
        return True
    @staticmethod

    def _plan_snapshot(editor: PlanEditorBase) -> str | None:
        return editor.plan_snapshot()
    @staticmethod

    def _draft_from_editor(editor: PlanEditorBase) -> PlanningDraft:
        return editor.draft(datetime.now(timezone.utc))

    def _persist_draft(self, editor: PlanEditorBase) -> None:
        if not editor.persisted and not self._has_content(editor):
            # Opening a new experiment to look at it does not create a draft.
            editor.lifecycle_label.setText("Not saved yet")
            self._refresh_experiment_status(editor)
            return
        if self.planning_service is None:
            editor.lifecycle_label.setText("Draft · memory only")
            self._refresh_experiment_status(editor)
            return
        try:
            self.planning_service.save_draft(self._draft_from_editor(editor))
        except ReviewPersistenceError as error:
            editor.save_failed = True
            editor.lifecycle_label.setText("Draft · save failed")
            self._refresh_experiment_status(editor)
            self.window._show_persistence_error(error)
            return
        editor.save_failed = False
        editor.persisted = True
        status = saved_plan_status(
            editor.last_revision, editor.last_revision_snapshot,
            self._plan_snapshot(editor),
        )
        self._show_plan_status(editor, status)
        if status.finalized:
            self._sync_webdb_upload_state(editor)

    def _show_plan_status(self, editor: PlanEditorBase, status: PlanStatus) -> None:
        editor.lifecycle_label.setText(status.label if editor.persisted else "Not saved yet")
        self._refresh_experiment_status(editor)

    def _suggest_experiment_id(self, plan_type: PlanType, protein: str) -> str:
        if self.planning_service is None:
            return suggest_experiment_id(
                EXPERIMENT_ID_PREFIXES[plan_type], protein, self._mxlive_experiment_ids
            )
        return self.planning_service.suggest_experiment_id(
            plan_type, protein, self._mxlive_experiment_ids
        )

    def _finalize_plan(self, editor: PlanEditorBase) -> PlanRevision | None:
        if self.planning_service is None:
            QMessageBox.warning(
                self.window, "Cannot finalize plan",
                "Open a writable review database first.",
            )
            return None
        snapshot = self._plan_snapshot(editor)
        if snapshot is None:
            QMessageBox.warning(
                self.window, "Cannot finalize plan",
                editor.error_label.text() or "The plan is not valid.",
            )
            return None
        self._persist_draft(editor)
        if snapshot == editor.last_revision_snapshot:
            return editor.last_revision
        if (
            editor.assigned_experiment_id is None
            and not self._check_new_experiment_id_against_mxlive()
        ):
            return None
        if not self._save_selection_snapshot(editor, editor.selection):
            return None
        try:
            revision = self.planning_service.finalize(
                editor.plan_id, editor.plan_type, editor.protein_input.text(),
                snapshot, editor.assigned_experiment_id, getpass.getuser(),
                self._mxlive_experiment_ids,
            )
        except STORE_ERRORS as error:
            QMessageBox.warning(self.window, "Cannot finalize plan", str(error))
            return None
        editor.last_revision = revision
        editor.assigned_experiment_id = revision.experiment_id
        editor.last_revision_snapshot = snapshot
        editor._refresh_experiment_id()
        self._persist_draft(editor)
        self._sync_webdb_upload_state(editor)
        self._refresh_project_well_usage()
        return revision

    def _sync_webdb_upload_state(
        self, editor: PlanEditorBase, failure: str = ""
    ) -> None:
        try:
            self._update_webdb_upload_controls(editor, failure)
        finally:
            self._refresh_experiment_status(editor)

    def _update_webdb_upload_controls(
        self, editor: PlanEditorBase, failure: str = ""
    ) -> None:
        editor.webdb_upload_button.setEnabled(False)
        editor.webdb_upload_button.setText("Upload to MxLive…")
        self._set_webdb_upload_state(editor, failure)
        account = self.mxlive_account
        revision = editor.last_revision
        if account is None or revision is None or not account.upload_ready:
            return
        lock = self._webdb_upload_lock(revision.experiment_id)
        if lock is None:
            return
        if not lock.can_upload:
            state = {
                UploadAvailability.UPLOADED: "Uploaded",
                UploadAvailability.PARTIAL: "Partial upload · review MxLive",
                UploadAvailability.NEEDS_VERIFICATION:
                    "Upload result unknown · verify on MxLive",
            }[lock.availability]
            if lock.event is not None and lock.event.revision_id != revision.id:
                state += " (earlier revision)"
            self._set_webdb_upload_state(editor, state)
            if lock.can_verify:
                editor.webdb_upload_button.setText("Verify on MxLive…")
                editor.webdb_upload_button.setEnabled(True)
                editor.webdb_upload_button.setToolTip(
                    "Check which records MxLive stored before allowing another upload."
                )
            else:
                editor.webdb_upload_button.setToolTip(
                    "This experiment ID is already in MxLive, which cannot update "
                    "or delete labworks."
                )
            return
        if self._plan_snapshot(editor) != editor.last_revision_snapshot:
            return
        editor.webdb_upload_button.setEnabled(True)
        editor.webdb_upload_button.setToolTip(
            "Upload this exact finalized revision to MxLive labworks."
        )
    @staticmethod

    def _set_webdb_upload_state(editor, state: str) -> None:
        # Stored on the editor so preview refreshes keep showing the upload state.
        if editor.webdb_upload_state != state:
            editor.webdb_upload_state = state
            editor._refresh_webdb_preview()

    def _webdb_upload_lock(self, experiment_id: str) -> UploadLockState | None:
        """Return None when upload history is unreadable, which blocks uploading."""
        if self.upload_service is None:
            return UploadLockState(UploadAvailability.AVAILABLE)
        try:
            return self.upload_service.lock_state(experiment_id)
        except ReviewPersistenceError as error:
            self.window.status_message_label.show_message(
                f"Upload history unavailable: {error}"
            )
            return None

    def _upload_plan_labworks(self, editor: PlanEditorBase) -> None:
        revision = editor.last_revision
        if revision is None:
            QMessageBox.warning(
                self.window, "Cannot upload", "Finalize the current plan revision first."
            )
            return
        build_labworks = editor.record_builder()
        snapshot = json.loads(revision.snapshot_json)
        self._upload_labworks(
            editor,
            build_labworks(
                plan_from_snapshot(editor.plan_id, revision.snapshot_json),
                experiment_id=revision.experiment_id,
                protein_name=str(snapshot.get("protein", "")).strip(),
                username=self.mxlive_account.username if self.mxlive_account else "",
                account_id=self.mxlive_account.account_id if self.mxlive_account else "",
            ),
        )

    def _upload_labworks(self, editor: PlanEditorBase, records: tuple) -> None:
        account = self.mxlive_account
        revision = editor.last_revision
        if account is None or revision is None:
            QMessageBox.warning(
                self.window, "Cannot upload", "Finalize the current plan revision first."
            )
            return
        if not account.upload_ready:
            QMessageBox.warning(
                self.window, "Cannot upload", "\n".join(account.upload_blockers)
            )
            return
        if self.upload_service is None:
            QMessageBox.warning(
                self.window, "Cannot upload",
                "Open a writable review database so the upload can be audited.",
            )
            return
        lock = self._webdb_upload_lock(revision.experiment_id)
        if lock is None:
            QMessageBox.warning(
                self.window, "Cannot upload",
                "Upload history could not be read, so a duplicate upload cannot be "
                "ruled out.",
            )
            return
        if lock.can_verify and lock.event is not None:
            self._verify_labworks_upload(editor, revision.experiment_id, lock.event)
            return
        if not lock.can_upload:
            QMessageBox.information(
                self.window, "Already uploaded",
                f"{revision.experiment_id} is already in MxLive. MxLive cannot update "
                "labworks, so it will not be uploaded again.",
            )
            self._sync_webdb_upload_state(editor)
            return
        if self._plan_snapshot(editor) != editor.last_revision_snapshot:
            QMessageBox.warning(
                self.window, "Cannot upload", "Finalize the current plan revision first."
            )
            return
        try:
            held = self._records_already_on_mxlive(revision.experiment_id)
        except (MxLiveReadError, ValueError, OSError) as error:
            # Refusing is the safe answer: an upload that overwrites cannot be
            # taken back, and not sending one costs only a later attempt.
            QMessageBox.warning(
                self.window, "Cannot upload",
                "MxLive could not be asked whether it already holds "
                f"{revision.experiment_id}, so an upload that would overwrite "
                f"records there cannot be ruled out.\n\n{error}",
            )
            return
        if held:
            QMessageBox.warning(
                self.window, "Already on MxLive",
                f"MxLive already holds {counted(held, 'record')} for "
                f"{revision.experiment_id}.\n\nUploading again would overwrite "
                "them, and would erase the harvest time and the puck the beamline "
                "recorded on them. MxLive cannot undo that.",
            )
            self._sync_webdb_upload_state(editor)
            return
        endpoint = labworks_endpoint(account.base_url, account.beamline)
        answer = QMessageBox.question(
            self.window,
            "Upload finalized revision",
            f"Upload {len(records)} records for {revision.experiment_id}?\n\n"
            f"Account: {account.username}\n"
            f"API project_id: {account.account_id}\n"
            f"Endpoint: {endpoint}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        payload = tuple(record.to_payload() for record in records)
        try:
            pending = self.upload_service.begin(
                revision, account.username, account.account_id, endpoint, payload
            )
        except ReviewPersistenceError as error:
            QMessageBox.critical(
                self.window, "Upload was not sent",
                f"The upload audit record could not be saved:\n{error}",
            )
            return

        def upload():
            client = LegacyMxLiveWriteClient(
                account.base_url, account.beamline, account.username,
                account.key_path, ca_bundle=account.ca_bundle,
                timeout_seconds=self.window.settings.mxlive_timeout_seconds,
            )
            return client.upload_labworks(payload)

        outcome = self.window._run_in_background(
            f"Uploading {len(records)} records to MxLive…",
            lambda: send_labworks(upload),
        )
        try:
            self.upload_service.complete(pending, outcome)
        except ReviewPersistenceError as error:
            QMessageBox.critical(
                self.window, "Upload audit could not be saved",
                "MxLive may have accepted the upload, but its result could not be "
                f"saved:\n{error}\n\nVerify on MxLive before uploading again.",
            )
            self._sync_webdb_upload_state(editor)
            return
        if outcome.status == FAILED:
            self._sync_webdb_upload_state(editor, "Last upload failed")
            QMessageBox.critical(
                self.window, "WebDB upload failed", outcome.error_message or "Unknown error"
            )
            return
        self._sync_webdb_upload_state(editor)
        self._refresh_project_well_usage()
        if outcome.status == SUCCEEDED:
            QMessageBox.information(
                self.window, "WebDB upload complete",
                f"Uploaded {len(records)} records for {revision.experiment_id}.",
            )
        elif outcome.status == PARTIAL:
            QMessageBox.critical(
                self.window, "WebDB upload partially completed",
                outcome.error_message or "Some records may have been uploaded.",
            )
        else:
            QMessageBox.critical(
                self.window, "WebDB upload result unknown",
                f"{outcome.error_message}\n\nMxLive may have stored these records. "
                "Use Verify on MxLive before uploading again.",
            )

    def _records_already_on_mxlive(self, experiment_id: str) -> int:
        """How many records MxLive already holds under this experiment ID.

        The local audit only knows about uploads made from this computer and
        this database, and MxLive overwrites a labwork it already has with the
        values sent - erasing the harvest time and the puck the beamline wrote
        on it. So the server is asked before anything is sent.

        MxLive matches the identifier as a substring, so the answers are
        compared here for an exact one.
        """
        reader = self._mxlive_reader()
        labworks = self.window._run_in_background(
            f"Checking {experiment_id} on MxLive…",
            lambda: reader.labworks(experiment_id),
        )
        return sum(1 for item in labworks if item.experiment_id == experiment_id)

    def _verify_labworks_upload(
        self, editor, experiment_id: str, event: WebDBUploadEvent
    ) -> None:
        account = self.mxlive_account
        if account is None or not account.upload_ready or self.upload_service is None:
            QMessageBox.warning(
                self.window, "Cannot verify upload", "MxLive access is not configured."
            )
            return
        try:
            reader = self._mxlive_reader()
            labworks = self.window._run_in_background(
                f"Checking {experiment_id} on MxLive…",
                lambda: reader.labworks(experiment_id),
            )
        except (MxLiveReadError, ValueError) as error:
            QMessageBox.warning(self.window, "Could not verify upload", str(error))
            return
        try:
            verified = self.upload_service.verify(event, experiment_id, labworks)
        except ReviewPersistenceError as error:
            QMessageBox.warning(self.window, "Could not record verification", str(error))
            return
        self._sync_webdb_upload_state(editor)
        self._refresh_project_well_usage()
        if verified.status == FAILED:
            QMessageBox.information(
                self.window, "Upload not found on MxLive",
                f"MxLive has no records for {experiment_id}. You can upload again.",
            )
        elif verified.status == SUCCEEDED:
            QMessageBox.information(
                self.window, "Upload verified",
                f"MxLive has all {event.record_count} records for {experiment_id}.",
            )
        else:
            QMessageBox.critical(
                self.window, "Upload incomplete on MxLive",
                f"{verified.error_message}\n\nReview {experiment_id} in WebDB before "
                "taking further action.",
            )

    def _fragment_library_choices(
        self,
    ) -> tuple[tuple[str, str, FragmentLibrary], ...]:
        choices = []
        library_directory = self.window.settings.fragment_library_directory
        if not library_directory.is_dir():
            return ()
        for path in sorted(
            library_directory.glob("*.csv"),
            key=lambda item: item.name.casefold(),
        ):
            try:
                library = load_fragment_library(path)
            except FragmentLibraryCsvError:
                continue
            choices.append(
                (
                    str(path.resolve()),
                    f"{path.name} · {len(library.fragments)} rows",
                    library,
                )
            )
        return tuple(choices)

    def _refresh_fragment_library_choices(self) -> None:
        choices = self._fragment_library_choices()
        for draft_editor in self._editors.values():
            if isinstance(draft_editor, FragmentScreeningEditor):
                draft_editor.set_library_choices(choices)
        self.window.status_message_label.show_message(
            f"Found {len(choices)} libraries in "
            f"{self.window.settings.fragment_library_directory}",
            4000,
        )

    def _mxlive_reader(self) -> LegacyMxLiveReadClient:
        account = self.mxlive_account
        return LegacyMxLiveReadClient(
            account.base_url, account.beamline, account.username,
            account.key_path, ca_bundle=account.ca_bundle,
            timeout_seconds=self.window.settings.mxlive_timeout_seconds,
        )

    def _check_new_experiment_id_against_mxlive(self) -> bool:
        """Load IDs other users already uploaded; False cancels finalization."""
        account = self.mxlive_account
        if account is None or not account.upload_ready:
            return True
        try:
            reader = self._mxlive_reader()
            remote = self.window._run_in_background(
                "Checking experiment IDs on MxLive…",
                lambda: reader.experiment_ids(datetime.now().year),
            )
        except (MxLiveReadError, ValueError) as error:
            answer = QMessageBox.question(
                self.window,
                "Experiment ID not checked",
                f"Existing experiment IDs could not be read from MxLive:\n{error}\n\n"
                "Finalize with an ID checked only against this computer? It may "
                "match another researcher's experiment.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return answer == QMessageBox.Yes
        self._mxlive_experiment_ids.update(remote)
        return True

    def _save_plan_worksheets(
        self,
        editor: PlanEditorBase,
        alternate_root: Path | None = None,
        instruments: tuple[str, ...] | None = None,
    ) -> None:
        """Save every worksheet of the finalized revision, or none of them.

        An instrument that cannot be reached is the ordinary state here, not
        an interruption: its worksheet waits in a folder of this account's own
        and the others are delivered, in one go and without a question. What
        is waiting, and where, is on the page afterwards.
        """
        revision = editor.last_revision
        if revision is None or self._plan_snapshot(editor) != editor.last_revision_snapshot:
            QMessageBox.warning(
                self.window, "Cannot save worksheets",
                "Finalize the experiment in Review first. Worksheets are made from "
                "the finalized revision.",
            )
            return
        # Deliver what the revision fixed, not whatever the editor shows now.
        try:
            plan = plan_from_snapshot(editor.plan_id, revision.snapshot_json)
        except ValueError as error:
            QMessageBox.warning(self.window, "Cannot save worksheets", str(error))
            return
        service = self._worksheet_export_service()
        destination = (
            str(alternate_root) if alternate_root is not None
            else "SHIFTER folders" if editor.plan_type is PlanType.RAW_CRYSTAL
            else "instrument folders"
        )
        try:
            result = self.window._run_in_background(
                f"Saving worksheets to {destination}…",
                lambda: service.deliver(
                    plan, revision.experiment_id, alternate_root, instruments,
                    hold_unreachable=alternate_root is None,
                ),
            )
        except WorksheetDestinationUnavailable as error:
            self._record_worksheet_export(
                service, revision, WORKSHEETS_FAILED, error=str(error)
            )
            self.window.status_message_label.show_message("No worksheets were saved", 5000)
            self._refresh_worksheets_step(editor)
            self._refresh_experiment_status(editor)
            # An unreachable instrument folder is nothing the user can fix
            # here, so offer what can still be done: the instruments that are
            # up, or another folder for the files.
            return
        else:
            self._record_worksheet_export(
                service, revision, WORKSHEETS_SUCCEEDED, result=result
            )
            editor.experiment_id_label.setText(
                f"Experiment ID: {result.experiment_id} · Saved as {result.file_stem}"
            )
            if isinstance(editor, ConditionTestEditor) and alternate_root is None:
                # Its wells are on the shared record now - and if the record
                # was reachable this time, so is anything held from before.
                self._merge_held_source_entries()
                editor.refresh_plate_records()
            self.window.status_message_label.show_message(
                f"Worksheets for {result.experiment_id} saved to the folder you chose"
                if alternate_root is not None
                else f"Worksheets saved for {result.experiment_id}",
                5000,
            )
        self._refresh_worksheets_step(editor)
        self._refresh_experiment_status(editor)

    def _unreachable_instruments(self) -> dict[str, str]:
        """Which instrument folders cannot be reached, and why, right now."""
        try:
            return WorksheetExporter(
                self.window.settings, getpass.getuser()
            ).unreachable_instruments()
        except (OSError, ValueError):
            return {}

    def _echo_is_away(self, unreachable: dict[str, str] | None = None) -> bool:
        """Whether the ECHO folder, and with it the shared source plate record, is gone."""
        away = self._unreachable_instruments() if unreachable is None else unreachable
        return any(
            (destination := self.window.settings.instrument(instrument)) is not None
            and destination.worksheet is WorksheetKind.ECHO
            for instrument in away
        )

    def _instrument_names(self, instruments: tuple[str, ...]) -> str:
        """The instruments as the user knows them: ECHO 650 and SHIFTER 1."""
        labels = []
        for instrument in instruments:
            destination = self.window.settings.instrument(instrument)
            labels.append(destination.label if destination is not None else instrument)
        if len(labels) < 2:
            return "".join(labels)
        return f"{', '.join(labels[:-1])} and {labels[-1]}"

    def _save_worksheets_elsewhere(self, editor: PlanEditorBase) -> None:
        selected = QFileDialog.getExistingDirectory(
            self.window, "Save worksheets to another folder",
            str(self._alternate_worksheet_root or Path.home()),
        )
        if selected:
            # A share that is down keeps everyone saving to the same folder today.
            self._alternate_worksheet_root = Path(selected)
            self._save_plan_worksheets(editor, Path(selected))

    def _worksheet_export_service(self) -> WorksheetExportService:
        return WorksheetExportService(
            WorksheetExporter(self.window.settings, getpass.getuser()),
            self.window.review_store.audit if self.window.review_store is not None else None,
            getpass.getuser(),
            self._source_plate_record(),
            SourcePlateRecord(
                record_directory(pending_root(self.window.settings)), create_root=True
            ),
        )

    def _merge_held_source_entries(self) -> None:
        """Put wells taken while the record was away onto it, once it is back.

        Asked whenever a condition test is opened and after every save that
        reached the record: those are the moments it is known to be there.
        Costs one look at a local folder when nothing is held, which is the
        ordinary case.
        """
        shared = self._source_plate_record()
        if shared is None:
            return
        held = SourcePlateRecord(
            record_directory(pending_root(self.window.settings)), create_root=True
        )
        try:
            merged = merge_held(held, shared)
        except WorksheetDestinationUnavailable:
            return  # Still away. The entries keep waiting; nothing is lost.
        if merged:
            plates = ", ".join(sorted(set(merged)))
            self.window.status_message_label.show_message(
                f"Source wells taken while the ECHO was away are on the shared "
                f"record now · {plates}",
                6000,
            )

    def _source_plate_record(self) -> SourcePlateRecord | None:
        """Shared source plates' well records, kept with the ECHO worksheets everyone saves."""
        echo = next(
            (
                item for item in self.window.settings.instruments
                if item.worksheet is WorksheetKind.ECHO
            ),
            None,
        )
        if echo is None:
            return None
        return SourcePlateRecord(
            record_directory(echo.output_directory),
            create_root=self.window.settings.create_missing_instrument_roots,
        )

    def _record_worksheet_export(
        self, service: WorksheetExportService, revision: PlanRevision, status: str,
        *, result=None, error: str | None = None,
    ) -> None:
        try:
            service.record(revision, status, result=result, error=error)
        except ReviewPersistenceError as persistence_error:
            self.window._show_persistence_error(persistence_error)

    def _review_target_warnings(self) -> None:
        if (
            self.current_editor is not None
            and self._current_step is not WorkflowStep.SELECT_WELLS
        ):
            self.go_to_step(WorkflowStep.SELECT_WELLS)
        warning_index = self.window.target_summary_filter.findData("warnings")
        self.window.target_summary_filter.setCurrentIndex(warning_index)
        self.window.target_summary_dock.show()
        self.window.target_summary_dock.raise_()
        self.window._refresh_target_summary()
        if self.window.target_summary_table.rowCount():
            self.window.target_summary_table.setCurrentCell(0, 0)
            self.window.target_summary_table.setFocus(Qt.OtherFocusReason)
        elif self.current_editor is not None:
            # The step's count can only be older than the dock, so say the
            # wells are ready rather than leave an empty list to answer for it.
            self.window.status_message_label.show_message(
                "No well needs attention any more", 4000
            )
            self._sync_editor_selection(self.current_editor)
