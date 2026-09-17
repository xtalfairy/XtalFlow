from pathlib import Path
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from xtalflow.domain import (
    CrystalImage,
    ExperimentPlan,
    ExperimentProject,
    PlanType,
    Project,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
    TargetPoint,
    SWISSCI_MIDI_3_LENS,
    crystal_selection_from_selected_crystals,
)
from xtalflow.domain.crystal_workflow import CrystalTarget, SelectedCrystal
from xtalflow.infrastructure.review_migrations import LATEST_SCHEMA_VERSION
from xtalflow.infrastructure import SQLiteReviewStore
from xtalflow.domain.plan_lifecycle import (
    PlanningDraft, PlanRevision, WebDBUploadEvent, WorksheetExportEvent,
)


def test_sqlite_store_replaces_and_restores_image_snapshot(tmp_path: Path) -> None:
    image = CrystalImage("1070", 5947, 1, 1, "profileID_1", Path("image.jpg"))
    session = ReviewSession()
    first = session.add_target(image, 10, 20, 100, 100)
    second = session.add_target(image, 30, 40, 100, 100)
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")

    store.workspace.save_image(image.image_key, (first, second))
    assert store.workspace.load_images((image.image_key,)) == (first, second)

    store.workspace.save_image(image.image_key, (second,))
    assert store.workspace.load_images((image.image_key,)) == (second,)
    store.close()


def test_sqlite_store_restores_review_state(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "first")
    preferences = ReviewPreferences(3)
    progress.move_to("second")

    store.workspace.save_review_state(progress, preferences)
    restored = store.workspace.load_review_state(progress.plan_key)

    assert restored is not None
    restored_progress, restored_preferences = restored
    assert restored_preferences.auto_advance_target_count == 3
    assert restored_progress.current_image_key == "second"
    store.close()


