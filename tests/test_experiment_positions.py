"""Positions belong to each experiment; well calibrations are shared."""

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from xtalflow.application import ProjectController
from xtalflow.application.planning_service import PlanningService
from xtalflow.domain import (
    SWISSCI_MIDI_3_LENS,
    ImageCalibration,
    PlanType,
    Project,
    ReviewPreferences,
    ReviewProgress,
    TargetPoint,
    crystal_selection_from_selected_crystals,
)
from xtalflow.domain.fragment_screening import CrystalTarget, SelectedCrystal
from xtalflow.domain.plan_lifecycle import PlanningDraft
from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
from xtalflow.settings import DEFAULT_SETTINGS

FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _workspace(store: SQLiteReviewStore):
    project = Project.create("Workspace")
    image_set = project.add_image_set("1070", 5947, "profileID_1", "image-a")
    store.workspace.save_project(project)
    return project, image_set


def _checkpoint(scoped, image_key: str, target_id: str) -> None:
    progress = ReviewProgress.create("1070", 5947, "profileID_1", image_key)
    scoped.save_checkpoint(
        image_key, (TargetPoint(target_id, image_key, 10, 20, NOW),), progress,
        ReviewPreferences(1), True,
    )


def test_each_experiment_keeps_its_own_positions_and_shares_calibration(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project, image_set = _workspace(store)
    first = store.workspace.scoped_to(image_set.id, "experiment-a")
    second = store.workspace.scoped_to(image_set.id, "experiment-b")

    _checkpoint(first, "image-a", "target-a")
    first.save_calibration(ImageCalibration.automatic("image-a", 100, 100, 50, 0.9, 2.77))

    assert [target.id for target in first.load_images(("image-a",))] == ["target-a"]
    assert second.load_images(("image-a",)) == ()
    assert second.load_reviewed_images(("image-a",)) == ()
    assert second.load_calibration("image-a") is not None
    assert store.workspace.target_count_for_image_set(image_set.id, "experiment-a") == 1
    assert store.workspace.experiments_using_images(image_set.id, "experiment-b") == {
        "image-a": ("experiment-a",)
    }
    store.workspace.delete_targets(("target-a",), "experiment-b")
    assert len(first.load_images(("image-a",))) == 1
    store.close()


def test_shared_workspace_positions_can_be_adopted_once_by_an_experiment(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project, image_set = _workspace(store)
    _checkpoint(store.workspace.scoped_to(image_set.id), "image-a", "shared-target")

    assert store.workspace.unassigned_workspace_position_count(project.id) == 1
    assert store.workspace.adopt_workspace_positions(project.id, "experiment-a") == 1

    adopted = store.workspace.scoped_to(image_set.id, "experiment-a")
    assert [target.id for target in adopted.load_images(("image-a",))] == ["shared-target"]
    assert adopted.load_reviewed_images(("image-a",)) == ("image-a",)
    assert store.workspace.unassigned_workspace_position_count(project.id) == 0
    with pytest.raises(ValueError):
        store.workspace.adopt_workspace_positions(project.id, "")
    store.close()


def test_schema17_experiments_receive_copies_of_the_positions_they_fixed(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    project, image_set = _workspace(store)
    shared = store.workspace.scoped_to(image_set.id)
    _checkpoint(shared, "image-a", "used-target")
    _checkpoint(shared, "image-b", "spare-target")
    store.planning.save_planning_draft(PlanningDraft(
        "experiment-a", project.id, "raw_crystal", "Harvest", None, "", "BRD4", "0",
        "selection", NOW, NOW,
    ))
    crystal = SelectedCrystal(
        "image-a", "1070", "A01a",
        (CrystalTarget("used-target", Decimal(0), Decimal(0), NOW),),
        SWISSCI_MIDI_3_LENS.id,
    )
    PlanningService(store.planning).save_selection_snapshot(
        "experiment-a", "Harvest", PlanType.RAW_CRYSTAL,
        crystal_selection_from_selected_crystals("experiment-a", (crystal,)), NOW,
    )
    store.close()

    connection = sqlite3.connect(database_path)
    for table, columns, key in (
        ("image_set_target_point",
         "target_id, image_set_id, image_key, x_px, y_px, selected_at", "target_id"),
        ("image_set_image_review", "image_set_id, image_key, reviewed_at",
         "image_set_id, image_key"),
        ("image_set_review_state",
         "image_set_id, auto_advance_target_count, current_image_key, created_at, updated_at",
         "image_set_id"),
    ):
        connection.execute(f"CREATE TABLE old AS SELECT {columns} FROM {table}")
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM old WHERE 0")
        connection.execute(f"CREATE UNIQUE INDEX {table}_key ON {table}({key})")
        connection.execute(f"INSERT INTO {table} SELECT * FROM old")
        connection.execute("DROP TABLE old")
    connection.execute("PRAGMA user_version = 17")
    connection.commit()
    connection.close()

    migrated = SQLiteReviewStore(database_path)
    experiment = migrated.workspace.scoped_to(image_set.id, "experiment-a")

    assert [target.id for target in experiment.load_images(("image-a", "image-b"))] == [
        "used-target"
    ]
    assert experiment.load_reviewed_images(("image-a",)) == ("image-a",)
    assert migrated.workspace.unassigned_workspace_position_count(project.id) == 1
    assert migrated.upgrade_backup_path is not None
    assert migrated._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    migrated.close()


def test_resume_step_is_saved_and_kept_when_not_given(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project, _ = _workspace(store)
    draft = PlanningDraft(
        "experiment", project.id, "fragment_screening", "Screen", None, "", "BRD4",
        "25", "selection", NOW, NOW, workflow_step="conditions",
    )
    store.planning.save_planning_draft(draft)
    store.planning.save_planning_draft(
        PlanningDraft(**{**draft.__dict__, "protein": "BRD9", "workflow_step": None})
    )

    restored = store.planning.load_planning_drafts(project.id)[0]
    assert (restored.protein, restored.workflow_step) == ("BRD9", "conditions")
    store.close()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_opening_another_experiment_shows_only_its_positions(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store.workspace)
    workspace.create_project("Workspace")
    workspace.add_pinned_image_set("1070", 5947, "profileID_1", SWISSCI_MIDI_3_LENS)

    workspace.open_experiment("experiment-a")
    workspace.review_controller.add_target(500, 500, 1224, 1024)
    workspace.review_controller.checkpoint_current()
    workspace.open_experiment("experiment-b")

    assert workspace.review_controller.session.target_count == 0
    assert workspace.image_set_review_statistics()
    workspace.open_experiment("experiment-a")
    assert workspace.review_controller.session.target_count == 1
    store.close()


def test_deleting_an_experiment_removes_only_its_positions(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project, image_set = _workspace(store)
    kept = store.workspace.scoped_to(image_set.id, "experiment-a")
    deleted = store.workspace.scoped_to(image_set.id, "experiment-b")
    _checkpoint(kept, "image-a", "target-a")
    _checkpoint(deleted, "image-a", "target-b")

    store.workspace.delete_experiment_positions("experiment-b")

    assert [target.id for target in kept.load_images(("image-a",))] == ["target-a"]
    assert deleted.load_images(("image-a",)) == ()
    with pytest.raises(ValueError):
        store.workspace.delete_experiment_positions("")
    store.close()


def test_draft_selection_snapshot_can_be_replaced_and_recent_drafts_listed(
    tmp_path: Path,
) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project, _ = _workspace(store)
    service = PlanningService(store.planning)
    crystals = {
        key: SelectedCrystal(
            key, "1070", well, (CrystalTarget(f"{key}-target", Decimal(0), Decimal(0), NOW),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for key, well in (("image-a", "A01a"), ("image-b", "B01a"))
    }
    for key in ("image-a", "image-b"):
        service.save_selection_snapshot(
            "plan-1", "Harvest", PlanType.RAW_CRYSTAL,
            crystal_selection_from_selected_crystals("plan-1", (crystals[key],)), NOW,
        )
    for plan_id, minutes in (("plan-1", 1), ("plan-2", 5)):
        edited = NOW.replace(minute=minutes)
        store.planning.save_planning_draft(
            PlanningDraft(
                plan_id, project.id, "raw_crystal", plan_id, None, "", "", "0",
                "selection", NOW, edited, None, "select_wells",
            )
        )

    saved = store.planning.load_experiment_project("plan-1")
    assert [well.image_key for well in saved.crystal_selection.wells] == ["image-b"]
    assert [draft.id for draft in store.planning.load_recent_drafts()] == [
        "plan-2", "plan-1"
    ]
    store.close()
