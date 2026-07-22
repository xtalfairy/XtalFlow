from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from xtalflow.domain import (
    ExperimentPlan,
    ExperimentProject,
    PlanType,
    SWISSCI_MIDI_3_LENS,
    crystal_selection_from_selected_crystals,
    selected_crystals_from_crystal_selection,
)
from xtalflow.domain.crystal_workflow import CrystalTarget, SelectedCrystal


def _selected_crystal(image_key: str = "image") -> SelectedCrystal:
    now = datetime.now(timezone.utc)
    return SelectedCrystal(
        image_key,
        "2069",
        "A04a",
        (
            CrystalTarget("target-1", Decimal("0.1"), Decimal("-0.2"), now),
            CrystalTarget(
                "target-2", Decimal("0.3"), Decimal("0.4"),
                now + timedelta(seconds=1),
            ),
        ),
        SWISSCI_MIDI_3_LENS.id,
        "/rmserver/image.jpg",
    )


def test_click_targets_become_one_selected_well_with_soaking_positions() -> None:
    selection = crystal_selection_from_selected_crystals(
        "project-1", (_selected_crystal(),), selection_id="selection-1"
    )

    assert len(selection.wells) == 1
    well = selection.wells[0]
    assert (well.plate_code, well.well_address) == ("2069", "A04a")
    assert well.image_path == "/rmserver/image.jpg"
    assert [position.position_order for position in well.soaking_positions] == [1, 2]
    assert [position.source_target_id for position in well.soaking_positions] == [
        "target-1", "target-2"
    ]
    restored = selected_crystals_from_crystal_selection(selection)
    assert restored[0].image_key == "image"
    assert restored[0].destination_well == "A04a"
    assert [target.target_id for target in restored[0].targets] == [
        "target-1", "target-2"
    ]


def test_same_source_well_can_be_reused_by_independent_projects() -> None:
    source = _selected_crystal()
    first = crystal_selection_from_selected_crystals("project-1", (source,))
    second = crystal_selection_from_selected_crystals("project-2", (source,))

    assert first.wells[0].image_key == second.wells[0].image_key
    assert first.wells[0].id != second.wells[0].id
    assert first.wells[0].soaking_positions[0].id != (
        second.wells[0].soaking_positions[0].id
    )


def test_experiment_project_combines_one_selection_and_one_plan() -> None:
    now = datetime.now(timezone.utc)
    selection = crystal_selection_from_selected_crystals(
        "project-1", (_selected_crystal(),)
    )
    plan = ExperimentPlan(
        "plan-1", "project-1", PlanType.FRAGMENT_SCREENING, now, now
    )

    project = ExperimentProject(
        "project-1", "BRD4 screen", selection, plan, now, now
    )

    assert project.crystal_selection is selection
    assert project.plan is plan


def test_project_rejects_selection_owned_by_another_project() -> None:
    now = datetime.now(timezone.utc)
    selection = crystal_selection_from_selected_crystals(
        "project-1", (_selected_crystal(),)
    )
    plan = ExperimentPlan("plan-2", "project-2", PlanType.RAW_CRYSTAL, now, now)

    with pytest.raises(ValueError, match="experiment plan"):
        ExperimentProject("project-1", "Mismatch", selection, plan, now, now)
