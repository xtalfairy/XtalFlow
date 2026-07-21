from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from .plate_format import SWISSCI_MIDI_3_LENS


@dataclass
class ProjectImageSet:
    id: str
    project_id: str
    plate_code: str
    batch_id: int
    profile: str
    display_order: int
    active_image_key: str
    created_at: datetime
    archived_at: datetime | None = None
    plate_format_id: str = SWISSCI_MIDI_3_LENS.id
    plate_format_version: int = SWISSCI_MIDI_3_LENS.version

    def __post_init__(self) -> None:
        if not self.id or not self.project_id:
            raise ValueError("project image-set identity must not be empty")
        if not self.plate_code.isdigit():
            raise ValueError("plate_code must contain digits only")
        if self.batch_id < 0:
            raise ValueError("batch_id must be non-negative")
        if not self.profile or not self.active_image_key:
            raise ValueError("profile and active image must not be empty")
        if self.display_order < 0:
            raise ValueError("display_order must be non-negative")
        if not self.plate_format_id:
            raise ValueError("plate format ID must not be empty")
        if self.plate_format_version < 1:
            raise ValueError("plate format version must be positive")

    @classmethod
    def create(
        cls,
        project_id: str,
        plate_code: str,
        batch_id: int,
        profile: str,
        display_order: int,
        active_image_key: str,
        plate_format_id: str = SWISSCI_MIDI_3_LENS.id,
        plate_format_version: int = SWISSCI_MIDI_3_LENS.version,
    ) -> ProjectImageSet:
        return cls(
            id=str(uuid4()),
            project_id=project_id,
            plate_code=plate_code,
            batch_id=batch_id,
            profile=profile,
            display_order=display_order,
            active_image_key=active_image_key,
            created_at=datetime.now(timezone.utc),
            plate_format_id=plate_format_id,
            plate_format_version=plate_format_version,
        )

    @property
    def source_key(self) -> tuple[str, int, str]:
        return self.plate_code, self.batch_id, self.profile

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


@dataclass
class Project:
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    active_image_set_id: str | None = None
    image_sets: list[ProjectImageSet] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("project name must not be empty")
        if any(image_set.project_id != self.id for image_set in self.image_sets):
            raise ValueError("all image sets must belong to the project")
        active_ids = {image_set.id for image_set in self.active_image_sets}
        if self.active_image_set_id is not None and self.active_image_set_id not in active_ids:
            raise ValueError("active image set must be an active project member")

    @classmethod
    def create(cls, name: str) -> Project:
        now = datetime.now(timezone.utc)
        return cls(str(uuid4()), name, now, now)

    @property
    def active_image_sets(self) -> tuple[ProjectImageSet, ...]:
        return tuple(
            sorted(
                (image_set for image_set in self.image_sets if not image_set.is_archived),
                key=lambda image_set: image_set.display_order,
            )
        )

    def rename(self, name: str) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("project name must not be empty")
        self.name = normalized
        if hasattr(self, "updated_at"):
            self.updated_at = datetime.now(timezone.utc)

    def add_image_set(
        self,
        plate_code: str,
        batch_id: int,
        profile: str,
        active_image_key: str,
        plate_format_id: str = SWISSCI_MIDI_3_LENS.id,
        plate_format_version: int = SWISSCI_MIDI_3_LENS.version,
    ) -> ProjectImageSet:
        source_key = (plate_code, batch_id, profile)
        if any(item.source_key == source_key for item in self.image_sets):
            raise ValueError(
                "this image set is already in the project or its archive"
            )
        image_set = ProjectImageSet.create(
            self.id,
            plate_code,
            batch_id,
            profile,
            len(self.active_image_sets),
            active_image_key,
            plate_format_id,
            plate_format_version,
        )
        self.image_sets.append(image_set)
        self.active_image_set_id = image_set.id
        self.updated_at = datetime.now(timezone.utc)
        return image_set

    def activate(self, image_set_id: str) -> None:
        if image_set_id not in {item.id for item in self.active_image_sets}:
            raise ValueError("image set is not an active project member")
        self.active_image_set_id = image_set_id
        self.updated_at = datetime.now(timezone.utc)

    def move_image_set(self, image_set_id: str, offset: int) -> None:
        ordered = list(self.active_image_sets)
        current = next(
            (index for index, item in enumerate(ordered) if item.id == image_set_id), None
        )
        if current is None:
            raise ValueError("image set is not an active project member")
        destination = current + offset
        if not 0 <= destination < len(ordered):
            return
        ordered[current], ordered[destination] = ordered[destination], ordered[current]
        for order, image_set in enumerate(ordered):
            image_set.display_order = order
        self.updated_at = datetime.now(timezone.utc)

    def archive_image_set(self, image_set_id: str) -> None:
        image_set = next(
            (item for item in self.active_image_sets if item.id == image_set_id), None
        )
        if image_set is None:
            raise ValueError("image set is not an active project member")
        image_set.archived_at = datetime.now(timezone.utc)
        for order, item in enumerate(self.active_image_sets):
            item.display_order = order
        if self.active_image_set_id == image_set_id:
            active = self.active_image_sets
            self.active_image_set_id = active[0].id if active else None
        self.updated_at = datetime.now(timezone.utc)

    def restore_image_set(self, image_set_id: str) -> ProjectImageSet:
        image_set = next(
            (item for item in self.image_sets if item.id == image_set_id and item.is_archived),
            None,
        )
        if image_set is None:
            raise ValueError("image set is not archived")
        if any(
            item.source_key == image_set.source_key and not item.is_archived
            for item in self.image_sets
        ):
            raise ValueError("an active image set already uses this source")
        image_set.archived_at = None
        image_set.display_order = len(self.active_image_sets) - 1
        self.active_image_set_id = image_set.id
        self.updated_at = datetime.now(timezone.utc)
        return image_set
