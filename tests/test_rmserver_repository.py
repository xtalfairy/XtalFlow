from pathlib import Path

import pytest

from xtalflow.infrastructure import (
    InvalidPlateCodeError,
    PlateImagesNotFoundError,
    RockMakerImageRepository,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "rmserver"
requires_real_fixture = pytest.mark.skipif(
    not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available"
)


@pytest.mark.parametrize(
    ("plate_code", "shard"),
    [("1069", "69"), ("1070", "70"), ("1100", "100"), ("2001", "01"), ("2100", "2100")],
)
def test_legacy_shard_rules(plate_code: str, shard: str) -> None:
    assert RockMakerImageRepository.shard_for(plate_code) == shard


def test_invalid_plate_code_is_rejected() -> None:
    with pytest.raises(InvalidPlateCodeError):
        RockMakerImageRepository.shard_for("plate-1070")


@pytest.mark.requires_rmserver_fixture
@requires_real_fixture
def test_real_fixture_uses_latest_valid_batch_and_original_images_only() -> None:
    plate = RockMakerImageRepository(FIXTURE_ROOT).load_plate("2070")

    assert plate.batch_id == 14122
    assert plate.well_numbers == tuple(range(1, 97))
    assert len(plate.images) == 192
    assert {image.drop_number for image in plate.images} == {1, 2}
    assert all(image.path.name.endswith("_ef.jpg") for image in plate.images)
    assert not any("_th" in image.path.name for image in plate.images)


@pytest.mark.requires_rmserver_fixture
@requires_real_fixture
@pytest.mark.parametrize(
    ("plate_code", "expected_batch", "expected_drops"),
    [("2069", 14121, {1, 2}), ("1100", 6088, {1, 3})],
)
def test_real_fixture_supports_other_plate_layouts(
    plate_code: str, expected_batch: int, expected_drops: set[int]
) -> None:
    plate = RockMakerImageRepository(FIXTURE_ROOT).load_plate(plate_code)

    assert plate.batch_id == expected_batch
    assert len(plate.well_numbers) == 96
    assert len(plate.images) == 192
    assert {image.drop_number for image in plate.images} == expected_drops


def test_highest_image_revision_wins_within_drop(tmp_path: Path) -> None:
    profile = tmp_path / "70" / "plateID_1070" / "batchID_2" / "wellNum_1" / "profileID_1"
    profile.mkdir(parents=True)
    (profile / "d1_r10_ef.jpg").touch()
    (profile / "d1_r20_ef.jpg").touch()
    (profile / "d1_r999_th.jpg").touch()

    plate = RockMakerImageRepository(tmp_path).load_plate("1070")

    assert [image.path.name for image in plate.images] == ["d1_r20_ef.jpg"]


def test_highest_numeric_batch_with_images_wins(tmp_path: Path) -> None:
    for batch_id in (9, 10):
        profile = (
            tmp_path
            / "70"
            / "plateID_1070"
            / f"batchID_{batch_id}"
            / "wellNum_1"
            / "profileID_1"
        )
        profile.mkdir(parents=True)
        (profile / f"d1_r{batch_id}_ef.jpg").touch()

    plate = RockMakerImageRepository(tmp_path).load_plate("1070")

    assert plate.batch_id == 10
    assert plate.images[0].path.name == "d1_r10_ef.jpg"


def test_missing_plate_has_specific_error(tmp_path: Path) -> None:
    with pytest.raises(PlateImagesNotFoundError, match="plate directory"):
        RockMakerImageRepository(tmp_path).load_plate("1070")


def test_compact_plate_directory_observed_on_rmserver_is_supported(tmp_path: Path) -> None:
    profile = (
        tmp_path
        / "70"
        / "plateID1070"
        / "batchID_1"
        / "wellNum_1"
        / "profileID_1"
    )
    profile.mkdir(parents=True)
    (profile / "d1_r1_ef.jpg").touch()

    plate = RockMakerImageRepository(tmp_path).load_plate("1070")

    assert plate.plate_code == "1070"
    assert plate.images[0].path.parent == profile


def test_underscored_plate_directory_takes_precedence_when_both_exist(tmp_path: Path) -> None:
    for directory_name, batch_id in (("plateID1070", 1), ("plateID_1070", 2)):
        profile = (
            tmp_path
            / "70"
            / directory_name
            / f"batchID_{batch_id}"
            / "wellNum_1"
            / "profileID_1"
        )
        profile.mkdir(parents=True)
        (profile / "d1_r1_ef.jpg").touch()

    plate = RockMakerImageRepository(tmp_path).load_plate("1070")

    assert plate.batch_id == 2


def test_specific_batch_can_be_pinned_and_reloaded(tmp_path: Path) -> None:
    for batch_id in (1, 2):
        profile = (
            tmp_path
            / "70"
            / "plateID_1070"
            / f"batchID_{batch_id}"
            / "wellNum_1"
            / "profileID_1"
        )
        profile.mkdir(parents=True)
        (profile / f"d1_r{batch_id}_ef.jpg").touch()
    repository = RockMakerImageRepository(tmp_path)

    assert repository.available_batches("1070") == (1, 2)
    assert repository.available_profiles("1070", 1) == ("profileID_1",)
    assert repository.load_plate_batch("1070", 1).batch_id == 1
    assert repository.load_plate("1070").batch_id == 2
