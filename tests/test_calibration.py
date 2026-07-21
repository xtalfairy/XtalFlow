from pathlib import Path
import sqlite3

import pytest

from xtalflow.application import WellCalibrationService, circle_from_three_points
from xtalflow.domain import CalibrationMethod, CrystalImage, ImageCalibration, Project
from xtalflow.infrastructure import (
    OpenCVWellDetector,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.settings import DEFAULT_SETTINGS


FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root


def test_three_point_circle_and_physical_coordinate_conversion() -> None:
    center_x, center_y, radius = circle_from_three_points(
        ((10, 0), (0, 10), (-10, 0))
    )
    calibration = ImageCalibration.automatic(
        "image", center_x, center_y, radius, 0.9, 2.77
    )

    assert center_x == pytest.approx(0)
    assert center_y == pytest.approx(0)
    assert radius == pytest.approx(10)
    assert calibration.pixel_to_mm(0, 0) == pytest.approx((0, 0))
    assert calibration.pixel_to_mm(10, 0) == pytest.approx((1.385, 0))
    assert calibration.pixel_to_mm(0, 10) == pytest.approx((0, 1.385))


def test_collinear_manual_points_are_rejected() -> None:
    with pytest.raises(ValueError):
        circle_from_three_points(((0, 0), (1, 1), (2, 2)))


def test_calibration_is_persisted_per_project_image_set(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Calibration")
    image_set = project.add_image_set("1070", 5947, "profileID_1", "image")
    store.save_project(project)
    scoped = store.scoped_to(image_set.id)
    image = CrystalImage("1070", 5947, 1, 1, "profileID_1", Path("image.jpg"))

    service = WellCalibrationService(OpenCVWellDetector(), 2.77, scoped)
    saved = service.save_manual_three_point(
        image, ((110, 100), (100, 110), (90, 100))
    )
    restored = scoped.load_calibration(image.image_key)

    assert restored == saved
    assert restored.method is CalibrationMethod.MANUAL_THREE_POINT
    assert restored.confirmed
    store.close()


def test_automatic_calibration_can_be_explicitly_confirmed(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Confirmation")
    image_set = project.add_image_set("1070", 5947, "profileID_1", "image")
    store.save_project(project)
    scoped = store.scoped_to(image_set.id)
    service = WellCalibrationService(OpenCVWellDetector(), 2.77, scoped)
    automatic = ImageCalibration.automatic("image", 100, 100, 50, 0.9, 2.77)
    service.save(automatic)

    confirmed = service.confirm(automatic)

    assert confirmed.confirmed
    assert confirmed.method is CalibrationMethod.AUTO_CIRCLE
    assert scoped.load_calibration("image") == confirmed
    store.close()


def test_schema_v7_corrects_previous_3_8_mm_calibration(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    project = Project.create("Old diameter")
    image_set = project.add_image_set("1070", 5947, "profileID_1", "image")
    store.save_project(project)
    old = ImageCalibration.automatic("image", 100, 100, 50, 0.9, 3.8)
    store.scoped_to(image_set.id).save_calibration(old)
    store.close()
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA user_version = 6")
    connection.commit()
    connection.close()

    migrated = SQLiteReviewStore(database_path)
    calibration = migrated.scoped_to(image_set.id).load_calibration("image")

    assert calibration.physical_diameter_mm == 2.77
    migrated.close()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_detector_finds_outer_well_on_real_fixture() -> None:
    image = RockMakerImageRepository(FIXTURE_ROOT).load_plate("2070").images[0]

    boundary = OpenCVWellDetector().detect(image)

    assert boundary.confidence > 0.8
    assert 600 < boundary.center_x_px < 680
    assert 480 < boundary.center_y_px < 550
    assert 460 < boundary.radius_x_px < 510