def test_experiment_project_selected_wells_round_trip(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(timezone.utc)
    source = SelectedCrystal(
        "image-key", "2069", "A04a",
        (
            CrystalTarget("target-1", Decimal("0.1"), Decimal("-0.2"), now),
            CrystalTarget("target-2", Decimal("0.3"), Decimal("0.4"), now),
        ),
        SWISSCI_MIDI_3_LENS.id,
        "/rmserver/image.jpg",
    )
    selection = crystal_selection_from_selected_crystals(
        "experiment-project", (source,), selection_id="selection"
    )
    plan = ExperimentPlan(
        "experiment-plan", "experiment-project",
        PlanType.FRAGMENT_SCREENING, now, now,
    )
    project = ExperimentProject(
        "experiment-project", "BRD4 screen", selection, plan, now, now
    )

    store.planning.save_experiment_project(project)
    restored = store.planning.load_experiment_projects()

    assert restored == (project,)
    assert len(restored[0].crystal_selection.wells[0].soaking_positions) == 2
    assert store.planning.prior_selected_well_usage(
        "experiment-project", ("image-key",)
    ) == {}
    later = now + timedelta(seconds=1)
    reused_selection = crystal_selection_from_selected_crystals(
        "second-project", (source,), selection_id="second-selection"
    )
    reused_project = ExperimentProject(
        "second-project",
        "BRD4 follow-up",
        reused_selection,
        ExperimentPlan(
            "second-plan", "second-project", PlanType.RAW_CRYSTAL, later, later
        ),
        later,
        later,
    )
    store.planning.save_experiment_project(reused_project)
    usage = store.planning.prior_selected_well_usage("second-project", ("image-key",))
    assert usage["image-key"][0].project_name == "BRD4 screen"
    assert usage["image-key"][0].status == "Draft"
    assert store._connection.execute("PRAGMA user_version").fetchone()[0] == (
        LATEST_SCHEMA_VERSION
    )
    store.close()


def test_finalized_legacy_plan_migrates_to_selected_well_project(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    now = datetime.now(timezone.utc)
    workspace = Project("workspace", "Workspace", now, now)
    store.workspace.save_project(workspace)
    draft = PlanningDraft(
        "legacy-plan", workspace.id, "raw_crystal", "Legacy Raw", None, "",
        "BRD4", "0", "selection", now, now, "RawCrystal-202607-BRD4-01",
    )
    store.planning.save_planning_draft(draft)
    snapshot = json.dumps({
        "schema": 1,
        "plan_type": "raw_crystal",
        "protein": "BRD4",
        "assignment_order": "selection",
        "selections": [
            {
                "image_key": "image-key",
                "image_path": "/rmserver/image.jpg",
                "plate": "2069",
                "well": "A04a",
                "plate_format_id": SWISSCI_MIDI_3_LENS.id,
                "target": {
                    "id": "target-1",
                    "x_mm": "0.1",
                    "y_mm": "-0.2",
                    "selected_at": now.isoformat(),
                },
            },
            {
                "image_key": "image-key",
                "image_path": "/rmserver/image.jpg",
                "plate": "2069",
                "well": "A04a",
                "plate_format_id": SWISSCI_MIDI_3_LENS.id,
                "target": {
                    "id": "target-2",
                    "x_mm": "0.3",
                    "y_mm": "0.4",
                    "selected_at": now.isoformat(),
                },
            },
        ],
    })
    store.planning.finalize_plan_revision(
        PlanRevision(
            "legacy-revision", draft.id, 0, draft.experiment_id,
            snapshot, "jjh", now,
        )
    )
    store.close()

    migrated = SQLiteReviewStore(database_path)
    project = migrated.planning.load_experiment_project(draft.id)

    assert migrated.planning_project_migration.migrated == 1
    assert project is not None
    assert len(project.crystal_selection.wells) == 1
    assert len(project.crystal_selection.wells[0].soaking_positions) == 2
    migrated.close()

    reopened = SQLiteReviewStore(database_path)
    assert reopened.planning_project_migration.migrated == 0
    assert reopened.planning_project_migration.skipped_existing == 1
    assert len(reopened.planning.load_experiment_projects()) == 1
    reopened.close()


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
    restored = store.workspace.load_review_state("1070:5947:profileID_1")

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

    first = store.fragment_libraries.import_fragment_library(csv_path)
    second = store.fragment_libraries.import_fragment_library(csv_path)
    restored = store.fragment_libraries.load_fragment_library(first.id)

    assert first == second
    assert len(store.fragment_libraries.list_fragment_libraries()) == 1
    assert first.display_name == "library.csv · 1 rows"
    assert restored.fragments[0].number == "8"
    assert restored.fragments[0].compound_id == "CMP-8"
    store.close()


def test_planning_draft_revision_and_export_lifecycle(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(timezone.utc)
    project = Project("project-1", "Test", now, now)
    store.workspace.save_project(project)
    draft = PlanningDraft(
        "plan-1", project.id, "fragment_screening", "Fragment Screening #1",
        "/libraries/main.csv", "1-8", "BRD4", "25.0", "selection", now, now,
        "FragSC-202607-BRD4-01",
    )
    store.planning.save_planning_draft(draft)

    restored = store.planning.load_planning_drafts(project.id)
    assert restored == (draft,)

    first = store.planning.finalize_plan_revision(
        PlanRevision("revision-1", draft.id, 0, "FragSC-202607-BRD4-01", "{\"v\":1}", "jjh", now)
    )
    second = store.planning.finalize_plan_revision(
        PlanRevision("revision-2", draft.id, 0, "FragSC-202607-BRD4-01", "{\"v\":2}", "jjh", now)
    )
    assert (first.revision, second.revision) == (1, 2)
    assert store.planning.list_plan_revisions(draft.id) == (first, second)
    assert store.planning.reserved_experiment_ids() == {"FragSC-202607-BRD4-01"}

    export = WorksheetExportEvent(
        "export-1", second.id, "jjh", now, "succeeded",
        "/echo/file.csv", "/shifter1/file.csv", "/shifter2/file.csv",
    )
    store.audit.record_worksheet_export(export)
    assert store.audit.list_worksheet_exports(second.id) == (export,)
    upload = WebDBUploadEvent(
        "upload-1", second.id, "fbdd", "fbdd",
        "https://mxlive.example/upload_labworks/BL-5C/", now,
        "succeeded", 2, '[{"expri_id":"FragSC-1"}]',
        '{"created":2}', None,
    )
    store.audit.record_webdb_upload(upload)
    assert store.audit.list_webdb_uploads(second.id) == (upload,)


def test_upload_outcome_is_updated_and_listed_for_every_revision_of_experiment(
    tmp_path: Path,
) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(timezone.utc)
    project = Project(str(uuid4()), "Uploads", now, now)
    store.workspace.save_project(project)
    draft = PlanningDraft(
        "plan", project.id, "raw_crystal", "Plan", None, "", "", "0",
        "selection", now, now,
    )
    store.planning.save_planning_draft(draft)
    first = store.planning.finalize_plan_revision(
        PlanRevision("revision-1", draft.id, 0, "RawCrystal-01", "{}", "jjh", now)
    )
    second = store.planning.finalize_plan_revision(
        PlanRevision("revision-2", draft.id, 0, "RawCrystal-01", "{\"v\":2}", "jjh", now)
    )
    pending = WebDBUploadEvent(
        "upload-1", first.id, "fbdd", "fbdd",
        "https://mxlive.example/upload_labworks/BL-5C/", now, "pending", 1, "[]",
    )
    store.audit.record_webdb_upload(pending)

    from dataclasses import replace

    unknown = replace(pending, status="unknown", error_message="ReadTimeout")
    store.audit.update_webdb_upload(unknown)

    assert store.audit.list_webdb_uploads_for_experiment("RawCrystal-01") == (unknown,)
    assert store.audit.list_webdb_uploads(second.id) == ()
    assert store.audit.list_webdb_uploads_for_experiment("RawCrystal-02") == ()
    store.close()


def test_only_planning_plan_with_upload_history_is_protected_from_deletion(
    tmp_path: Path,
) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(timezone.utc)
    project = Project(str(uuid4()), "Deletion", now, now)
    store.workspace.save_project(project)
    removable = PlanningDraft(
        "draft-only", project.id, "raw_crystal", "Draft only", None, "", "",
        "0", "selection", now, now,
    )
    finalized = PlanningDraft(
        "finalized", project.id, "raw_crystal", "Finalized", None, "", "",
        "0", "selection", now, now,
    )
    uploaded = PlanningDraft(
        "uploaded", project.id, "raw_crystal", "Uploaded", None, "", "",
        "0", "selection", now, now,
    )
    store.planning.save_planning_draft(removable)
    store.planning.save_planning_draft(finalized)
    store.planning.save_planning_draft(uploaded)
    store.planning.finalize_plan_revision(
        PlanRevision("revision", finalized.id, 0, "RawCrystal-01", "{}", "jjh", now)
    )
    uploaded_revision = store.planning.finalize_plan_revision(
        PlanRevision("uploaded-revision", uploaded.id, 0, "RawCrystal-02", "{}", "jjh", now)
    )
    store.audit.record_webdb_upload(WebDBUploadEvent(
        "uploaded-event", uploaded_revision.id, "jjh", "jjh",
        "https://mxlive.example/upload_labworks/BL-5C/", now, "succeeded",
        1, "[]", "{}", None,
    ))

    store.planning.delete_planning_draft(removable.id)
    store.planning.delete_planning_draft(finalized.id)
    assert store.planning.planning_plan_has_upload_history(uploaded.id)
    with pytest.raises(ValueError, match="upload history"):
        store.planning.delete_planning_draft(uploaded.id)

    assert [draft.id for draft in store.planning.load_planning_drafts(project.id)] == [
        uploaded.id
    ]
    store.close()


def test_existing_plan_inherits_latest_revision_experiment_id(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    store = SQLiteReviewStore(database_path)
    now = datetime.now(timezone.utc)
    project = Project("project-1", "Test", now, now)
    store.workspace.save_project(project)
    draft = PlanningDraft(
        "plan-1", project.id, "raw_crystal", "Raw #1", None, "", "TEST",
        "0", "selection", now, now,
    )
    store.planning.save_planning_draft(draft)
    store.planning.finalize_plan_revision(
        PlanRevision("r1", draft.id, 0, "RawCrystal-01", "{}", "jjh", now)
    )
    store.planning.finalize_plan_revision(
        PlanRevision("r2", draft.id, 0, "RawCrystal-02", "{\"v\":2}", "jjh", now)
    )
    store.close()

    reopened = SQLiteReviewStore(database_path)

    assert reopened.planning.load_planning_drafts(project.id)[0].experiment_id == "RawCrystal-02"
    reopened.close()


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
    store.workspace.save_checkpoint(image.image_key, (original,), progress, preferences)
    store._connection.execute(
        """
        CREATE TRIGGER reject_review_update BEFORE UPDATE ON review_plan
        BEGIN SELECT RAISE(ABORT, 'test failure'); END
        """
    )

    from xtalflow.application import ReviewPersistenceError

    with pytest.raises(ReviewPersistenceError):
        store.workspace.save_checkpoint(
            image.image_key, (replacement,), progress, preferences
        )

    assert store.workspace.load_images((image.image_key,)) == (original,)
    store.close()


def test_same_physical_image_set_is_isolated_between_projects(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    first_project = Project.create("First")
    second_project = Project.create("Second")
    first_set = first_project.add_image_set("1070", 5947, "profileID_1", "image")
    second_set = second_project.add_image_set("1070", 5947, "profileID_1", "image")
    store.workspace.save_project(first_project)
    store.workspace.save_project(second_project)
    target = TargetPoint("target-1", "image", 10, 20)
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "image")
    preferences = ReviewPreferences(3)

    store.workspace.scoped_to(first_set.id).save_checkpoint(
        "image", (target,), progress, preferences
    )

    assert store.workspace.scoped_to(first_set.id).load_images(("image",)) == (target,)
    assert store.workspace.scoped_to(second_set.id).load_images(("image",)) == ()
    store.close()


def test_reviewed_images_are_scoped_and_restored(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Review status")
    image_set = project.add_image_set("1070", 5947, "profileID_1", "first")
    store.workspace.save_project(project)
    scoped = store.workspace.scoped_to(image_set.id)
    progress = ReviewProgress.create("1070", 5947, "profileID_1", "second")

    scoped.save_checkpoint("first", (), progress, ReviewPreferences(1), True)

    assert scoped.load_reviewed_images(("first", "second")) == ("first",)
    store.close()


def _create_standalone_target_database(database_path: Path) -> str:
    image_key = "1070:5947:1:1:profileID_1"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE target_point(target_id TEXT PRIMARY KEY, image_key TEXT, x_px REAL, y_px REAL)"
    )
    connection.execute(
        "INSERT INTO target_point VALUES ('t1', ?, 10, 20)", (image_key,)
    )
    connection.commit()
    connection.close()
    return image_key


def test_deleted_imported_targets_stay_deleted_after_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    image_key = _create_standalone_target_database(database_path)
    image_set_id = "legacy:1070:5947:profileID_1"
    store = SQLiteReviewStore(database_path)
    scoped = store.workspace.scoped_to(image_set_id)
    assert len(scoped.load_images((image_key,))) == 1
    progress = ReviewProgress.create("1070", 5947, "profileID_1", image_key)

    scoped.save_checkpoint(image_key, (), progress, ReviewPreferences(1), True)
    store.close()

    reopened = SQLiteReviewStore(database_path)
    assert reopened.workspace.scoped_to(image_set_id).load_images((image_key,)) == ()
    reopened.close()


def test_upgrade_does_not_restore_targets_deleted_before_import_tracking(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    image_key = _create_standalone_target_database(database_path)
    image_set_id = "legacy:1070:5947:profileID_1"
    SQLiteReviewStore(database_path).close()
    connection = sqlite3.connect(database_path)
    connection.execute("DELETE FROM image_set_target_point")
    connection.execute("DROP TABLE legacy_target_import")
    connection.execute("PRAGMA user_version = 15")
    connection.commit()
    connection.close()

    store = SQLiteReviewStore(database_path)

    assert store.workspace.scoped_to(image_set_id).load_images((image_key,)) == ()
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
    imported = next(project for project in store.workspace.load_projects() if project.name.startswith("Imported"))
    image_set = imported.active_image_sets[0]

    assert image_set.source_key == ("1070", 5947, "profileID_1")
    assert image_set.plate_format_id == SWISSCI_MIDI_3_LENS.id
    assert len(
        store.workspace.scoped_to(image_set.id).load_images(("1070:5947:1:1:profileID_1",))
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
    image_set = store.workspace.load_projects()[0].active_image_sets[0]

    assert image_set.plate_format_id == SWISSCI_MIDI_3_LENS.id
    assert image_set.plate_format_version == SWISSCI_MIDI_3_LENS.version
    store.close()


def _legacy_required_count_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE review_plan (
            plan_key TEXT PRIMARY KEY, plate_code TEXT NOT NULL,
            batch_id INTEGER NOT NULL, profile TEXT NOT NULL,
            required_target_count INTEGER NOT NULL, current_image_key TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO review_plan VALUES (
            '1070:5947:profileID_1', '1070', 5947, 'profileID_1', 3,
            '1070:5947:1:1:profileID_1', '2026-01-01T00:00:00+00:00',
            '2026-01-01T00:00:00+00:00'
        );
        """
    )
    connection.close()


def _schema(database_path: Path) -> tuple[int, set[str], set[str]]:
    connection = sqlite3.connect(database_path)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    columns = {row[1] for row in connection.execute("PRAGMA table_info(review_plan)")}
    connection.close()
    return version, tables, columns


def test_interrupted_migration_leaves_database_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    from xtalflow.application import ReviewPersistenceError
    from xtalflow.infrastructure import review_migrations

    database_path = tmp_path / "reviews.sqlite3"
    _legacy_required_count_database(database_path)
    before = _schema(database_path)

    def interrupted(connection):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(review_migrations, "_import_standalone_reviews", interrupted)
    with pytest.raises(ReviewPersistenceError):
        SQLiteReviewStore(database_path)

    assert _schema(database_path) == before
    monkeypatch.undo()
    store = SQLiteReviewStore(database_path)
    _, preferences = store.workspace.load_review_state("1070:5947:profileID_1")
    assert preferences.auto_advance_target_count == 3
    store.close()


def test_database_from_newer_xtalflow_is_not_opened_or_downgraded(
    tmp_path: Path,
) -> None:
    from xtalflow.application import ReviewPersistenceError

    database_path = tmp_path / "reviews.sqlite3"
    SQLiteReviewStore(database_path).close()
    connection = sqlite3.connect(database_path)
    connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()

    with pytest.raises(ReviewPersistenceError, match="newer than this XtalFlow"):
        SQLiteReviewStore(database_path)

    assert _schema(database_path)[0] == LATEST_SCHEMA_VERSION + 1


def test_existing_database_is_backed_up_only_before_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "reviews.sqlite3"
    fresh = SQLiteReviewStore(database_path)
    assert fresh.upgrade_backup_path is None
    fresh.close()
    assert SQLiteReviewStore(database_path).upgrade_backup_path is None

    legacy_path = tmp_path / "legacy.sqlite3"
    _legacy_required_count_database(legacy_path)
    upgraded = SQLiteReviewStore(legacy_path)
    backup_path = upgraded.upgrade_backup_path
    upgraded.close()

    assert backup_path is not None and backup_path.name.startswith(
        "legacy.sqlite3.schema0-"
    )
    version, tables, columns = _schema(backup_path)
    assert (version, tables) == (0, {"review_plan"})
    assert "auto_advance_target_count" not in columns
    assert _schema(legacy_path)[0] == LATEST_SCHEMA_VERSION


def test_damaged_experiment_project_does_not_hide_other_projects(tmp_path: Path) -> None:
    from xtalflow.application import ReviewPersistenceError
    from xtalflow.application.planning_service import PlanningService

    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    now = datetime.now(timezone.utc)
    image = CrystalImage("1070", 5947, 1, 1, "profileID_1", Path("image.jpg"))
    target = ReviewSession().add_target(image, 10, 20, 100, 100)
    crystals = (
        SelectedCrystal(
            image.image_key, "1070", "A01a",
            (CrystalTarget(target.id, Decimal(0), Decimal(0), now),),
            SWISSCI_MIDI_3_LENS.id,
        ),
    )
    service = PlanningService(store.planning)
    for plan_id in ("damaged", "healthy"):
        service.save_selection_snapshot(
            plan_id, plan_id, PlanType.RAW_CRYSTAL,
            crystal_selection_from_selected_crystals(plan_id, crystals), now,
        )
    store._connection.execute(
        "UPDATE experiment_plan SET plan_type = 'retired' WHERE project_id = 'damaged'"
    )
    store._connection.commit()

    assert store.planning.load_experiment_project("healthy").id == "healthy"
    with pytest.raises(ReviewPersistenceError, match="experiment project damaged"):
        store.planning.load_experiment_project("damaged")
    store.close()
