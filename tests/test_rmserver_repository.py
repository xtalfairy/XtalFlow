from pathlib import Path

import pytest

from xtalflow.infrastructure import (
    ImageSourceUnavailable,
    InvalidPlateCodeError,
    PlateImagesNotFoundError,
    RockMakerImageRepository,
)
from xtalflow.settings import DEFAULT_SETTINGS


FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root
requires_real_fixture = pytest.mark.skipif(
    not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available"
)


@pytest.mark.parametrize(
    ("plate_code", "shard"),
    [
        # Taken from the folders on the BL-5C share, not from the old rule:
        # every plate is filed under its code's last three digits, written
        # without leading zeros.
        ("1069", "69"), ("1070", "70"), ("1100", "100"),
        ("2164", "164"), ("1164", "164"),
        ("2001", "1"), ("1002", "2"), ("2002", "2"), ("2000", "0"),
        ("2099", "99"), ("999", "999"), ("2181", "181"),
    ],
)
def test_plates_are_filed_under_the_last_three_digits(
    plate_code: str, shard: str
) -> None:
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


def test_latest_image_source_skips_scheduled_batches_without_images() -> None:
    from xtalflow.infrastructure import PlateImagesNotFoundError, latest_image_source

    class Repository:
        def available_batches(self, plate_code):
            return (7, 12, 15)

        def available_profiles(self, plate_code, batch_id):
            return {7: ("profileID_1",), 12: ("profileID_2", "profileID_10")}.get(
                batch_id, ()
            )

    class EmptyRepository(Repository):
        def available_profiles(self, plate_code, batch_id):
            return ()

    assert latest_image_source(Repository(), "1070") == (12, "profileID_10")
    with pytest.raises(PlateImagesNotFoundError, match="no imaged batches"):
        latest_image_source(EmptyRepository(), "1070")


def _mount_table(path: Path, filesystem: str) -> str:
    return f"device {path} {filesystem}"


def test_a_folder_in_place_of_the_share_is_not_a_missing_plate(tmp_path: Path) -> None:
    repository = RockMakerImageRepository(
        tmp_path, require_network_mount=True, mount_table=_mount_table(tmp_path, "apfs")
    )

    with pytest.raises(ImageSourceUnavailable) as raised:
        repository.available_batches("2071")

    assert raised.value.reason == "the network share is not mounted"
    assert str(tmp_path) in str(raised.value)
    # Every caller that already catches OSError keeps catching this.
    assert isinstance(raised.value, OSError)


def test_a_plate_missing_from_a_mounted_share_says_so(tmp_path: Path) -> None:
    repository = RockMakerImageRepository(
        tmp_path, require_network_mount=True, mount_table=_mount_table(tmp_path, "smbfs")
    )

    with pytest.raises(PlateImagesNotFoundError, match="plate directory does not exist"):
        repository.available_batches("2071")


def test_a_local_image_root_reports_missing_plates_as_missing(tmp_path: Path) -> None:
    repository = RockMakerImageRepository(tmp_path)

    with pytest.raises(PlateImagesNotFoundError, match="plate directory does not exist"):
        repository.available_batches("2071")


def test_a_missing_image_root_is_the_share_not_the_plate(tmp_path: Path) -> None:
    repository = RockMakerImageRepository(tmp_path / "gone")

    with pytest.raises(ImageSourceUnavailable) as raised:
        repository.available_batches("2071")

    assert raised.value.reason == "the folder is missing"


@pytest.mark.requires_rmserver_fixture
@requires_real_fixture
def test_a_share_that_drops_mid_read_is_reported_as_the_share(monkeypatch) -> None:
    repository = RockMakerImageRepository(FIXTURE_ROOT)
    down = OSError(112, "Host is down")

    def drop(self):
        raise down

    monkeypatch.setattr(Path, "iterdir", drop)

    with pytest.raises(ImageSourceUnavailable) as raised:
        repository.available_batches("1070")

    assert "Host is down" in str(raised.value)
    assert raised.value.__cause__ is down
