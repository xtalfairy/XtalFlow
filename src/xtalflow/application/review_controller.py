from __future__ import annotations

from xtalflow.domain import (
    CrystalImage,
    ImageFilter,
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
        self.image_filter = ImageFilter.ALL
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
        return self._destination(-1) is not None

    @property
    def can_move_next(self) -> bool:
        return self._destination(1) is not None

    def move_previous(self) -> bool:
        destination = self._destination(-1)
        return destination is not None and self.move_to(destination)

    def move_next(self) -> bool:
        destination = self._destination(1)
        return destination is not None and self.move_to(destination)

    def change_image_filter(self, image_filter: ImageFilter) -> bool:
        self.image_filter = image_filter
        if self._matches_filter(self.image_index):
            return False
        candidates = self.filtered_indices
        if not candidates:
            return False
        next_index = next((index for index in candidates if index > self.image_index), None)
        return self.move_to(next_index if next_index is not None else candidates[0])

    def move_to_well(self, well_number: int) -> bool:
        destination = next(
            (
                index
                for index, image in enumerate(self.plate.images)
                if image.well_number == well_number
            ),
            None,
        )
        if destination is None:
            raise ValueError(f"well {well_number} is not available in this image set")
        if destination == self.image_index:
            return False
        return self.move_to(destination)

    @property
    def filtered_indices(self) -> tuple[int, ...]:
        return tuple(
            index
            for index in range(len(self.plate.images))
            if self._matches_filter(index)
        )

    @property
    def current_matches_filter(self) -> bool:
        return self._matches_filter(self.image_index)

    def _matches_filter(self, image_index: int) -> bool:
        image = self.plate.images[image_index]
        target_count = self.session.target_count_for(image)
        if self.image_filter is ImageFilter.WITH_TARGETS:
            return target_count > 0
        if self.image_filter is ImageFilter.WITHOUT_TARGETS:
            return self.session.is_reviewed(image) and target_count == 0
        if self.image_filter is ImageFilter.UNREVIEWED:
            return not self.session.is_reviewed(image)
        return True

    def _destination(self, direction: int) -> int | None:
        candidates = self.filtered_indices
        if direction < 0:
            return next(
                (index for index in reversed(candidates) if index < self.image_index),
                None,
            )
        return next((index for index in candidates if index > self.image_index), None)

    def move_to(self, image_index: int) -> bool:
        if not (0 <= image_index < len(self.plate.images)):
            return False
        previous_index = self.image_index
        previous_key = self.progress.current_image_key
        previous_updated_at = self.progress.updated_at
        outgoing_image = self.current_image
        outgoing_targets = self.current_targets
        was_reviewed = self.session.is_reviewed(outgoing_image)
        self.session.mark_reviewed(outgoing_image)
        self.image_index = image_index
        self.progress.move_to(self.current_image.image_key)
        try:
            if self.store is not None:
                self.store.save_checkpoint(
                    outgoing_image.image_key,
                    outgoing_targets,
                    self.progress,
                    self.preferences,
                    True,
                )
                self.has_unsaved_changes = False
        except ReviewPersistenceError:
            self.image_index = previous_index
            self.progress.current_image_key = previous_key
            self.progress.updated_at = previous_updated_at
            if not was_reviewed:
                self.session.unmark_reviewed(outgoing_image)
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

    def checkpoint_current(self, mark_reviewed: bool = False) -> None:
        was_reviewed = self.session.is_reviewed(self.current_image)
        if mark_reviewed:
            self.session.mark_reviewed(self.current_image)
        if self.store is not None:
            try:
                self.store.save_checkpoint(
                    self.current_image.image_key,
                    self.current_targets,
                    self.progress,
                    self.preferences,
                    mark_reviewed,
                )
                self.has_unsaved_changes = False
            except ReviewPersistenceError:
                if mark_reviewed and not was_reviewed:
                    self.session.unmark_reviewed(self.current_image)
                raise

    def persist_state(self) -> None:
        if self.store is not None:
            self.store.save_review_state(self.progress, self.preferences)
