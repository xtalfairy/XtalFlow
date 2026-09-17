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


def test_conditions_are_not_complete_before_wells_are_selected() -> None:
    status = evaluate_experiment(replace(READY_FRAGMENT, well_count=0, position_count=0))

    conditions = status.status_of(WorkflowStep.CONDITIONS)
    assert conditions.state is StepState.INCOMPLETE
    assert conditions.message == "Select wells to check the fragment assignment."
    assert status.status_of(WorkflowStep.SETUP).message == "Protein: BRD4"


def test_condition_test_sets_conditions_before_wells_and_counts_crystals() -> None:
    facts = ExperimentFacts(
        PlanType.CONDITION_TEST, "BRD4", well_count=10, position_count=10,
        required_well_count=16, conditions_summary="8 conditions · 16 crystals",
    )
    status = evaluate_experiment(facts)

    assert steps_for(PlanType.CONDITION_TEST)[1] is WorkflowStep.CONDITIONS
    assert status.status_of(WorkflowStep.CONDITIONS).message == "8 conditions · 16 crystals"
    assert status.status_of(WorkflowStep.SELECT_WELLS).message == "10 of 16 wells selected"
    assert not status.ready_to_finalize

    enough = evaluate_experiment(replace(facts, well_count=17, position_count=17, unused_well_count=1))
    assert enough.ready_to_finalize
    assert enough.notes == ("The last 1 selected well is not used.",)
    split = evaluate_experiment(
        replace(facts, well_count=16, selection_error="A01a: cannot be split")
    )
    assert split.status_of(WorkflowStep.SELECT_WELLS).state is StepState.ATTENTION
    assert not split.ready_to_finalize
