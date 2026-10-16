from __future__ import annotations

import re
from pathlib import Path

from xtalflow.domain import CrystalImage, PlateImages
from xtalflow.infrastructure.mounts import is_network_mount


class InvalidPlateCodeError(ValueError):
    """Raised when a plate code cannot be interpreted."""


class PlateImagesNotFoundError(FileNotFoundError):
    """Raised when no usable images exist for a plate/profile."""


class ImageSourceUnavailable(OSError):
    """The image share itself cannot be read, so no plate can be found on it.

    An unmounted mountpoint is an ordinary empty folder, so a missing plate and
    a dropped share look alike until the root itself is checked. Callers that
    catch OSError keep catching this; callers that can offer to look again
    catch it by name.
    """

    def __init__(self, root: Path, reason: str) -> None:
        super().__init__(f"plate images on {root} cannot be read: {reason}")
        self.root = root
        self.reason = reason


def natural_name_key(value: str) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )


def latest_image_source(repository, plate_code: str) -> tuple[int, str]:
    """Return the newest batch that already has images and its highest profile.

    RockMaker creates batch folders for scheduled inspections before imaging, so
    the newest folder alone may still be empty.
    """
    batches = repository.available_batches(plate_code)
    if not batches:
        raise PlateImagesNotFoundError(f"no batches found for plate {plate_code}")
    for batch_id in sorted(batches, reverse=True):
        profiles = repository.available_profiles(plate_code, batch_id)
        if profiles:
            return batch_id, max(profiles, key=natural_name_key)
    raise PlateImagesNotFoundError(f"no imaged batches found for plate {plate_code}")


