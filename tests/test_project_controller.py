from pathlib import Path

import pytest

from xtalflow.application import ProjectController, ReviewPersistenceError
from xtalflow.domain import ImageFilter, SWISSCI_MIDI_3_LENS
from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "rmserver"


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_project_controller_switches_pinned_image_sets_and_restores_active(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store)
    project = workspace.create_project("FBDD")
    first = workspace.add_latest_image_set("1070", SWISSCI_MIDI_3_LENS)
    second = workspace.add_latest_image_set("1100", SWISSCI_MIDI_3_LENS)

    assert workspace.review_controller.plate.batch_id == 6088
    workspace.activate_image_set(first.id)
    assert workspace.review_controller.plate.batch_id == 5947
    assert project.active_image_set_id == first.id
    store.close()

    restored_store = SQLiteReviewStore(database_path)
    restored = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), restored_store)
    restored.open_project(project.id)
    assert restored.review_controller.plate.plate_code == "1070"
    assert [item.plate_code for item in restored.active_project.active_image_sets] == [
        "1070",
        "1100",
    ]
    restored_store.close()


def test_project_controller_remembers_last_open_project(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store)
    workspace.create_project("First")
    last_project = workspace.create_project("Last opened")
    store.close()

    restored_store = SQLiteReviewStore(database_path)
    restored = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), restored_store)

    assert restored.last_open_project_id == last_project.id
    restored.open_project(restored.last_open_project_id)
    assert restored.active_project.name == "Last opened"
    restored_store.close()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_project_navigation_crosses_plate_boundary_in_project_order(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store)
    workspace.create_project("Cross-plate")
    first = workspace.add_latest_image_set("1070", SWISSCI_MIDI_3_LENS)
    workspace.add_latest_image_set("1100", SWISSCI_MIDI_3_LENS)
    workspace.activate_image_set(first.id)
    workspace.review_controller.move_to(len(workspace.review_controller.plate.images) - 1)

    assert workspace.move_across_image_sets(1, ImageFilter.ALL)
    assert workspace.review_controller.plate.plate_code == "1100"
    assert workspace.review_controller.image_index == 0
    assert workspace.move_across_image_sets(-1, ImageFilter.ALL)
    assert workspace.review_controller.plate.plate_code == "1070"
    assert workspace.review_controller.image_index == len(workspace.review_controller.plate.images) - 1
    store.close()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_project_navigation_uses_target_filter_and_reports_totals(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store)
    workspace.create_project("Filtered")
    first = workspace.add_latest_image_set("1070", SWISSCI_MIDI_3_LENS)
    workspace.add_latest_image_set("1100", SWISSCI_MIDI_3_LENS)
    target_image_key = workspace.review_controller.current_image.image_key
    workspace.review_controller.add_target(10, 10, 100, 100)
    workspace.review_controller.checkpoint_current()
    workspace.activate_image_set(first.id)

    assert workspace.move_across_image_sets(1, ImageFilter.WITH_TARGETS)
    assert workspace.review_controller.current_image.image_key == target_image_key
    statistics = workspace.project_review_statistics()
    assert statistics.total_images > 1
    assert statistics.reviewed_images >= 1
    assert statistics.target_images == 1
    assert statistics.target_points == 1
    assert statistics.unreviewed_images == statistics.total_images - statistics.reviewed_images
    store.close()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_failed_cross_plate_activation_keeps_original_plate(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    workspace = ProjectController(RockMakerImageRepository(FIXTURE_ROOT), store)
    workspace.create_project("Failure")
    first = workspace.add_latest_image_set("1070", SWISSCI_MIDI_3_LENS)
    second = workspace.add_latest_image_set("1100", SWISSCI_MIDI_3_LENS)
    workspace.activate_image_set(first.id)
    store._connection.execute(
        f"""
        CREATE TRIGGER reject_second_state BEFORE UPDATE ON image_set_review_state
        WHEN NEW.image_set_id = '{second.id}'
        BEGIN SELECT RAISE(ABORT, 'test failure'); END
        """
    )

    with pytest.raises(ReviewPersistenceError):
        workspace.move_across_image_sets(1, ImageFilter.ALL)

    assert workspace.active_project.active_image_set_id == first.id
    assert workspace.review_controller.plate.plate_code == "1070"
    store.close()
