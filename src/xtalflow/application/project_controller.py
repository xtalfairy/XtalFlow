from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from xtalflow.domain import (
    CrystalImage,
    CalibrationMethod,
    ImageCalibration,
    ImageFilter,
    PlateImages,
    PlateFormat,
    Project,
    ProjectImageSet,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
    TargetPoint,
    plate_format_by_id,
)
from xtalflow.domain.fragment_screening import CrystalTarget, SelectedCrystal

from .review_controller import ReviewController
from .review_port import ReviewPersistenceError


@dataclass(frozen=True, slots=True)
class ProjectReviewStatistics:
    total_images: int
    reviewed_images: int
    target_images: int
    target_points: int
    reviewed_without_targets: int
    unreviewed_images: int


class TargetValidationIssue(str, Enum):
    CALIBRATION_MISSING = "calibration_missing"
    CALIBRATION_UNCONFIRMED = "calibration_unconfirmed"
    OUTSIDE_WELL = "outside_well"


@dataclass(frozen=True, slots=True)
class ProjectTargetSummary:
    image_set_id: str
    image: CrystalImage
    target_number: int
    target: TargetPoint
    calibration: ImageCalibration | None

    @property
    def validation_issues(self) -> tuple[TargetValidationIssue, ...]:
        calibration = self.calibration
        if calibration is None:
            return (TargetValidationIssue.CALIBRATION_MISSING,)
        issues: list[TargetValidationIssue] = []
        if not calibration.confirmed:
            issues.append(TargetValidationIssue.CALIBRATION_UNCONFIRMED)
        normalized_distance = (
            ((self.target.x_px - calibration.center_x_px) / calibration.radius_x_px)
            ** 2
            + ((self.target.y_px - calibration.center_y_px) / calibration.radius_y_px)
            ** 2
        )
        if normalized_distance > 1:
            issues.append(TargetValidationIssue.OUTSIDE_WELL)
        return tuple(issues)

    @property
    def is_ready(self) -> bool:
        return not self.validation_issues


class ImageRepositoryPort(Protocol):
    def load_plate(self, plate_code: str, profile: str = "profileID_1") -> PlateImages: ...

    def load_plate_batch(
        self, plate_code: str, batch_id: int, profile: str = "profileID_1"
    ) -> PlateImages: ...