class RockMakerImageRepository:
    """Read crystal images from the legacy RockMaker directory layout."""

    _BATCH_PATTERN = re.compile(r"^batchID_(\d+)$")
    _WELL_PATTERN = re.compile(r"^wellNum_(\d+)$")
    _IMAGE_PATTERN = re.compile(r"^d(\d+)_.*_ef\.jpg$", re.IGNORECASE)
    _REVISION_PATTERN = re.compile(r"_r(\d+)_", re.IGNORECASE)

    def __init__(
        self,
        root: Path | str,
        require_network_mount: bool = False,
        mount_table: str | None = None,
    ) -> None:
        self.root = Path(root).expanduser()
        # Operating installations read a mounted share; a folder in its place
        # means the share is gone, not that every plate has been deleted.
        self.require_network_mount = require_network_mount
        self.mount_table = mount_table

    def _entries(self, directory: Path) -> tuple[Path, ...]:
        """A folder's contents; a share that drops mid-read says so."""
        try:
            return tuple(directory.iterdir())
        except OSError as error:
            raise ImageSourceUnavailable(self.root, str(error)) from error

    def _explain_missing(self, path: Path, plate_code: str = "") -> Exception:
        """Why ``path`` is not there: the share, or the plate itself.

        ``plate_code`` is given when the plate's own folder is missing, so the
        window can say which plate is not there rather than quoting a path.
        """
        try:
            root_is_there = self.root.is_dir()
        except OSError as error:
            return ImageSourceUnavailable(self.root, str(error))
        if not root_is_there:
            return ImageSourceUnavailable(self.root, "the folder is missing")
        if self.require_network_mount and not is_network_mount(self.root, self.mount_table):
            return ImageSourceUnavailable(self.root, "the network share is not mounted")
        missing = PlateImagesNotFoundError(f"plate directory does not exist: {path}")
        missing.plate_code = plate_code
        return missing

    @staticmethod
    def shard_for(plate_code: str | int) -> str:
        code = str(plate_code).strip()
        if not code.isdigit():
            raise InvalidPlateCodeError(f"invalid plate code: {plate_code!r}")

        plate_number = int(code)
        if 999 < plate_number < 1100:
            return code[2:]
        if 1100 <= plate_number < 2000:
            return code[1:]
        if 2000 <= plate_number < 2100:
            return code[2:]
        return code

    def plate_directory(self, plate_code: str | int) -> Path:
        code = str(plate_code).strip()
        shard = self.shard_for(code)
        shard_dir = self.root / shard
        underscored = shard_dir / f"plateID_{code}"
        compact = shard_dir / f"plateID{code}"
        if underscored.is_dir() or not compact.is_dir():
            return underscored
        return compact

    def load_plate(self, plate_code: str | int, profile: str = "profileID_1") -> PlateImages:
        code = str(plate_code).strip()
        plate_dir = self.plate_directory(code)
        candidates: list[tuple[int, tuple[CrystalImage, ...]]] = []

        if not plate_dir.is_dir():
            raise self._explain_missing(plate_dir, code)

        for batch_dir in self._entries(plate_dir):
            match = self._BATCH_PATTERN.fullmatch(batch_dir.name)
            if not match or not batch_dir.is_dir():
                continue
            batch_id = int(match.group(1))
            images = self._images_in_batch(code, batch_id, batch_dir, profile)
            if images:
                candidates.append((batch_id, images))

        if not candidates:
            raise PlateImagesNotFoundError(
                f"no original images for plate {code} and profile {profile}"
            )

        batch_id, images = max(candidates, key=lambda item: item[0])
        return PlateImages(code, batch_id, profile, images)

    def available_batches(self, plate_code: str | int) -> tuple[int, ...]:
        code = str(plate_code).strip()
        plate_dir = self.plate_directory(code)
        if not plate_dir.is_dir():
            raise self._explain_missing(plate_dir, code)
        return tuple(
            sorted(
                int(match.group(1))
                for batch_dir in self._entries(plate_dir)
                if batch_dir.is_dir()
                and (match := self._BATCH_PATTERN.fullmatch(batch_dir.name))
            )
        )

    def load_plate_batch(
        self,
        plate_code: str | int,
        batch_id: int,
        profile: str = "profileID_1",
    ) -> PlateImages:
        code = str(plate_code).strip()
        plate_dir = self.plate_directory(code)
        if batch_id < 0:
            raise ValueError("batch_id must be non-negative")
        batch_dir = plate_dir / f"batchID_{batch_id}"
        if not batch_dir.is_dir():
            raise self._explain_missing(batch_dir)
        images = self._images_in_batch(code, batch_id, batch_dir, profile)
        if not images:
            raise PlateImagesNotFoundError(
                f"no original images for plate {code}, batch {batch_id}, profile {profile}"
            )
        return PlateImages(code, batch_id, profile, images)

    def available_profiles(
        self, plate_code: str | int, batch_id: int
    ) -> tuple[str, ...]:
        code = str(plate_code).strip()
        batch_dir = self.plate_directory(code) / f"batchID_{batch_id}"
        if not batch_dir.is_dir():
            raise self._explain_missing(batch_dir)
        profiles = {
            profile_dir.name
            for well_dir in self._entries(batch_dir)
            if well_dir.is_dir() and self._WELL_PATTERN.fullmatch(well_dir.name)
            for profile_dir in self._entries(well_dir)
            if profile_dir.is_dir() and profile_dir.name.startswith("profileID_")
        }
        return tuple(sorted(profiles))

    def _images_in_batch(
        self,
        plate_code: str,
        batch_id: int,
        batch_dir: Path,
        profile: str,
    ) -> tuple[CrystalImage, ...]:
        selected: dict[tuple[int, int], tuple[int, Path]] = {}

        for well_dir in self._entries(batch_dir):
            well_match = self._WELL_PATTERN.fullmatch(well_dir.name)
            if not well_match or not well_dir.is_dir():
                continue
            well_number = int(well_match.group(1))
            profile_dir = well_dir / profile
            if not profile_dir.is_dir():
                continue

            for image_path in self._entries(profile_dir):
                image_match = self._IMAGE_PATTERN.fullmatch(image_path.name)
                if not image_match or not image_path.is_file():
                    continue
                drop_number = int(image_match.group(1))
                revision_match = self._REVISION_PATTERN.search(image_path.name)
                revision = int(revision_match.group(1)) if revision_match else -1
                key = (well_number, drop_number)
                current = selected.get(key)
                if current is None or (revision, image_path.name) > (current[0], current[1].name):
                    selected[key] = (revision, image_path)

        return tuple(
            CrystalImage(
                plate_code=plate_code,
                batch_id=batch_id,
                well_number=well_number,
                drop_number=drop_number,
                profile=profile,
                path=path,
            )
            for (well_number, drop_number), (_, path) in sorted(selected.items())
        )
