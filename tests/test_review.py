from pathlib import Path

import pytest

from xtalflow.domain import (
    CrystalImage,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
)


def image(drop_number: int = 1) -> CrystalImage:
    return CrystalImage("1070", 14122, 1, drop_number, "profileID_1", Path("image.jpg"))


def test_targets_are_stored_in_original_image_coordinates() -> None:
    session = ReviewSession()
    target = session.add_target(image(), 612.5, 511.25, 1224, 1024)

    assert target.x_px == 612.5
    assert target.y_px == 511.25
    assert session.targets_for(image()) == (target,)
    assert session.target_count == 1


def test_targets_are_isolated_by_logical_image() -> None:
    session = ReviewSession()
    session.add_target(image(1), 10, 20, 100, 100)

    assert len(session.targets_for(image(1))) == 1
    assert session.targets_for(image(2)) == ()


def test_nearest_target_is_removed_only_inside_radius() -> None:
    session = ReviewSession()
    first = session.add_target(image(), 10, 10, 100, 100)
    second = session.add_target(image(), 50, 50, 100, 100)

    assert session.remove_nearest(image(), 45, 50, 3) is None
    assert session.remove_nearest(image(), 45, 50, 6) == second
    assert session.targets_for(image()) == (first,)


def test_target_outside_image_is_rejected() -> None:
    session = ReviewSession()

    with pytest.raises(ValueError):
        session.add_target(image(), 100, 20, 100, 100)


def test_review_progress_and_preferences_have_separate_responsibilities() -> None:
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "first")
    preferences = ReviewPreferences(2)

    preferences.change_auto_advance_target_count(3)
    progress.move_to("second")

    assert preferences.auto_advance_target_count == 3
    assert progress.current_image_key == "second"
