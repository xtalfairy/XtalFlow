from dataclasses import replace

from xtalflow.application.experiment_workflow import (
    ExperimentFacts,
    StepState,
    WorkflowStep,
    evaluate_experiment,
    steps_for,
)
from xtalflow.domain import PlanType


READY_FRAGMENT = ExperimentFacts(
    PlanType.FRAGMENT_SCREENING, "BRD4", well_count=12, position_count=19
)


def _states(status):
    return {item.step: item.state for item in status.steps}


def test_raw_crystal_has_no_conditions_step() -> None:
    assert WorkflowStep.CONDITIONS not in steps_for(PlanType.RAW_CRYSTAL)
    assert steps_for(PlanType.FRAGMENT_SCREENING)[2] is WorkflowStep.CONDITIONS


def test_new_experiment_points_to_setup_and_cannot_finalize() -> None:
    status = evaluate_experiment(
        ExperimentFacts(PlanType.FRAGMENT_SCREENING, "", 0, 0, conditions_error="select a library")
    )

    assert not status.ready_to_finalize
    assert status.first_unfinished is WorkflowStep.SETUP
    assert status.status_of(WorkflowStep.SETUP).message == "Enter a protein name to continue."
    assert status.status_of(WorkflowStep.SELECT_WELLS).message == (
        "Select at least one well to continue."
    )
    assert status.status_of(WorkflowStep.REVIEW).message == "Complete the earlier steps first."


def test_attention_in_selection_or_conditions_blocks_finalizing() -> None:
    boundary = evaluate_experiment(replace(READY_FRAGMENT, wells_needing_attention=2))
    volume = evaluate_experiment(
        replace(READY_FRAGMENT, conditions_error="25 nL cannot be split equally")
    )

    assert _states(boundary)[WorkflowStep.SELECT_WELLS] is StepState.ATTENTION
    assert boundary.status_of(WorkflowStep.SELECT_WELLS).message.startswith(
        "2 wells need attention"
    )
    assert not boundary.ready_to_finalize
    assert _states(volume)[WorkflowStep.CONDITIONS] is StepState.ATTENTION
    assert not volume.ready_to_finalize


def test_unused_fragments_are_allowed_but_reported() -> None:
    status = evaluate_experiment(replace(READY_FRAGMENT, unused_fragment_count=3))

    assert status.ready_to_finalize
    assert status.notes == ("The last 3 fragments in the chosen rows will not be used.",)
    assert status.status_of(WorkflowStep.REVIEW).message == "Ready to finalize"


def test_revision_and_worksheet_states() -> None:
    finalized = evaluate_experiment(
        replace(READY_FRAGMENT, revision_number=1, revision_matches_current=True)
    )
    saved = evaluate_experiment(
        replace(
            READY_FRAGMENT, revision_number=1, revision_matches_current=True,
            worksheets_saved_for_revision=True,
        )
    )
    changed = evaluate_experiment(replace(READY_FRAGMENT, revision_number=1))

    assert not finalized.ready_to_finalize
    assert finalized.status_of(WorkflowStep.WORKSHEETS).message == "Ready to save worksheets"
    assert finalized.first_unfinished is WorkflowStep.WORKSHEETS
    assert all(state is StepState.COMPLETE for state in _states(saved).values())
    assert changed.ready_to_finalize
    assert changed.status_of(WorkflowStep.REVIEW).message.startswith("Changes after r1")
    assert changed.status_of(WorkflowStep.WORKSHEETS).state is StepState.INCOMPLETE


def test_unsaved_changes_block_finalizing() -> None:
    status = evaluate_experiment(replace(READY_FRAGMENT, local_save_failed=True))

    assert not status.ready_to_finalize
    assert "not saved" in status.status_of(WorkflowStep.REVIEW).message
