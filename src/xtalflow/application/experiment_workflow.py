"""Where an experiment stands in its guided steps, computed in one place.

Screens show these results instead of deciding readiness on their own, so the
stepper, footer, and Finalize button always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xtalflow.domain.experiment_naming import normalize_protein_name
from xtalflow.domain.experiment_project import PlanType


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
    # Condition tests: crystals the included conditions need, a one-line summary
    # of them, a problem found only with the chosen wells, and wells left over.
    required_well_count: int = 0
    conditions_summary: str = ""
    selection_error: str | None = None
    unused_well_count: int = 0


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


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}{'' if value == 1 else 's'}"


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

    selection = f"{_count(facts.well_count, 'well')} · {_count(facts.position_count, 'position')}"
    condition_test = facts.plan_type is PlanType.CONDITION_TEST
    if condition_test and facts.required_well_count and facts.well_count < facts.required_well_count:
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
            f"{_count(facts.wells_needing_attention, 'well')} "
            f"{'needs' if facts.wells_needing_attention == 1 else 'need'} attention · {selection}",
        )
    else:
        statuses[WorkflowStep.SELECT_WELLS] = StepStatus(
            WorkflowStep.SELECT_WELLS, StepState.COMPLETE, selection
        )

    if condition_test and facts.unused_well_count:
        notes.append(
            f"The last {_count(facts.unused_well_count, 'selected well')} "
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
            message = f"Fragments assigned to {_count(facts.well_count, 'well')}"
            if facts.unused_fragment_count:
                notes.append(
                    f"The last {_count(facts.unused_fragment_count, 'fragment')} "
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

    if finalized and facts.worksheets_saved_for_revision:
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
