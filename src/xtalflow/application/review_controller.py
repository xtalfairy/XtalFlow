from __future__ import annotations

from xtalflow.domain import (
    CrystalImage,
    PlateImages,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
    TargetPoint,
)
from .review_port import ReviewPersistenceError, ReviewStorePort


class ReviewController:
    """Application rules for one loaded plate, independent of Qt widgets and storage."""

    def __init__(
        self,
        plate: PlateImages,
        progress: ReviewProgress,
        preferences: ReviewPreferences,
        session: ReviewSession,
        store: ReviewStorePort | None = None,
    ) -> None:
        self.plate = plate
        self.progress = progress
        self.preferences = preferences
        self.session = session
        self.store = store
        self.has_unsaved_changes = False
        self._index_by_key = {
            image.image_key: index for index, image in enumerate(plate.images)
        }
        self.image_index = self._index_by_key.get(progress.current_image_key, 0)
        if progress.current_image_key not in self._index_by_key:
            progress.move_to(plate.images[0].image_key)

    @property
    def current_image(self) -> CrystalImage:
        return self.plate.images[self.image_index]

    @property
    def current_targets(self) -> tuple[TargetPoint, ...]:
        return self.session.targets_for(self.current_image)

    @property
    def can_move_previous(self) -> bool:
        return self.image_index > 0

    @property
    def can_move_next(self) -> bool:
        return self.image_index < len(self.plate.images) - 1

    def move_previous(self) -> bool:
        return self.move_to(self.image_index - 1)

    def move_next(self) -> bool:
        return self.move_to(self.image_index + 1)

    def move_to(self, image_index: int) -> bool:
        if not (0 <= image_index < len(self.plate.images)):
            return False
        previous_index = self.image_index
        previous_key = self.progress.current_image_key
        previous_updated_at = self.progress.updated_at
        outgoing_image = self.current_image
        outgoing_targets = self.current_targets
        self.image_index = image_index
        self.progress.move_to(self.current_image.image_key)
        try:
            if self.store is not None:
                self.store.save_checkpoint(
                    outgoing_image.image_key,
                    outgoing_targets,
                    self.progress,
                    self.preferences,
                )
                self.has_unsaved_changes = False
        except ReviewPersistenceError:
            self.image_index = previous_index
            self.progress.current_image_key = previous_key
            self.progress.updated_at = previous_updated_at
            raise
        return True

    def add_target(
        self, x_px: float, y_px: float, image_width: int, image_height: int
    ) -> bool:
        self.session.add_target(
            self.current_image, x_px, y_px, image_width, image_height
        )
        self.has_unsaved_changes = True
        return (
            self.session.target_count_for(self.current_image)
            == self.preferences.auto_advance_target_count
        )

    def remove_nearest_target(
        self, x_px: float, y_px: float, radius_px: float
    ) -> TargetPoint | None:
        removed = self.session.remove_nearest(
            self.current_image, x_px, y_px, radius_px
        )
        if removed is not None:
            self.has_unsaved_changes = True
        return removed

    def change_auto_advance_target_count(self, count: int) -> None:
        previous_count = self.preferences.auto_advance_target_count
        self.preferences.change_auto_advance_target_count(count)
        try:
            self.persist_state()
        except ReviewPersistenceError:
            self.preferences.auto_advance_target_count = previous_count
            raise

    def checkpoint_current(self) -> None:
        if self.store is not None:
            self.store.save_checkpoint(
                self.current_image.image_key,
                self.current_targets,
                self.progress,
                self.preferences,
            )
            self.has_unsaved_changes = False

    def persist_state(self) -> None:
        if self.store is not None:
            self.store.save_review_state(self.progress, self.preferences)
