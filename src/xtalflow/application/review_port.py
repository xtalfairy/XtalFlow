from __future__ import annotations

from typing import Protocol

from xtalflow.domain import ReviewPreferences, ReviewProgress, TargetPoint


class ReviewPersistenceError(RuntimeError):
    """A review checkpoint could not be stored or loaded safely."""


class ReviewStorePort(Protocol):
    def save_review_state(
        self, progress: ReviewProgress, preferences: ReviewPreferences
    ) -> None: ...

    def save_checkpoint(
        self,
        image_key: str,
        targets: tuple[TargetPoint, ...],
        progress: ReviewProgress,
        preferences: ReviewPreferences,
    ) -> None: ...
