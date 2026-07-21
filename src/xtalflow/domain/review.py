from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import hypot, isfinite
from uuid import uuid4
from enum import Enum

from .imaging import CrystalImage


class ImageFilter(str, Enum):
    ALL = "all"
    WITH_TARGETS = "with_targets"
    WITHOUT_TARGETS = "without_targets"
    UNREVIEWED = "unreviewed"


@dataclass(slots=True)
class ReviewProgress:
    plan_key: str
    plate_code: str
    batch_id: int
    profile: str
    current_image_key: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.plan_key or not self.plate_code or not self.profile:
            raise ValueError("review plan identity must not be empty")
        if self.batch_id < 0:
            raise ValueError("batch_id must be non-negative")
        if not self.current_image_key:
            raise ValueError("current_image_key must not be empty")

    @classmethod
    def create(
        cls,
        plate_code: str,
        batch_id: int,
        profile: str,
        current_image_key: str,
    ) -> ReviewProgress:
        now = datetime.now(UTC)
        return cls(
            plan_key=f"{plate_code}:{batch_id}:{profile}",
            plate_code=plate_code,
            batch_id=batch_id,
            profile=profile,
            current_image_key=current_image_key,
            created_at=now,
            updated_at=now,
        )

    def move_to(self, image_key: str) -> None:
        if not image_key:
            raise ValueError("image_key must not be empty")
        if image_key != self.current_image_key:
            self.current_image_key = image_key
            self.updated_at = datetime.now(UTC)


@dataclass(slots=True)
class ReviewPreferences:
    auto_advance_target_count: int = 1

    def __post_init__(self) -> None:
        if self.auto_advance_target_count < 1:
            raise ValueError("auto_advance_target_count must be positive")

    def change_auto_advance_target_count(self, count: int) -> None:
        if count < 1:
            raise ValueError("auto-advance target count must be positive")
        self.auto_advance_target_count = count


@dataclass(frozen=True, slots=True)
class TargetPoint:
    id: str
    image_key: str
    x_px: float
    y_px: float
    selected_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("target id must not be empty")
        if not self.image_key:
            raise ValueError("image_key must not be empty")
        if not isfinite(self.x_px) or not isfinite(self.y_px):
            raise ValueError("target coordinates must be finite")
        if self.x_px < 0 or self.y_px < 0:
            raise ValueError("target coordinates must be non-negative")
        if self.selected_at.tzinfo is None:
            raise ValueError("target selection time must include a timezone")


class ReviewSession:
    """In-memory target aggregate; persistence is deliberately kept outside the UI."""

    def __init__(self) -> None:
        self._targets_by_image: dict[str, list[TargetPoint]] = {}
        self._reviewed_image_keys: set[str] = set()

    def add_target(
        self,
        image: CrystalImage,
        x_px: float,
        y_px: float,
        image_width: int,
        image_height: int,
    ) -> TargetPoint:
        if image_width <= 0 or image_height <= 0:
            raise ValueError("image dimensions must be positive")
        if not (0 <= x_px < image_width and 0 <= y_px < image_height):
            raise ValueError("target must be inside the image")
        target = TargetPoint(
            str(uuid4()), image.image_key, x_px, y_px, datetime.now(UTC)
        )
        self._targets_by_image.setdefault(image.image_key, []).append(target)
        return target

    def targets_for(self, image: CrystalImage | str) -> tuple[TargetPoint, ...]:
        image_key = image.image_key if isinstance(image, CrystalImage) else image
        return tuple(self._targets_by_image.get(image_key, ()))

    def target_count_for(self, image: CrystalImage | str) -> int:
        return len(self.targets_for(image))

    def restore_targets(self, targets: tuple[TargetPoint, ...]) -> None:
        """Replace in-memory state with a previously persisted snapshot."""
        restored: dict[str, list[TargetPoint]] = {}
        for target in targets:
            restored.setdefault(target.image_key, []).append(target)
        self._targets_by_image = restored

    def restore_reviewed(self, image_keys: tuple[str, ...]) -> None:
        self._reviewed_image_keys = set(image_keys)

    def mark_reviewed(self, image: CrystalImage | str) -> None:
        image_key = image.image_key if isinstance(image, CrystalImage) else image
        self._reviewed_image_keys.add(image_key)

    def unmark_reviewed(self, image: CrystalImage | str) -> None:
        image_key = image.image_key if isinstance(image, CrystalImage) else image
        self._reviewed_image_keys.discard(image_key)

    def is_reviewed(self, image: CrystalImage | str) -> bool:
        image_key = image.image_key if isinstance(image, CrystalImage) else image
        return image_key in self._reviewed_image_keys

    @property
    def reviewed_count(self) -> int:
        return len(self._reviewed_image_keys)

    def remove_nearest(
        self,
        image: CrystalImage,
        x_px: float,
        y_px: float,
        radius_px: float,
    ) -> TargetPoint | None:
        if radius_px < 0:
            raise ValueError("radius must be non-negative")
        targets = self._targets_by_image.get(image.image_key, [])
        candidates = [
            (hypot(target.x_px - x_px, target.y_px - y_px), target)
            for target in targets
        ]
        if not candidates:
            return None
        distance, nearest = min(candidates, key=lambda candidate: candidate[0])
        if distance > radius_px:
            return None
        targets.remove(nearest)
        if not targets:
            self._targets_by_image.pop(image.image_key, None)
        return nearest

    def remove_target(self, target_id: str) -> TargetPoint | None:
        for image_key, targets in tuple(self._targets_by_image.items()):
            target = next((item for item in targets if item.id == target_id), None)
            if target is None:
                continue
            targets.remove(target)
            if not targets:
                self._targets_by_image.pop(image_key, None)
            return target
        return None

    @property
    def target_count(self) -> int:
        return sum(len(targets) for targets in self._targets_by_image.values())
