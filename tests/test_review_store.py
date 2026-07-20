from pathlib import Path
import sqlite3

import pytest

from xtalflow.domain import (
    CrystalImage,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
)
from xtalflow.infrastructure.review_migrations import LATEST_SCHEMA_VERSION
from xtalflow.infrastructure import SQLiteReviewStore


def test_sqlite_store_replaces_and_restores_image_snapshot(tmp_path: Path) -> None:
    image = CrystalImage("1070", 5947, 1, 1, "profileID_1", Path("image.jpg"))
    session = ReviewSession()
    first = session.add_target(image, 10, 20, 100, 100)
    second = session.add_target(image, 30, 40, 100, 100)
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")

    store.save_image(image.image_key, (first, second))
    assert store.load_images((image.image_key,)) == (first, second)

    store.save_image(image.image_key, (second,))
    assert store.load_images((image.image_key,)) == (second,)
    store.close()


def test_sqlite_store_restores_review_state(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "first")
    preferences = ReviewPreferences(3)
    progress.move_to("second")

    store.save_review_state(progress, preferences)
    restored = store.load_review_state(progress.plan_key)

    assert restored is not None
    restored_progress, restored_preferences = restored
    assert restored_preferences.auto_advance_target_count == 3
    assert restored_progress.current_image_key == "second"
    store.close()


def test_old_required_count_column_is_migrated_to_auto_advance(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE review_plan (
            plan_key TEXT PRIMARY KEY, plate_code TEXT NOT NULL,
            batch_id INTEGER NOT NULL, profile TEXT NOT NULL,
            required_target_count INTEGER NOT NULL, current_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO review_plan VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("1070:5947:profileID_1", "1070", 5947, "profileID_1", 10, "image", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    store = SQLiteReviewStore(database_path)
    restored = store.load_review_state("1070:5947:profileID_1")

    assert restored[1].auto_advance_target_count == 10
    version = store._connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == LATEST_SCHEMA_VERSION
    store.close()


def test_checkpoint_rolls_back_targets_when_progress_write_fails(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    image = CrystalImage("1070", 5947, 1, 1, "profileID_1", Path("image.jpg"))
    session = ReviewSession()
    original = session.add_target(image, 10, 10, 100, 100)
    replacement = session.add_target(image, 20, 20, 100, 100)
    progress = ReviewProgress.create(
        "1070", 5947, "profileID_1", image.image_key
    )
    preferences = ReviewPreferences(3)
    store = SQLiteReviewStore(database_path)
    store.save_checkpoint(image.image_key, (original,), progress, preferences)
    store._connection.execute(
        """
        CREATE TRIGGER reject_review_update BEFORE UPDATE ON review_plan
        BEGIN SELECT RAISE(ABORT, 'test failure'); END
        """
    )

    from xtalflow.application import ReviewPersistenceError

    with pytest.raises(ReviewPersistenceError):
        store.save_checkpoint(
            image.image_key, (replacement,), progress, preferences
        )

    assert store.load_images((image.image_key,)) == (original,)
    store.close()
