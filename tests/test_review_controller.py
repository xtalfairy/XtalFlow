from pathlib import Path

import pytest

from xtalflow.application import ReviewController
from xtalflow.application import ReviewPersistenceError
from xtalflow.domain import (
    CrystalImage,
    PlateImages,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
)


def controller(auto_advance_count: int = 2) -> ReviewController:
    images = tuple(
        CrystalImage("1070", 5947, 1, drop, "profileID_1", Path(f"{drop}.jpg"))
        for drop in (1, 2)
    )
    plate = PlateImages("1070", 5947, "profileID_1", images)
    progress = ReviewProgress.create(
        plate.plate_code, plate.batch_id, plate.profile, images[0].image_key
    )
    return ReviewController(
        plate, progress, ReviewPreferences(auto_advance_count), ReviewSession()
    )


def test_controller_reports_auto_advance_without_forcing_navigation() -> None:
    review = controller(2)

    assert not review.add_target(10, 10, 100, 100)
    assert review.add_target(20, 20, 100, 100)
    assert review.image_index == 0


def test_controller_navigation_updates_progress() -> None:
    review = controller()

    assert review.move_next()
    assert review.image_index == 1
    assert review.progress.current_image_key == review.current_image.image_key
    assert not review.move_next()
    assert review.move_previous()


def test_auto_advance_preference_does_not_limit_actual_targets() -> None:
    review = controller(2)

    for coordinate in (10, 20, 30):
        review.add_target(coordinate, coordinate, 100, 100)

    assert review.session.target_count_for(review.current_image) == 3


class FailingStore:
    def save_checkpoint(self, *args) -> None:
        raise ReviewPersistenceError("disk unavailable")

    def save_review_state(self, *args) -> None:
        raise ReviewPersistenceError("disk unavailable")


def test_controller_rolls_back_navigation_when_checkpoint_fails() -> None:
    review = controller()
    review.store = FailingStore()
    original_key = review.progress.current_image_key

    with pytest.raises(ReviewPersistenceError):
        review.move_next()

    assert review.image_index == 0
    assert review.progress.current_image_key == original_key
