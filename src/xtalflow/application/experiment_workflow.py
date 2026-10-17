"""Where an experiment stands in its guided steps, computed in one place.

Screens show these results instead of deciding readiness on their own, so the
stepper, footer, and Finalize button always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xtalflow.application.labwork_upload import UploadAvailability
from xtalflow.domain.experiment_naming import normalize_protein_name
from xtalflow.domain.mxlive import BeamlineProgress
from xtalflow.domain.experiment_project import PlanType
from xtalflow.text import counted


class WorkflowStep(str, Enum):
    SETUP = "setup"
    SELECT_WELLS = "select_wells"
    CONDITIONS = "conditions"
    REVIEW = "review"
    WORKSHEETS = "worksheets"


STEP_LABELS = {
    WorkflowStep.SETUP: "Setup",
    WorkflowStep.SELECT_WELLS: "Select wells",
    WorkflowStep.CONDITIONS: "Conditions",
    WorkflowStep.REVIEW: "Review",
    WorkflowStep.WORKSHEETS: "Worksheets",
}


def _and_list(names: tuple[str, ...]) -> str:
    return " and ".join(", ".join(names).rsplit(", ", 1))


def _pucks(names: tuple[str, ...]) -> str:
    return ("puck " if len(names) == 1 else "pucks ") + _and_list(names)


@dataclass(frozen=True)
class ExperimentProgress:
    """How far an experiment has come, for the list of experiments.

    ``headline`` is short enough for a column; ``detail`` is the whole story,
    shown when the stage mark is hovered or clicked.  ``state`` is a key of
    ``theme.STATUS_COLORS``: it decides the colour, never the meaning.
    """

    headline: str
    detail: str
    state: str = "muted"


def experiment_progress(
    step: WorkflowStep,
    revision_number: int | None,
    *,
    worksheets_saved: bool = False,
    worksheets_saved_elsewhere: bool = False,
    instruments_missing: tuple[str, ...] = (),
    instruments_expected: int = 0,
    save_failed: bool = False,
    instruments_have_revision: int | None = None,
    upload: UploadAvailability | None = None,
    uploaded_revision: int | None = None,
    beamline: BeamlineProgress | None = None,
) -> ExperimentProgress:
    """The furthest stage this program can see for itself.

    The headline is the stage THIS revision reached, and anything after the
    middle dot is the one thing that makes that stage unusable.  What follows
    the upload - the beamline accepting the samples and collecting data - is
    MxLive's to tell, and MxLive's labwork records carry no field for either.

    The facts are about the newest revision unless they say otherwise:
    ``instruments_have_revision`` is an earlier revision whose worksheets are
    the ones sitting in the instrument folders, and ``uploaded_revision`` is
    the revision MxLive actually holds.  ``instruments_missing`` names the
    instruments that read this plan's worksheets and did not get one, because
    they were switched off when the others were saved.
    """
    if revision_number is None:
        return ExperimentProgress(
            f"Draft \u00b7 {STEP_LABELS[step]}",
            "Not finalized yet. The experiment was left at the "
            f"{STEP_LABELS[step]} step.",
        )
    uploaded = upload is UploadAvailability.UPLOADED
    sent = uploaded and uploaded_revision in (None, revision_number)
    harvested = sent and beamline is not None and beamline.fully_harvested
    if harvested:
        headline = f"Harvested r{revision_number}"
        stage = (
            f"The beamline harvested all {counted(beamline.records, 'crystal')} "
            f"of r{revision_number}"
            + (f" into {_pucks(beamline.pucks)}." if beamline.pucks else ".")
        )
        clean = "Nothing after this is recorded in MxLive."
    elif sent:
        headline = f"Sent r{revision_number}"
        stage = f"The sample list for r{revision_number} is on MxLive."
        clean = "Its worksheets are in the instrument folders."
        if beamline is not None and beamline.harvested:
            clean = (
                f"{beamline.harvested} of "
                f"{counted(beamline.records, 'crystal')} have been harvested."
            )
    elif worksheets_saved:
        headline = f"Worksheets r{revision_number}"
        every = (
            f"in the folders of all {instruments_expected} instruments that read them"
            if instruments_expected > 1 else "in the instrument folders"
        )
        stage = f"The worksheets for r{revision_number} are {every}."
        clean = "The sample list has not been sent to MxLive."
    else:
        headline = f"Finalized r{revision_number}"
        stage = f"Finalized as r{revision_number}."
        clean = "No worksheets have been saved yet."

    clause, because = _stage_clause(
        revision_number,
        sent=sent,
        worksheets_saved=worksheets_saved,
        worksheets_saved_elsewhere=worksheets_saved_elsewhere,
        instruments_missing=instruments_missing,
        save_failed=save_failed,
        instruments_have_revision=instruments_have_revision,
        upload=upload,
        uploaded_revision=uploaded_revision if uploaded else None,
    )
    if not clause:
        return ExperimentProgress(headline, f"{stage} {clean}", "ok")
    return ExperimentProgress(
        f"{headline} \u00b7 {clause}", f"{stage} {because}", "attention"
    )


def _stage_clause(
    revision_number: int,
    *,
    sent: bool,
    worksheets_saved: bool,
    worksheets_saved_elsewhere: bool,
    instruments_missing: tuple[str, ...],
    save_failed: bool,
    instruments_have_revision: int | None,
    upload: UploadAvailability | None,
    uploaded_revision: int | None,
) -> tuple[str, str]:
    """The one thing in the way, worst first; empty when nothing is."""
    if upload in (UploadAvailability.PARTIAL, UploadAvailability.NEEDS_VERIFICATION):
        # Never covered: every other warning asks for an action that can be
        # taken back, and this one asks for an action NOT to be taken.
        return "check MxLive", (
            f"The MxLive upload of r{revision_number} did not finish with a "
            "known result. Check which records MxLive holds before uploading "
            "again: there is no way to remove a labwork once it is stored."
        )
    if instruments_have_revision is not None:
        older = f"r{instruments_have_revision}"
        return f"instruments have {older}", (
            f"The instruments still hold the worksheets of {older}. Harvesting "
            f"now would follow that plan, not this one: save the worksheets "
            f"for r{revision_number} before anybody runs it."
        )
    if worksheets_saved_elsewhere:
        return "not on instruments", (
            f"The worksheets for r{revision_number} went to a folder you chose "
            "because an instrument could not be reached. Copy them to the "
            "instrument folders before running the experiment."
        )
    if worksheets_saved and instruments_missing:
        missing = (
            f"not on {instruments_missing[0]}" if len(instruments_missing) == 1
            else f"not on {counted(len(instruments_missing), 'instrument')}"
        )
        return missing, (
            f"The worksheets for r{revision_number} did not reach "
            f"{_and_list(instruments_missing)}, which could not be reached "
            "when they were saved. Do not run the experiment there."
        )
    if sent and not worksheets_saved:
        return "no worksheets", (
            "Its worksheets were never saved, so no instrument will harvest "
            "anything for it."
        )
    if save_failed:
        return "worksheet save failed", (
            f"The last attempt to save the worksheets for r{revision_number} "
            "failed, so no instrument has them. Open the experiment and save "
            "them again."
        )
    if uploaded_revision is not None and uploaded_revision != revision_number:
        older = f"r{uploaded_revision}"
        return f"MxLive has {older}", (
            f"MxLive holds the sample list of {older}, finalized before this "
            f"revision. Nothing was sent for r{revision_number}, and a labwork "
            "cannot be replaced once it is stored."
        )
    return "", ""


def steps_for(plan_type: PlanType) -> tuple[WorkflowStep, ...]:
    """Raw crystal harvesting has no conditions to choose.

    A condition test decides its conditions first, because they set how many
    crystals to select.
    """
    if plan_type is PlanType.CONDITION_TEST:
        return (
            WorkflowStep.SETUP, WorkflowStep.CONDITIONS, WorkflowStep.SELECT_WELLS,
            WorkflowStep.REVIEW, WorkflowStep.WORKSHEETS,
        )
    if plan_type is PlanType.RAW_CRYSTAL:
        return (
            WorkflowStep.SETUP, WorkflowStep.SELECT_WELLS,
            WorkflowStep.REVIEW, WorkflowStep.WORKSHEETS,
        )
    return tuple(WorkflowStep)


class StepState(str, Enum):
    COMPLETE = "complete"
    ATTENTION = "attention"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class StepStatus:
    step: WorkflowStep
    state: StepState
    message: str


@dataclass(frozen=True)
class ExperimentFacts:
    """What the workflow needs to know about an experiment, gathered by the caller."""

    plan_type: PlanType
    protein: str
    well_count: int
    position_count: int
    wells_needing_attention: int = 0
    conditions_error: str | None = None
    unused_fragment_count: int = 0
    revision_number: int | None = None
    revision_matches_current: bool = False
    worksheets_saved_for_revision: bool = False
    local_save_failed: bool = False
    # Saved, but to a folder the user chose because an instrument folder could
    # not be reached: the files still have to reach the instrument.
    worksheets_saved_elsewhere: bool = False
    # Condition tests: crystals the included conditions need, a one-line summary
    # of them, a problem found only with the chosen wells, and wells left over.
    required_well_count: int = 0
    conditions_summary: str = ""
    selection_error: str | None = None
    unused_well_count: int = 0
    # Why the selected wells could not be counted: the image share was unreachable.
    images_unavailable: str | None = None


@dataclass(frozen=True)
class ExperimentStatus:
    steps: tuple[StepStatus, ...]
    ready_to_finalize: bool
    notes: tuple[str, ...] = ()

    def status_of(self, step: WorkflowStep) -> StepStatus:
        return next(status for status in self.steps if status.step is step)

    @property
    def first_unfinished(self) -> WorkflowStep:
        return next(
            (status.step for status in self.steps if status.state is not StepState.COMPLETE),
            self.steps[-1].step,
        )


def evaluate_experiment(facts: ExperimentFacts) -> ExperimentStatus:
    statuses: dict[WorkflowStep, StepStatus] = {}
    notes: list[str] = []

    try:
        normalize_protein_name(facts.protein)
        statuses[WorkflowStep.SETUP] = StepStatus(
            WorkflowStep.SETUP, StepState.COMPLETE, f"Protein: {facts.protein.strip()}"
        )
    except ValueError:
        message = (
            "Enter a protein name to continue."
            if not facts.protein.strip() else "Fix the protein name to continue."
        )
        statuses[WorkflowStep.SETUP] = StepStatus(
            WorkflowStep.SETUP, StepState.INCOMPLETE, message
        )

    selection = f"{counted(facts.well_count, 'well')} · {counted(facts.position_count, 'position')}"
    condition_test = facts.plan_type is PlanType.CONDITION_TEST
    if facts.images_unavailable:
        # Without the images the wells cannot be counted, so nothing below is known.
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS, StepState.ATTENTION,
            f"Plate images unavailable · {facts.images_unavailable}",
        )
    elif condition_test and facts.required_well_count and facts.well_count < facts.required_well_count:
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS,
            StepState.ATTENTION if facts.wells_needing_attention else StepState.INCOMPLETE,
            f"{facts.well_count} of {facts.required_well_count} wells selected",
        )
    elif condition_test and facts.selection_error and not facts.wells_needing_attention:
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS, StepState.ATTENTION, facts.selection_error
        )
    elif facts.well_count == 0:
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS, StepState.INCOMPLETE,
            "Select at least one well to continue.",
        )
    elif facts.wells_needing_attention:
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS, StepState.ATTENTION,
            f"{counted(facts.wells_needing_attention, 'well')} "
            f"{'needs' if facts.wells_needing_attention == 1 else 'need'} attention · {selection}",
        )
    else:
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS, StepState.COMPLETE, selection
        )

    if condition_test and facts.unused_well_count:
        notes.append(
            f"The last {counted(facts.unused_well_count, 'selected well')} "
            f"{'is' if facts.unused_well_count == 1 else 'are'} not used."
        )

    steps = steps_for(facts.plan_type)
    if condition_test:
        statuses[WorkflowStep.CONDITIONS] = (
            StepStatus(WorkflowStep.CONDITIONS, StepState.ATTENTION, facts.conditions_error)
            if facts.conditions_error
            else StepStatus(WorkflowStep.CONDITIONS, StepState.COMPLETE, facts.conditions_summary)
        )
    elif WorkflowStep.CONDITIONS in steps:
        if facts.conditions_error:
            statuses[WorkflowStep.CONDITIONS] = StepStatus(
                WorkflowStep.CONDITIONS, StepState.ATTENTION, facts.conditions_error
            )
        elif facts.wells_needing_attention:
            statuses[WorkflowStep.CONDITIONS] = StepStatus(
                WorkflowStep.CONDITIONS, StepState.INCOMPLETE,
                "Check the selected wells before assigning fragments.",
            )
        elif facts.well_count == 0:
            # Volumes and fragment counts can only be checked against selected wells.
            statuses[WorkflowStep.CONDITIONS] = StepStatus(
                WorkflowStep.CONDITIONS, StepState.INCOMPLETE,
                "Select wells to check the fragment assignment.",
            )
        else:
            message = f"Fragments assigned to {counted(facts.well_count, 'well')}"
            if facts.unused_fragment_count:
                notes.append(
                    f"The last {counted(facts.unused_fragment_count, 'fragment')} "
                    "in the chosen rows will not be used."
                )
            statuses[WorkflowStep.CONDITIONS] = StepStatus(
                WorkflowStep.CONDITIONS, StepState.COMPLETE, message
            )
    elif facts.conditions_error:
        # Plans without a conditions step still report build problems at Review.
        notes.append(facts.conditions_error)

    earlier_complete = all(
        statuses[step].state is StepState.COMPLETE
        for step in steps
        if step not in (WorkflowStep.REVIEW, WorkflowStep.WORKSHEETS)
    )
    ready = (
        earlier_complete
        and not facts.local_save_failed
        and not (facts.plan_type is PlanType.RAW_CRYSTAL and facts.conditions_error)
    )
    finalized = facts.revision_number is not None and facts.revision_matches_current
    if finalized:
        review = StepStatus(
            WorkflowStep.REVIEW, StepState.COMPLETE, f"Finalized r{facts.revision_number}"
        )
    elif facts.revision_number is not None:
        review = StepStatus(
            WorkflowStep.REVIEW, StepState.ATTENTION,
            f"Changes after r{facts.revision_number} · finalize again to use them",
        )
    elif facts.local_save_failed:
        review = StepStatus(
            WorkflowStep.REVIEW, StepState.INCOMPLETE,
            "Your latest changes are not saved. Retry saving before finalizing.",
        )
    elif ready:
        review = StepStatus(WorkflowStep.REVIEW, StepState.INCOMPLETE, "Ready to finalize")
    else:
        review = StepStatus(
            WorkflowStep.REVIEW, StepState.INCOMPLETE, "Complete the earlier steps first."
        )
    statuses[WorkflowStep.REVIEW] = review

    if finalized and facts.worksheets_saved_for_revision and facts.worksheets_saved_elsewhere:
        worksheets = StepStatus(
            WorkflowStep.WORKSHEETS, StepState.ATTENTION,
            f"Worksheets r{facts.revision_number} saved to a folder you chose · "
            "copy them to the instrument folders",
        )
    elif finalized and facts.worksheets_saved_for_revision:
        worksheets = StepStatus(
            WorkflowStep.WORKSHEETS, StepState.COMPLETE,
            f"Worksheets saved r{facts.revision_number}",
        )
    elif finalized:
        worksheets = StepStatus(
            WorkflowStep.WORKSHEETS, StepState.INCOMPLETE, "Ready to save worksheets"
        )
    else:
        worksheets = StepStatus(
            WorkflowStep.WORKSHEETS, StepState.INCOMPLETE,
            "Finalize the experiment to save worksheets.",
        )
    statuses[WorkflowStep.WORKSHEETS] = worksheets

    return ExperimentStatus(
        tuple(statuses[step] for step in steps),
        ready and not finalized,
        tuple(notes),
    )