class ProjectController:
    """Manage project image-set membership and the active plate review."""

    def __init__(
        self,
        image_repository: ImageRepositoryPort,
        workspace_store=None,
        default_auto_advance_count: int = 1,
    ) -> None:
        self.image_repository = image_repository
        self.workspace_store = workspace_store
        self.projects: list[Project] = (
            list(workspace_store.load_projects()) if workspace_store is not None else []
        )
        self.last_open_project_id: str | None = (
            workspace_store.load_last_open_project()
            if workspace_store is not None
            else None
        )
        self.active_project: Project | None = None
        self.review_controller: ReviewController | None = None
        self.default_auto_advance_count = default_auto_advance_count

    def create_project(self, name: str) -> Project:
        self._checkpoint_active_review()
        project = Project.create(name)
        self.projects.append(project)
        self.active_project = project
        self.review_controller = None
        self._save_project(project)
        self._save_last_open_project(project.id)
        return project

    def open_project(self, project_id: str) -> Project:
        project = self._project(project_id)
        self._checkpoint_active_review()
        self.active_project = project
        self._save_last_open_project(project.id)
        self.review_controller = None
        if project.active_image_set_id is not None:
            self.activate_image_set(project.active_image_set_id)
        return project

    def add_latest_image_set(
        self, plate_code: str, plate_format: PlateFormat, profile: str = "profileID_1"
    ) -> ProjectImageSet:
        project = self._require_active_project()
        plate = self.image_repository.load_plate(plate_code, profile)
        return self._add_loaded_image_set(plate, plate_format)

    def add_pinned_image_set(
        self, plate_code: str, batch_id: int, profile: str, plate_format: PlateFormat
    ) -> ProjectImageSet:
        plate = self.image_repository.load_plate_batch(plate_code, batch_id, profile)
        return self._add_loaded_image_set(plate, plate_format)

    def _add_loaded_image_set(
        self, plate: PlateImages, plate_format: PlateFormat
    ) -> ProjectImageSet:
        project = self._require_active_project()
        plate = self._images_supported_by(plate, plate_format)
        existing = next(
            (
                item
                for item in project.active_image_sets
                if item.source_key == (plate.plate_code, plate.batch_id, plate.profile)
            ),
            None,
        )
        if existing is not None:
            if existing.plate_format_id != plate_format.id:
                raise ValueError(
                    "this image set already exists with a different plate format"
                )
            self.activate_image_set(existing.id)
            return existing
        image_set = project.add_image_set(
            plate.plate_code,
            plate.batch_id,
            plate.profile,
            plate.images[0].image_key,
            plate_format.id,
            plate_format.version,
        )
        self._save_project(project)
        self._activate_loaded_image_set(image_set, plate)
        return image_set

    @property
    def active_image_set(self) -> ProjectImageSet | None:
        project = self.active_project
        if project is None or project.active_image_set_id is None:
            return None
        return next(
            item for item in project.active_image_sets
            if item.id == project.active_image_set_id
        )

    def set_image_set_plate_format(
        self, image_set_id: str, plate_format: PlateFormat
    ) -> ProjectImageSet:
        project = self._require_active_project()
        image_set = next(
            (item for item in project.image_sets if item.id == image_set_id), None
        )
        if image_set is None:
            raise ValueError("image set is not in the active project")
        image_set.plate_format_id = plate_format.id
        image_set.plate_format_version = plate_format.version
        self._save_project(project)
        if project.active_image_set_id == image_set.id:
            self._checkpoint_active_review()
            plate = self._load_image_set_plate(image_set)
            self._activate_loaded_image_set(image_set, plate)
        return image_set

    def rename_active_project(self, name: str) -> None:
        project = self._require_active_project()
        project.rename(name)
        self._save_project(project)

    def activate_image_set(self, image_set_id: str) -> ProjectImageSet:
        project = self._require_active_project()
        image_set = next(
            (item for item in project.active_image_sets if item.id == image_set_id), None
        )
        if image_set is None:
            raise ValueError("image set is not in the active project")
        if self.review_controller is not None:
            current = self.review_controller.plate
            if (
                current.plate_code,
                current.batch_id,
                current.profile,
            ) == image_set.source_key:
                return image_set
        self._checkpoint_active_review()
        plate = self._load_image_set_plate(image_set)
        self._activate_loaded_image_set(image_set, plate)
        return image_set

    def move_across_image_sets(
        self, direction: int, image_filter: ImageFilter
    ) -> bool:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        project = self._require_active_project()
        controller = self.review_controller
        if controller is None or project.active_image_set_id is None:
            return False
        image_sets = project.active_image_sets
        current_set_index = next(
            index
            for index, image_set in enumerate(image_sets)
            if image_set.id == project.active_image_set_id
        )
        remaining = (
            image_sets[current_set_index + 1 :]
            if direction > 0
            else tuple(reversed(image_sets[:current_set_index]))
        )
        for image_set in remaining:
            plate = self._load_image_set_plate(image_set)
            candidate = self._filtered_destination(image_set, plate, image_filter, direction)
            if candidate is None:
                continue
            self._checkpoint_active_review()
            self._activate_loaded_image_set(
                image_set, plate, initial_image_key=plate.images[candidate].image_key
            )
            self.review_controller.image_filter = image_filter
            return True
        return False

    def has_adjacent_image_set(self, direction: int) -> bool:
        project = self.active_project
        if project is None or project.active_image_set_id is None:
            return False
        ids = [image_set.id for image_set in project.active_image_sets]
        current = ids.index(project.active_image_set_id)
        return current > 0 if direction < 0 else current < len(ids) - 1

    def project_review_statistics(self) -> ProjectReviewStatistics:
        project = self._require_active_project()
        total = reviewed = target_images = target_points = reviewed_without = 0
        for image_set in project.active_image_sets:
            plate = self._load_image_set_plate(image_set)
            total += len(plate.images)
            if (
                project.active_image_set_id == image_set.id
                and self.review_controller is not None
            ):
                session = self.review_controller.session
            else:
                session, _, _, _ = self._load_review_material(image_set, plate)
            reviewed += session.reviewed_count
            for image in plate.images:
                count = session.target_count_for(image)
                if count:
                    target_images += 1
                    target_points += count
                elif session.is_reviewed(image):
                    reviewed_without += 1
        return ProjectReviewStatistics(
            total,
            reviewed,
            target_images,
            target_points,
            reviewed_without,
            total - reviewed,
        )

    def project_target_summaries(self) -> tuple[ProjectTargetSummary, ...]:
        project = self._require_active_project()
        summaries: list[ProjectTargetSummary] = []
        for image_set in project.active_image_sets:
            plate = self._load_image_set_plate(image_set)
            if (
                project.active_image_set_id == image_set.id
                and self.review_controller is not None
            ):
                session = self.review_controller.session
                scoped_store = self.review_controller.store
            else:
                session, _, _, scoped_store = self._load_review_material(
                    image_set, plate
                )
            for image in plate.images:
                targets = session.targets_for(image)
                if not targets:
                    continue
                calibration = (
                    scoped_store.load_calibration(image.image_key)
                    if scoped_store is not None
                    else None
                )
                summaries.extend(
                    ProjectTargetSummary(
                        image_set.id, image, number, target, calibration
                    )
                    for number, target in enumerate(targets, start=1)
                )
        return tuple(
            sorted(summaries, key=lambda summary: summary.target.selected_at)
        )

    def selected_crystals_for_plan(self) -> tuple[SelectedCrystal, ...]:
        """Return calibrated target images in first-selection order."""
        project = self._require_active_project()
        image_sets = {item.id: item for item in project.active_image_sets}
        grouped: dict[str, list[ProjectTargetSummary]] = {}
        for summary in self.project_target_summaries():
            if not summary.is_ready:
                raise ValueError(
                    "all targets need confirmed, valid well calibration before planning"
                )
            grouped.setdefault(summary.image.image_key, []).append(summary)

        crystals: list[SelectedCrystal] = []
        for summaries in grouped.values():
            first = summaries[0]
            image_set = image_sets[first.image_set_id]
            plate_format = plate_format_by_id(image_set.plate_format_id)
            if plate_format is None:
                raise ValueError("all target image sets need a supported plate format")
            address = str(
                plate_format.address_for(
                    first.image.well_number, first.image.drop_number
                )
            )
            targets = []
            for summary in summaries:
                calibration = summary.calibration
                if calibration is None:  # guarded by is_ready; keeps typing explicit
                    raise ValueError("target calibration is missing")
                x_mm, y_mm = calibration.pixel_to_mm(
                    summary.target.x_px, summary.target.y_px
                )
                targets.append(
                    CrystalTarget(
                        summary.target.id,
                        Decimal(str(x_mm)),
                        Decimal(str(y_mm)),
                        summary.target.selected_at,
                    )
                )
            crystals.append(
                SelectedCrystal(
                    first.image.image_key,
                    first.image.plate_code,
                    address,
                    tuple(targets),
                    plate_format.id,
                )
            )
        return tuple(crystals)

    def valid_unconfirmed_automatic_calibration_count(self) -> int:
        return len(self._valid_unconfirmed_automatic_calibrations())

    def confirm_valid_automatic_calibrations(self) -> int:
        """Confirm only automatic wells whose selected targets are all in bounds."""
        candidates = self._valid_unconfirmed_automatic_calibrations()
        if not candidates:
            return 0
        if self.workspace_store is None:
            raise ReviewPersistenceError(
                "bulk calibration confirmation requires a persistent project database"
            )
        for image_set_id, calibration in candidates:
            confirmed = replace(
                calibration, confirmed=True, updated_at=datetime.now(UTC)
            )
            self.workspace_store.scoped_to(image_set_id).save_calibration(confirmed)
        return len(candidates)

    def _valid_unconfirmed_automatic_calibrations(
        self,
    ) -> tuple[tuple[str, ImageCalibration], ...]:
        grouped: dict[tuple[str, str], list[ProjectTargetSummary]] = {}
        for summary in self.project_target_summaries():
            grouped.setdefault(
                (summary.image_set_id, summary.image.image_key), []
            ).append(summary)
        candidates: list[tuple[str, ImageCalibration]] = []
        for (image_set_id, _), summaries in grouped.items():
            calibration = summaries[0].calibration
            if (
                calibration is not None
                and calibration.method is CalibrationMethod.AUTO_CIRCLE
                and not calibration.confirmed
                and all(
                    summary.validation_issues
                    == (TargetValidationIssue.CALIBRATION_UNCONFIRMED,)
                    for summary in summaries
                )
            ):
                candidates.append((image_set_id, calibration))
        return tuple(candidates)

    def remove_project_targets(self, target_ids: tuple[str, ...]) -> int:
        unique_ids = tuple(dict.fromkeys(target_ids))
        if not unique_ids:
            return 0
        summaries = self.project_target_summaries()
        existing_ids = {summary.target.id for summary in summaries}
        unknown = set(unique_ids) - existing_ids
        if unknown:
            raise ValueError("one or more selected targets no longer exist")
        if self.workspace_store is not None:
            self.workspace_store.delete_targets(unique_ids)
        if self.review_controller is not None:
            for target_id in unique_ids:
                self.review_controller.session.remove_target(target_id)
        return len(unique_ids)

    def _load_image_set_plate(self, image_set: ProjectImageSet) -> PlateImages:
        plate_format = plate_format_by_id(image_set.plate_format_id)
        if plate_format is None or plate_format.version != image_set.plate_format_version:
            raise ValueError("image set uses an unsupported plate format")
        plate = self.image_repository.load_plate_batch(
            image_set.plate_code, image_set.batch_id, image_set.profile
        )
        return self._images_supported_by(plate, plate_format)

    @staticmethod
    def _images_supported_by(
        plate: PlateImages, plate_format: PlateFormat
    ) -> PlateImages:
        supported_drops = {lens.drop_number for lens in plate_format.lenses}
        images = tuple(
            image for image in plate.images if image.drop_number in supported_drops
        )
        if not images:
            raise ValueError(
                f"no images match the selected format: {plate_format.display_name}"
            )
        return PlateImages(plate.plate_code, plate.batch_id, plate.profile, images)

    def move_image_set(self, image_set_id: str, offset: int) -> None:
        project = self._require_active_project()
        project.move_image_set(image_set_id, offset)
        self._save_project(project)

    def archive_image_set(self, image_set_id: str) -> None:
        project = self._require_active_project()
        was_active = project.active_image_set_id == image_set_id
        if was_active:
            self._checkpoint_active_review()
        project.archive_image_set(image_set_id)
        self._save_project(project)
        if was_active:
            self.review_controller = None
            if project.active_image_set_id is not None:
                self.activate_image_set(project.active_image_set_id)

    def restore_image_set(self, image_set_id: str) -> ProjectImageSet:
        project = self._require_active_project()
        image_set = project.restore_image_set(image_set_id)
        self._save_project(project)
        self.activate_image_set(image_set.id)
        return image_set

    def _activate_loaded_image_set(
        self,
        image_set: ProjectImageSet,
        plate: PlateImages,
        initial_image_key: str | None = None,
    ) -> None:
        project = self._require_active_project()
        previous_active_id = project.active_image_set_id
        previous_image_key = image_set.active_image_key
        session, progress, preferences, scoped_store = self._load_review_material(
            image_set, plate
        )
        if initial_image_key is not None:
            progress.move_to(initial_image_key)
        controller = ReviewController(
            plate, progress, preferences, session, scoped_store
        )
        try:
            controller.persist_state()
            project.activate(image_set.id)
            image_set.active_image_key = controller.current_image.image_key
            self._save_project(project)
        except (ReviewPersistenceError, ValueError):
            project.active_image_set_id = previous_active_id
            image_set.active_image_key = previous_image_key
            raise
        self.review_controller = controller

    def _load_review_material(self, image_set: ProjectImageSet, plate: PlateImages):
        scoped_store = (
            self.workspace_store.scoped_to(image_set.id)
            if self.workspace_store is not None
            else None
        )
        session = ReviewSession()
        plan_key = f"{plate.plate_code}:{plate.batch_id}:{plate.profile}"
        state = None
        if scoped_store is not None:
            image_keys = tuple(image.image_key for image in plate.images)
            session.restore_targets(scoped_store.load_images(image_keys))
            session.restore_reviewed(scoped_store.load_reviewed_images(image_keys))
            state = scoped_store.load_review_state(plan_key)
        if state is None:
            progress = ReviewProgress.create(
                plate.plate_code,
                plate.batch_id,
                plate.profile,
                image_set.active_image_key,
            )
            preferences = ReviewPreferences(self.default_auto_advance_count)
        else:
            progress, preferences = state
        return session, progress, preferences, scoped_store

    def _filtered_destination(
        self,
        image_set: ProjectImageSet,
        plate: PlateImages,
        image_filter: ImageFilter,
        direction: int,
    ) -> int | None:
        session, progress, preferences, _ = self._load_review_material(image_set, plate)
        controller = ReviewController(plate, progress, preferences, session)
        controller.image_filter = image_filter
        candidates = controller.filtered_indices
        if not candidates:
            return None
        return candidates[0] if direction > 0 else candidates[-1]

    def _checkpoint_active_review(self) -> None:
        if self.review_controller is not None:
            self.review_controller.checkpoint_current(mark_reviewed=True)

    def _project(self, project_id: str) -> Project:
        project = next((item for item in self.projects if item.id == project_id), None)
        if project is None:
            raise ValueError("project does not exist")
        return project

    def _require_active_project(self) -> Project:
        if self.active_project is None:
            raise ValueError("no project is open")
        return self.active_project

    def _save_project(self, project: Project) -> None:
        if self.workspace_store is not None:
            self.workspace_store.save_project(project)

    def _save_last_open_project(self, project_id: str) -> None:
        self.last_open_project_id = project_id
        if self.workspace_store is not None:
            self.workspace_store.save_last_open_project(project_id)
