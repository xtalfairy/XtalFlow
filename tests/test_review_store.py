from pathlib import Path
import sqlite3
from datetime import UTC, datetime

import pytest

from xtalflow.domain import (
    CrystalImage,
    Project,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
    TargetPoint,
    SWISSCI_MIDI_3_LENS,
)
from xtalflow.infrastructure.review_migrations import LATEST_SCHEMA_VERSION
from xtalflow.infrastructure import SQLiteReviewStore
from xtalflow.domain.plan_lifecycle import PlanningDraft, PlanRevision, WorksheetExportEvent


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


def test_fragment_library_import_is_content_deduplicated_and_reloadable(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "library.csv"
    csv_path.write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        "Vendor,Lib,8,CMP-8,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n",
        encoding="utf-8",
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")

    first = store.import_fragment_library(csv_path)
    second = store.import_fragment_library(csv_path)
    restored = store.load_fragment_library(first.id)

    assert first == second
    assert len(store.list_fragment_libraries()) == 1
    assert first.display_name == "library.csv · 1 rows"
    assert restored.fragments[0].number == "8"
    assert restored.fragments[0].compound_id == "CMP-8"
    store.close()


def test_planning_draft_revision_and_export_lifecycle(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(UTC)
    project = Project("project-1", "Test", now, now)
    store.save_project(project)
    draft = PlanningDraft(
        "plan-1", project.id, "fragment_screening", "Fragment Screening #1",
        "/libraries/main.csv", "1-8", "BRD4", "25.0", "selection", now, now,
    )
    store.save_planning_draft(draft)

    restored = store.load_planning_drafts(project.id)
    assert restored == (draft,)

    first = store.finalize_plan_revision(
        PlanRevision("revision-1", draft.id, 0, "FragSC-202607-BRD4-01", "{\"v\":1}", "jjh", now)
    )
    second = store.finalize_plan_revision(
        PlanRevision("revision-2", draft.id, 0, "FragSC-202607-BRD4-02", "{\"v\":2}", "jjh", now)
    )
    assert (first.revision, second.revision) == (1, 2)
    assert store.list_plan_revisions(draft.id) == (first, second)
    assert store.reserved_experiment_ids() == {
        "FragSC-202607-BRD4-01", "FragSC-202607-BRD4-02"
    }

    export = WorksheetExportEvent(
        "export-1", second.id, "jjh", now, "succeeded",
        "/echo/file.csv", "/shifter1/file.csv", "/shifter2/file.csv",
    )
    store.record_worksheet_export(export)
    assert store.list_worksheet_exports(second.id) == (export,)
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


def test_same_physical_image_set_is_isolated_between_projects(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    first_project = Project.create("First")
    second_project = Project.create("Second")
    first_set = first_project.add_image_set("1070", 5947, "profileID_1", "image")
    second_set = second_project.add_image_set("1070", 5947, "profileID_1", "image")
    store.save_project(first_project)
    store.save_project(second_project)
    target = TargetPoint("target-1", "image", 10, 20)
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "image")
    preferences = ReviewPreferences(3)

    store.scoped_to(first_set.id).save_checkpoint(
        "image", (target,), progress, preferences
    )

    assert store.scoped_to(first_set.id).load_images(("image",)) == (target,)
    assert store.scoped_to(second_set.id).load_images(("image",)) == ()
    store.close()


def test_reviewed_images_are_scoped_and_restored(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Review status")
    image_set = project.add_image_set("1070", 5947, "profileID_1", "first")
    store.save_project(project)
    scoped = store.scoped_to(image_set.id)
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "second")

    scoped.save_checkpoint("first", (), progress, ReviewPreferences(1), True)

    assert scoped.load_reviewed_images(("first", "second")) == ("first",)
    store.close()


def test_standalone_targets_are_imported_into_a_project(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE target_point(target_id TEXT PRIMARY KEY, image_key TEXT, x_px REAL, y_px REAL)"
    )
    connection.execute(
        "INSERT INTO target_point VALUES ('t1', '1070:5947:1:1:profileID_1', 10, 20)"
    )
    connection.commit()
    connection.close()

    store = SQLiteReviewStore(database_path)
    imported = next(project for project in store.load_projects() if project.name.startswith("Imported"))
    image_set = imported.active_image_sets[0]

    assert image_set.source_key == ("1070", 5947, "profileID_1")
    assert image_set.plate_format_id == SWISSCI_MIDI_3_LENS.id
    assert len(
        store.scoped_to(image_set.id).load_images(("1070:5947:1:1:profileID_1",))
    ) == 1
    store.close()


def test_schema_v9_assigns_all_existing_image_sets_to_three_lens(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE project (
            project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            active_image_set_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE project_image_set (
            image_set_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            plate_code TEXT NOT NULL, batch_id INTEGER NOT NULL, profile TEXT NOT NULL,
            display_order INTEGER NOT NULL, active_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL, archived_at TEXT,
            plate_format_id TEXT, plate_format_version INTEGER
        );
        INSERT INTO project VALUES (
            'p1', 'Legacy', 's1', '2026-01-01T00:00:00+00:00',
            '2026-01-01T00:00:00+00:00'
        );
        INSERT INTO project_image_set VALUES (
            's1', 'p1', '1070', 5947, 'profileID_1', 0, 'image',
            '2026-01-01T00:00:00+00:00', NULL, NULL, NULL
        );
        PRAGMA user_version = 8;
        """
    )
    connection.close()

    store = SQLiteReviewStore(database_path)
    image_set = store.load_projects()[0].active_image_sets[0]

    assert image_set.plate_format_id == SWISSCI_MIDI_3_LENS.id
    assert image_set.plate_format_version == SWISSCI_MIDI_3_LENS.version
    store.close()
