"""Where an experiment stands in its guided steps, computed in one place.

Screens show these results instead of deciding readiness on their own, so the
stepper, footer, and Finalize button always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xtalflow.application.labwork_upload import UploadAvailability
from xtalflow.domain.experiment_naming import normalize_protein_name
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
    instruments_saved: int = 0,
    instruments_expected: int = 0,
    upload: UploadAvailability | None = None,
) -> ExperimentProgress:
    """The furthest stage this program can see for itself.

    The stages are in the order they happen.  What follows the upload - the
    beamline accepting the samples and collecting data - is MxLive's to tell,
    and MxLive's labwork records carry no field for either.

    ``instruments_saved`` of ``instruments_expected`` counts the instruments
    that read this plan's worksheets: one of them being switched off leaves
    the others with the files and that one without.
    """
    some_instruments = 0 < instruments_saved < instruments_expected
    reach = f"{instruments_saved} of {instruments_expected} instruments"
    if revision_number is None:
        return ExperimentProgress(
            f"Draft \u00b7 {STEP_LABELS[step]}",
            "Not finalized yet. The experiment was left at the "
            f"{STEP_LABELS[step]} step.",
        )
    if upload in (UploadAvailability.PARTIAL, UploadAvailability.NEEDS_VERIFICATION):
        return ExperimentProgress(
            f"Upload r{revision_number} \u00b7 check MxLive",
            f"The MxLive upload of r{revision_number} did not finish with a "
            "known result. Check which records MxLive holds before uploading "
            "again: there is no way to remove a labwork once it is stored.",
            "attention",
        )
    if upload is UploadAvailability.UPLOADED:
        sent = f"The sample list for r{revision_number} is on MxLive."
        # The samples are registered, but nothing will be harvested for them
        # until the worksheets reach the instruments. Say so here.
        if not worksheets_saved:
            return ExperimentProgress(
                f"Sent r{revision_number} \u00b7 no worksheets",
                f"{sent} Its worksheets were never saved, so no instrument "
                "will harvest anything for it.",
                "attention",
            )
        if worksheets_saved_elsewhere:
            return ExperimentProgress(
                f"Sent r{revision_number} \u00b7 worksheets elsewhere",
                f"{sent} Its worksheets went to a folder you chose, not to the "
                "instruments, so copy them across before running it.",
                "attention",
            )
        if some_instruments:
            return ExperimentProgress(
                f"Sent r{revision_number} \u00b7 {reach}",
                f"{sent} Its worksheets reached {reach}; the ones left out "
                "hold nothing for it.",
                "attention",
            )
        return ExperimentProgress(f"Sent r{revision_number}", sent, "ok")
    if worksheets_saved_elsewhere:
        return ExperimentProgress(
            f"Worksheets r{revision_number} \u00b7 elsewhere",
            f"The worksheets for r{revision_number} went to a folder you chose "
            "because an instrument could not be reached. Copy them to the "
            "instrument folders before running the experiment.",
            "attention",
        )
    if worksheets_saved and some_instruments:
        return ExperimentProgress(
            f"Worksheets r{revision_number} \u00b7 {reach}",
            f"The worksheets for r{revision_number} reached {reach}, because "
            "the others could not be reached when they were saved. Do not run "
            "the experiment on an instrument that holds nothing for it.",
            "attention",
        )
    if worksheets_saved:
        where = (
            f"in the folders of all {instruments_expected} instruments that "
            "read them" if instruments_expected > 1 else "in the instrument folders"
        )
        return ExperimentProgress(
            f"Worksheets r{revision_number}",
            f"The worksheets for r{revision_number} are {where}. The sample "
            "list has not been sent to MxLive.",
            "ok",
        )
    return ExperimentProgress(
        f"Finalized r{revision_number}",
        f"Finalized as r{revision_number}. No worksheets have been saved yet.",
        "ok",
    )


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
