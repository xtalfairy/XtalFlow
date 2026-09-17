import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QInputDialog, QMessageBox

from xtalflow.domain import (
    PLATE_FORMATS,
    ImageFilter,
    ReviewSession,
    SWISSCI_MIDI_3_LENS,
    SWISSCI_MRC_2_WELL,
)
from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
)
from xtalflow.application import ReviewPersistenceError
from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
from xtalflow.ui.plan_editors import FragmentScreeningDialog
from xtalflow.ui.load_plates_dialog import LoadPlatesDialog
from xtalflow.viewer import ViewerWindow
from xtalflow.viewer import main
from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments
from xtalflow.infrastructure.user_preferences import JsonUserPreferencesStore
from xtalflow.domain.plan_lifecycle import PlanningDraft


FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root


class _FakeMxLiveReader:
    experiment_ids_by_year: dict[int, tuple[str, ...]] = {}
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    def experiment_ids(self, year):
        if self.error is not None:
            raise self.error
        return self.experiment_ids_by_year.get(year, ())

    def labworks(self, experiment_id):
        return ()


def _fragment(number: int) -> Fragment:
    return Fragment(
        "Vendor",
        "Library",
        str(number),
        f"CMP-{number}",
        "C2H6O",
        Decimal("46.07"),
        "CCO",
        Decimal("100"),
        "DMSO",
        "SRC",
        f"A{number:02d}",
    )


def test_fragment_plan_dialog_previews_and_reassigns_by_plate_well() -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime.now(timezone.utc)
    crystals = (
        SelectedCrystal(
            "first",
            "20",
            "A01a",
            (CrystalTarget("t1", Decimal(0), Decimal(0), now),),
            SWISSCI_MIDI_3_LENS.id,
        ),
        SelectedCrystal(
            "second",
            "3",
            "A01a",
            (CrystalTarget("t2", Decimal(0), Decimal(0), now + timedelta(seconds=1)),),
            SWISSCI_MIDI_3_LENS.id,
        ),
    )
    dialog = FragmentScreeningDialog(
        FragmentLibrary("Library", (_fragment(8), _fragment(15))), crystals
    )

    assert dialog.table.item(0, 0).text() == "1"
    assert dialog.table.item(0, 1).text() == "20"
    assert dialog.table.item(0, 5).text() == "CMP-8"
    assert [
        dialog.editor.preview_tabs.tabText(index)
        for index in range(dialog.editor.preview_tabs.count())
    ] == ["Summary", "ECHO Worksheet", "SHIFTER Worksheet", "WebDB"]
    assert dialog.editor.echo_table.rowCount() == 2
    assert dialog.editor.shifter_table.rowCount() == 2
    dialog.order_input.setCurrentIndex(
        dialog.order_input.findData(AssignmentOrder.PLATE_WELL)
    )
    assert dialog.table.item(0, 1).text() == "3"
    assert dialog.table.item(0, 5).text() == "CMP-8"
    dialog.rows_input.setText("2")
    assert dialog.current_plan is None
    assert "enough fragments" in dialog.error_label.text()
    assert dialog.editor.echo_table.rowCount() == 0
    assert dialog.editor.shifter_table.rowCount() == 0
    dialog.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_viewer_loads_and_navigates_images() -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), auto_advance_target_count=2
    )

    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    first_text = window.navigation_label.text()
    first_position = window.position_label.text()
    first_well = window.well_input.text()
    assert window.auto_advance_input.prefix() == "Targets/img: "
    assert window.target_summary_button.text() == "Target Summary"
    assert window.previous_button.text() == "◀"
    assert window.next_button.text() == "▶"
    assert window.save_status_label.parent() is window.statusBar()
    assert window.status_message_label.width() == 280
    assert window.status_message_label.parent() is window.statusBar()
    window.show_next()

    assert "Plate 2070" in first_text
    assert "Batch 14122" in first_position
    assert first_well == "A01a"
    assert window.well_input.text() == "A01b"
    assert window.controller.image_index == 1
    assert window.image_path_status.toolTip() == str(
        window.controller.current_image.path.resolve()
    )
    assert window.controller.current_image.path.name in window.image_path_status.text()
    window.well_input.setText("a2A")
    window._go_to_entered_well()
    assert window.well_input.text() == "A02a"
    assert window.controller.current_image.well_number == 2
    window.well_input.setText("A01d")
    window._go_to_entered_well()
    assert window.well_input.text() == "A02a"
    assert "Invalid" in window.status_message_label.text()
    assert window.controller.image_index == 2
    assert window.navigation_label.text() != first_text
    assert not window.image_canvas.pixmap().isNull()
    window.close()
    app.processEvents()


def test_status_bar_is_visible_before_navigation(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(RockMakerImageRepository(tmp_path))
    window.show()
    app.processEvents()
    assert window.statusBar().isVisible()
    assert window.statusBar().height() > 0
    window.close()
    app.processEvents()


def test_empty_workspace_clears_image_from_previous_workspace(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(RockMakerImageRepository(tmp_path))
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.white)
    window.image_canvas.set_image(pixmap, ())
    window.well_input.setText("A01a")
    window.navigation_label.setText("Plate 2069 · Well A01a")

    window.project_controller.create_project("Empty Workspace")
    window._adopt_active_review()
    window._sync_project_widgets()

    assert window.image_canvas.pixmap().isNull()
    assert window.well_input.text() == ""
    assert window.navigation_label.text() == "No image set loaded"
    assert window.review_summary_label.text() == (
        "Add a plate to the active workspace"
    )
    assert window.project_progress_label.text() == "Workspace: no images"
    assert not window.previous_button.isEnabled()
    assert not window.next_button.isEnabled()
    window.close()
    app.processEvents()


def test_viewer_starts_when_workspace_images_are_unavailable(tmp_path: Path) -> None:
    from xtalflow.domain import Project

    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Offline share")
    project.add_image_set(
        "1070", 5947, "profileID_1", "1070:5947:1:1:profileID_1",
        SWISSCI_MIDI_3_LENS.id, SWISSCI_MIDI_3_LENS.version,
    )
    store.workspace.save_project(project)
    store.workspace.save_last_open_project(project.id)

    class OfflineRepository(RockMakerImageRepository):
        def load_plate_batch(self, *args, **kwargs):
            raise OSError(112, "Host is down")

    window = ViewerWindow(OfflineRepository(tmp_path), store)

    assert window.controller is None
    assert window.project_controller.active_project.id == project.id
    assert "Host is down" in window.review_summary_label.text()
    window.close()
    app.processEvents()


def test_background_io_keeps_event_loop_running_and_reraises_errors(
    tmp_path: Path,
) -> None:
    import threading
    import time

    from PyQt5.QtCore import QTimer

    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(RockMakerImageRepository(tmp_path))
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)

    def slow_network_call():
        time.sleep(0.2)
        return threading.current_thread() is threading.main_thread()

    ran_on_ui_thread = window._run_in_background("Working…", slow_network_call)
    timer.stop()

    assert ran_on_ui_thread is False
    assert len(ticks) >= 5

    def dropped_share():
        raise OSError(112, "Host is down")

    with pytest.raises(OSError, match="Host is down"):
        window._run_in_background("Working…", dropped_share)
    window.close()
    app.processEvents()


def test_auto_confirm_confidence_is_saved_per_user(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    preferences_path = tmp_path / ".config" / "xtalflow" / "preferences.json"
    first = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        preferences_store=JsonUserPreferencesStore(preferences_path),
    )
    first.auto_confirm_confidence_input.setValue(96)
    first.close()

    restored = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        preferences_store=JsonUserPreferencesStore(preferences_path),
    )
    assert restored.auto_confirm_confidence_input.value() == 96
    assert str(preferences_path) in restored.auto_confirm_confidence_input.toolTip()
    restored.close()
    app.processEvents()


def test_main_window_separates_image_review_and_planning_tabs(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(RockMakerImageRepository(tmp_path))

    assert window.main_tabs.count() == 2
    assert window.main_tabs.tabText(window.image_review_tab_index) == "Image Review"
    assert window.main_tabs.tabText(window.planning_tab_index) == "Planning"
    assert window.main_tabs.currentIndex() == window.image_review_tab_index
    assert window.plan_list.count() == 0
    assert window.new_plan_button.text() == "+ New Project"
    assert window.new_workspace_action.text() == "New Workspace…"

    window.close()
    app.processEvents()


def test_planning_tab_lists_libraries_from_designated_directory(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    csv_path = tmp_path / "library.csv"
    csv_path.write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        "Vendor,Lib,8,CMP-8,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n",
        encoding="utf-8",
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        store,
        settings=replace(
            DEFAULT_SETTINGS, fragment_library_directory=tmp_path
        ),
    )
    crystal = SelectedCrystal(
        "image",
        "1070",
        "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )

    window._add_fragment_plan(None, (crystal,))
    editor = window.plan_stack.currentWidget()
    editor.library_input.setCurrentIndex(1)

    assert window.plan_list.count() == 1
    assert not window.plan_list_empty_label.isVisible()
    assert editor.library_input.currentText() == "library.csv · 1 rows"
    assert editor.table.item(0, 5).text() == "CMP-8"
    webdb_columns = {
        editor.webdb_table.horizontalHeaderItem(index).text(): index
        for index in range(editor.webdb_table.columnCount())
    }
    assert editor.webdb_table.rowCount() == 1
    assert editor.webdb_table.item(0, webdb_columns["expri_id"]).text() == (
        "Pending finalization"
    )
    assert editor.webdb_table.item(0, webdb_columns["protein_name"]).text() == ""
    window.close()
    app.processEvents()


def test_refreshing_libraries_keeps_selected_library_rows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    (tmp_path / "library.csv").write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        + "".join(
            f"Vendor,Lib,{number},CMP-{number},C2H6O,46.07,CCO,100,DMSO,SRC-1,A{number:02d}\n"
            for number in (1, 2, 3)
        ),
        encoding="utf-8",
    )
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=replace(DEFAULT_SETTINGS, fragment_library_directory=tmp_path),
    )
    crystal = SelectedCrystal(
        "image",
        "1070",
        "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_fragment_plan(None, (crystal,))
    editor = window.plan_stack.currentWidget()
    editor.library_input.setCurrentIndex(1)
    assert editor.rows_input.text() == "1-3"
    editor.rows_input.setText("2-3")

    window._refresh_fragment_library_choices()

    assert editor.library_input.currentIndex() == 1
    assert editor.rows_input.text() == "2-3"
    window.close()
    app.processEvents()


def test_damaged_saved_plan_does_not_block_other_plans(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    window = ViewerWindow(RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path))
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    for protein in ("DAMAGED", "HEALTHY"):
        window._add_raw_crystal_plan((crystal,))
        editor = window.plan_stack.currentWidget()
        editor.protein_input.setText(protein)
        window._persist_draft(editor)
    damaged_id = window.plan_stack.widget(1).plan_id
    window.close()
    app.processEvents()
    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE experiment_plan SET plan_type = 'retired' WHERE project_id = ?",
        (damaged_id,),
    )
    connection.commit()
    connection.close()

    restored = ViewerWindow(RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path))

    assert restored.plan_list.count() == 1
    assert restored.plan_stack.widget(1).protein_input.text() == "HEALTHY"
    restored.close()
    app.processEvents()


def test_raw_crystal_plan_has_shifter_preview_without_echo(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    selected_at = datetime.now(timezone.utc)
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (
            CrystalTarget("target", Decimal(0), Decimal(0), selected_at),
            CrystalTarget(
                "target-2", Decimal("0.1"), Decimal("-0.2"),
                selected_at + timedelta(seconds=1),
            ),
        ),
        SWISSCI_MIDI_3_LENS.id,
    )

    window.main_tabs.setCurrentIndex(window.planning_tab_index)
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    window._persist_draft(editor)

    assert editor.current_plan is not None
    assert editor.shifter_table.rowCount() == 1
    assert editor.summary_table.horizontalHeaderItem(3).text() == (
        "Soaking Position"
    )
    assert editor.summary_table.item(0, 3).text() == "1"
    assert editor.summary_table.item(1, 3).text() == "2"
    assert editor.summary_table.item(0, 3).data(Qt.UserRole) == "target"
    assert editor.summary_table.item(0, 4).text() == "Original"
    assert editor.preview_tabs.tabText(2) == "WebDB"
    assert editor.webdb_table.rowCount() == 1
    columns = {
        editor.webdb_table.horizontalHeaderItem(index).text(): index
        for index in range(editor.webdb_table.columnCount())
    }
    assert editor.webdb_table.item(0, columns["protein_name"]).text() == "BRD4"
    assert editor.webdb_table.item(0, columns["plate_type"]).text() == (
        "SwissCI-MRC-3d"
    )
    assert editor.webdb_table.item(0, columns["plate_code"]).text() == "1070"
    assert editor.webdb_table.item(0, columns["plate_well"]).text() == "A01a"
    assert editor.webdb_table.item(0, columns["soak_id"]).text() == ""
    assert editor.webdb_table.item(0, columns["soak_smile"]).text() == ""
    assert editor.webdb_table.item(0, columns["project_id"]).text() != ""
    assert not hasattr(editor, "echo_table")
    drafts = store.planning.load_planning_drafts(window.project_controller.active_project.id)
    assert drafts[-1].plan_type == "raw_crystal"

    window._add_raw_crystal_plan((crystal,))
    reused_editor = window.plan_stack.currentWidget()
    assert reused_editor.summary_table.item(0, 4).text() == "Reused"
    assert "Raw Crystal Project #1" in (
        reused_editor.summary_table.item(0, 4).toolTip()
    )
    assert editor.summary_table.item(0, 4).text() == "Original"
    window.close()
    app.processEvents()


def test_draft_plan_can_be_deleted_from_planning_sidebar(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_raw_crystal_plan((crystal,))
    plan_id = window.plan_stack.currentWidget().plan_id
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )

    window._delete_selected_draft_plan()
    app.processEvents()

    assert window.plan_list.count() == 0
    assert window.plan_stack.currentIndex() == 0
    assert all(
        draft.id != plan_id
        for draft in store.planning.load_planning_drafts(
            window.project_controller.active_project.id
        )
    )
    assert store.planning.load_experiment_project(plan_id) is None
    window.close()
    app.processEvents()


def test_raw_webdb_preview_is_populated_without_mxlive_configuration(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    invalid_config = tmp_path / "invalid.toml"
    invalid_config.write_text("[mxlive\n", encoding="utf-8")
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        store,
        settings=replace(DEFAULT_SETTINGS, mxlive_config_path=invalid_config),
    )
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
        "/rmserver/image.jpg",
    )

    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    columns = {
        editor.webdb_table.horizontalHeaderItem(index).text(): index
        for index in range(editor.webdb_table.columnCount())
    }

    assert editor.webdb_table.rowCount() == 1
    assert editor.webdb_table.item(0, columns["expri_id"]).text() == (
        "Pending finalization"
    )
    assert editor.webdb_table.item(0, columns["plate_code"]).text() == "1070"
    assert "preview records" in editor.webdb_status_label.text()
    window.close()
    app.processEvents()


def test_new_plan_owns_selection_snapshot_when_review_targets_change(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    selected_at = datetime.now(timezone.utc)
    original = SelectedCrystal(
        "original-image", "2069", "A04a",
        (CrystalTarget("original-target", Decimal(0), Decimal(0), selected_at),),
        SWISSCI_MIDI_3_LENS.id,
    )
    replacement = SelectedCrystal(
        "replacement-image", "2070", "B02c",
        (CrystalTarget("replacement-target", Decimal(0), Decimal(0), selected_at),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_raw_crystal_plan((original,))
    editor = window.plan_stack.currentWidget()
    monkeypatch.setattr(
        window.project_controller,
        "selected_crystals_for_plan",
        lambda: (replacement,),
    )

    window._main_tab_changed(window.planning_tab_index)

    assert editor.selection.wells[0].image_key == original.image_key
    owned = store.planning.load_experiment_project(editor.plan_id)
    assert owned is not None
    assert owned.crystal_selection.wells[0].image_key == "original-image"
    window.close()
    app.processEvents()


def test_legacy_draft_requires_explicit_selection_adoption(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    now = datetime.now(timezone.utc)
    workspace_id = window.project_controller.active_project.id
    draft = PlanningDraft(
        "legacy-draft", workspace_id, "raw_crystal", "Legacy Draft", None,
        "", "BRD4", "0", "selection", now, now,
    )
    original = SelectedCrystal(
        "original", "2069", "A01a",
        (CrystalTarget("old", Decimal(0), Decimal(0), now),),
        SWISSCI_MIDI_3_LENS.id,
    )
    adopted = SelectedCrystal(
        "adopted", "2070", "B02c",
        (CrystalTarget("new", Decimal("0.1"), Decimal("0.2"), now),),
        SWISSCI_MIDI_3_LENS.id,
    )
    store.planning.save_planning_draft(draft)
    window._add_raw_crystal_plan((original,), restored=draft)
    editor = window.plan_stack.currentWidget()
    monkeypatch.setattr(
        window.project_controller,
        "selected_crystals_for_plan",
        lambda: (adopted,),
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )

    assert not editor.adopt_selection_button.isHidden()
    assert not editor.selection_snapshot_owned
    window._adopt_legacy_selection(editor)

    assert editor.selection_snapshot_owned
    assert editor.selection.wells[0].image_key == "adopted"
    assert store.planning.load_experiment_project(editor.plan_id) is not None
    assert editor.adopt_selection_button.isHidden()
    window.close()
    app.processEvents()


def test_only_finalized_raw_revision_can_be_uploaded_and_is_audited(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    settings = replace(
        DEFAULT_SETTINGS,
        mxlive_base_url="https://mxlive.example",
        mxlive_key_path=key,
        mxlive_ca_bundle=None,
        mxlive_config_path=None,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveReadClient", _FakeMxLiveReader)
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
    )
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    assert not editor.webdb_upload_button.isEnabled()

    unexpected_dialogs = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: unexpected_dialogs.append(("warning", args[2])),
    )
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *args, **kwargs: unexpected_dialogs.append(("critical", args[2])),
    )

    revision = window._finalize_plan(editor)
    assert revision is not None, unexpected_dialogs
    assert editor.webdb_upload_button.isEnabled()

    class FakeWriter:
        def __init__(self, *args, **kwargs):
            pass

        def upload_labworks(self, records):
            assert records[0]["plate_imgpath"] == "/rmserver/image.jpg"
            return {"created": len(records)}

    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveWriteClient", FakeWriter)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)
    window._upload_plan_labworks(editor)

    events = store.audit.list_webdb_uploads(revision.id)
    assert len(events) == 1
    assert events[0].status == "succeeded"
    assert events[0].account_id == events[0].username
    assert not editor.webdb_upload_button.isEnabled()
    assert unexpected_dialogs == []
    window.close()
    app.processEvents()


def _finalized_raw_upload_window(tmp_path: Path, monkeypatch):
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    settings = replace(
        DEFAULT_SETTINGS,
        mxlive_base_url="https://mxlive.example",
        mxlive_key_path=key,
        mxlive_ca_bundle=None,
        mxlive_config_path=None,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveReadClient", _FakeMxLiveReader)
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
    )
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    dialogs = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            lambda *args, _name=name, **kwargs: dialogs.append((_name, args[2])),
        )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window._finalize_plan(editor)
    assert revision is not None, dialogs
    return window, store, editor, revision, dialogs


class _RecordingWriter:
    posted = []
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    def upload_labworks(self, records):
        type(self).posted.append(records)
        if type(self).error is not None:
            raise type(self).error
        return {"created": len(records)}


def test_unknown_upload_result_locks_until_verified_on_mxlive(
    tmp_path: Path, monkeypatch
) -> None:
    from xtalflow.domain.mxlive import MxLiveLabwork, MxLiveUncertainWriteError

    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )

    class TimeoutWriter(_RecordingWriter):
        posted = []
        error = MxLiveUncertainWriteError("MxLive upload result is unknown (ReadTimeout)")

    stored_records: list[MxLiveLabwork] = []

    class FakeReader:
        def __init__(self, *args, **kwargs):
            pass

        def labworks(self, experiment_id):
            return tuple(stored_records)

    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveWriteClient", TimeoutWriter)
    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveReadClient", FakeReader)

    window._upload_plan_labworks(editor)

    assert [event.status for event in store.audit.list_webdb_uploads(revision.id)] == [
        "unknown"
    ]
    assert editor.webdb_upload_button.text() == "Verify on MxLive…"
    assert editor.webdb_upload_button.isEnabled()

    window._upload_plan_labworks(editor)
    assert len(TimeoutWriter.posted) == 1
    assert [event.status for event in store.audit.list_webdb_uploads(revision.id)] == [
        "failed"
    ]
    assert editor.webdb_upload_button.text() == "Upload Finalized Revision…"
    assert editor.webdb_upload_button.isEnabled()
    assert dialogs[-1][0] == "information"
    window.close()
    app.processEvents()


def test_upload_is_not_sent_when_attempt_cannot_be_audited(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )

    class Writer(_RecordingWriter):
        posted = []

    def audit_unavailable(event):
        raise ReviewPersistenceError("database is locked")

    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveWriteClient", Writer)
    monkeypatch.setattr(store.audit, "record_webdb_upload", audit_unavailable)

    window._upload_plan_labworks(editor)

    assert Writer.posted == []
    assert dialogs[-1] == (
        "critical", "The upload audit record could not be saved:\ndatabase is locked"
    )
    window.close()
    app.processEvents()


def test_later_revision_with_uploaded_experiment_id_is_not_uploaded_again(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )

    class Writer(_RecordingWriter):
        posted = []

    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveWriteClient", Writer)
    window._upload_plan_labworks(editor)
    editor.protein_input.setText("BRD4 variant")
    second = window._finalize_plan(editor)

    assert second is not None and second.revision == 2
    assert not editor.webdb_upload_button.isEnabled()
    assert "Uploaded (earlier revision)" in editor.webdb_status_label.text()
    window._upload_plan_labworks(editor)
    assert len(Writer.posted) == 1
    assert dialogs[-1][0] == "information"
    window.close()
    app.processEvents()


def test_new_experiment_id_skips_ids_already_used_on_mxlive(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    month = datetime.now()
    taken = f"RawCrystal-{month:%Y%m}-BRD4-01"

    monkeypatch.setattr(
        _FakeMxLiveReader, "experiment_ids_by_year", {month.year: (taken,)}
    )
    window, store, editor, revision, dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )

    assert revision.experiment_id == f"RawCrystal-{month:%Y%m}-BRD4-02"
    window.close()
    app.processEvents()


def test_unchecked_experiment_id_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    settings = replace(
        DEFAULT_SETTINGS, mxlive_base_url="https://mxlive.example",
        mxlive_key_path=key, mxlive_ca_bundle=None, mxlive_config_path=None,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)

    from xtalflow.domain.mxlive import MxLiveReadError

    class OfflineReader(_FakeMxLiveReader):
        error = MxLiveReadError("MxLive request failed (ConnectionError)")

    monkeypatch.setattr("xtalflow.viewer.LegacyMxLiveReadClient", OfflineReader)
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    questions = []

    def answer(response):
        def question(*args, **kwargs):
            questions.append(args[1])
            return response
        return question

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.No))
    assert window._finalize_plan(editor) is None
    assert store.planning.list_plan_revisions(editor.plan_id) == ()

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.Yes))
    assert window._finalize_plan(editor) is not None
    assert questions == ["Experiment ID not checked"] * 2
    window.close()
    app.processEvents()


def _fragment_plan_window(tmp_path: Path, settings):
    (tmp_path / "library.csv").write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        "Vendor,Lib,1,CMP-1,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n",
        encoding="utf-8",
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), store,
        settings=replace(settings, fragment_library_directory=tmp_path),
    )
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_fragment_plan(None, (crystal,))
    editor = window.plan_stack.currentWidget()
    editor.library_input.setCurrentIndex(1)
    editor.protein_input.setText("BRD4")
    window._choose_worksheet_assignment_order = lambda: AssignmentOrder.SELECTION
    return window, store, editor


def test_fragment_worksheets_are_saved_and_audited(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650",
            tmp_path / "shifter1",
            tmp_path / "shifter2",
        ),
        create_missing_instrument_roots=True,
    )
    window, store, editor, = _fragment_plan_window(tmp_path, settings)
    messages = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: messages.append(args[1])
    )

    window._save_plan_worksheets(editor)

    revision = editor.last_revision
    exports = store.audit.list_worksheet_exports(revision.id)
    assert messages == ["Worksheets saved"]
    assert [event.status for event in exports] == ["succeeded"]
    assert Path(exports[0].path_for("echo650")).is_file()
    assert Path(exports[0].path_for("shifter2")).is_file()
    window.close()
    app.processEvents()


def test_unavailable_instrument_share_can_use_alternate_root(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "missing-echo",
            tmp_path / "missing-shifter1",
            tmp_path / "missing-shifter2",
        ),
        create_missing_instrument_roots=False,
    )
    window, store, editor = _fragment_plan_window(tmp_path, settings)
    monkeypatch.setattr(QMessageBox, "exec_", lambda dialog: 0)
    monkeypatch.setattr(
        QMessageBox, "clickedButton",
        lambda dialog: next(
            button for button in dialog.buttons() if button.text().startswith("Choose")
        ),
    )
    monkeypatch.setattr(
        "xtalflow.viewer.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path / "chosen"),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    window._save_plan_worksheets(editor)

    exports = store.audit.list_worksheet_exports(editor.last_revision.id)
    assert [event.status for event in exports] == ["succeeded"]
    assert exports[0].path_for("echo650").startswith(str(tmp_path / "chosen" / "echo650"))
    window.close()
    app.processEvents()


def test_raw_plan_keeps_experiment_id_across_revisions(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Ok)

    first = window._finalize_plan(editor)
    editor.protein_input.setText("BRD4 variant")
    second = window._finalize_plan(editor)

    assert first is not None and second is not None
    assert second.revision == 2
    assert second.experiment_id == first.experiment_id
    assert "fixed for this plan" in editor.experiment_id_label.text()
    restored = store.planning.load_planning_drafts(editor.project_id)
    assert restored[-1].experiment_id == first.experiment_id
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_zoom_pan_and_fit_preserve_original_pixel_coordinates() -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), auto_advance_target_count=10
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.show()
    app.processEvents()
    canvas = window.image_canvas
    anchor = canvas.rect().center()
    before_zoom = canvas.transform().viewport_to_image(anchor.x(), anchor.y())

    canvas._set_zoom(2.0, anchor)
    after_zoom = canvas.transform().viewport_to_image(anchor.x(), anchor.y())
    assert after_zoom == pytest.approx(before_zoom)
    assert window.zoom_label.text() == "200%"

    QTest.mousePress(canvas, Qt.MiddleButton, pos=anchor)
    QTest.mouseMove(canvas, anchor + QPoint(40, 30))
    QTest.mouseRelease(canvas, Qt.MiddleButton, pos=anchor + QPoint(40, 30))
    assert canvas._pan_x != 0 or canvas._pan_y != 0

    expected_x, expected_y = 500.0, 400.0
    viewport_x, viewport_y = canvas.transform().image_to_viewport(
        expected_x, expected_y
    )
    QTest.mouseClick(
        canvas,
        Qt.LeftButton,
        pos=QPoint(round(viewport_x), round(viewport_y)),
    )
    target = window.controller.current_targets[-1]
    assert target.x_px == pytest.approx(expected_x, abs=0.6)
    assert target.y_px == pytest.approx(expected_y, abs=0.6)

    canvas.fit_image()
    assert canvas.zoom == 1.0
    assert window.zoom_label.text() == "100%"
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_target_summary_uses_hidden_right_dock_and_jumps_to_image(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    target_image_key = window.controller.current_image.image_key
    window.show()
    app.processEvents()
    review_only_width = window.width()

    assert not window.target_summary_dock.isVisible()
    window.target_summary_button.click()
    app.processEvents()
    assert window.target_summary_dock.isVisible()
    assert window.target_summary_button.isChecked()
    assert window.dockWidgetArea(window.target_summary_dock) == Qt.RightDockWidgetArea
    assert window.width() == review_only_width
    window.target_summary_dock.hide()
    app.processEvents()
    assert not window.target_summary_button.isChecked()
    assert window.width() == review_only_width
    window.target_summary_dock.show()
    app.processEvents()

    window._handle_image_click(600, 500, Qt.LeftButton)
    second_target_image_key = window.controller.current_image.image_key
    window._handle_image_click(610, 510, Qt.LeftButton)
    assert window.target_summary_table.rowCount() == 2
    assert window.target_summary_table.item(0, 1).text() == "A01a"
    assert window.target_summary_table.item(0, 3).text() != "—"
    window._review_target_warnings()
    assert window.target_summary_filter.currentData() == "warnings"
    assert window.target_summary_dock.isVisible()
    assert window.target_summary_table.rowCount() == 2
    window.target_summary_filter.setCurrentIndex(
        window.target_summary_filter.findData("all")
    )

    window.target_summary_table.setCurrentCell(0, 0)
    assert window.controller.current_image.image_key == target_image_key
    assert window.target_summary_table.hasFocus()
    assert "Unconfirmed well boundary" in window.target_summary_table.item(0, 5).text()
    assert window.accept_calibration_button.isEnabled()
    window.accept_calibration_button.click()
    assert window.current_calibration.confirmed
    assert window.target_summary_table.item(0, 5).text() == "✓ Ready"
    assert not window.accept_calibration_button.isEnabled()
    QTest.keyClick(window.target_summary_table, Qt.Key_Down)
    assert window.target_summary_table.currentRow() == 1
    assert window.controller.current_image.image_key == second_target_image_key
    window.target_summary_filter.setCurrentIndex(
        window.target_summary_filter.findData("warnings")
    )
    assert window.target_summary_table.rowCount() == 1
    assert "Unconfirmed well boundary" in window.target_summary_table.item(0, 5).text()
    window.target_summary_filter.setCurrentIndex(
        window.target_summary_filter.findData("all")
    )
    window.target_summary_table.selectAll()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )
    window._remove_selected_targets()
    assert window.target_summary_table.rowCount() == 0
    assert window.controller.session.target_count == 0
    assert window.review_store.workspace.target_count_for_image_set(
        window.project_controller.active_image_set.id
    ) == 0
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_valid_automatic_wells_can_be_confirmed_in_bulk(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window._handle_image_click(610, 510, Qt.LeftButton)

    assert (
        window.project_controller.valid_unconfirmed_automatic_calibration_count()
        == 1
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )
    window._accept_valid_auto_wells()

    summaries = window.project_controller.project_target_summaries()
    assert all(summary.is_ready for summary in summaries)
    assert window.current_calibration.confirmed
    assert (
        window.project_controller.valid_unconfirmed_automatic_calibration_count()
        == 0
    )
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_trusted_plate_auto_confirms_well_above_user_threshold(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        preferences_store=JsonUserPreferencesStore(tmp_path / "preferences.json"),
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_confidence_input.setValue(50)
    assert window.current_calibration is not None
    assert window.auto_confirm_plate_checkbox.isChecked()
    assert window.current_calibration.confirmed
    assert "Well aligned" in window.calibration_label.text()
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_typing_auto_confirm_threshold_applies_only_finished_value(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        preferences_store=JsonUserPreferencesStore(tmp_path / "preferences.json"),
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    assert window.auto_confirm_plate_checkbox.isChecked()
    thresholds = []
    window.project_controller.confirm_valid_automatic_calibrations = (
        lambda threshold, image_set_id: thresholds.append(threshold)
    )
    line_edit = window.auto_confirm_confidence_input.lineEdit()
    line_edit.setFocus()
    line_edit.selectAll()

    QTest.keyClicks(line_edit, "85")
    assert thresholds == []
    QTest.keyClick(line_edit, Qt.Key_Return)

    assert thresholds == [0.85]
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_unexpected_slot_error_saves_current_targets_and_reports(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT), store)
    window.error_log_path = tmp_path / "xtalflow-errors.log"
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    image = window.controller.current_image
    window.controller.add_target(10, 20, 1224, 1024)
    messages = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *args, **kwargs: messages.append(args[2])
    )

    try:
        raise OSError(112, "Host is down")
    except OSError as error:
        window.handle_unexpected_error(type(error), error, error.__traceback__)

    image_set = window.project_controller.active_image_set
    assert len(store.workspace.scoped_to(image_set.id).load_images((image.image_key,))) == 1
    assert "Targets on the current image were saved." in messages[0]
    assert "Host is down" in window.error_log_path.read_text(encoding="utf-8")
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_auto_well_asks_before_replacing_confirmed_calibration(
    tmp_path: Path, monkeypatch
) -> None:
    from xtalflow.domain import CalibrationMethod

    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    manual = replace(
        window.current_calibration,
        method=CalibrationMethod.MANUAL_THREE_POINT,
        confirmed=True,
    )
    window.calibration_service.save(manual)
    window._load_current_calibration()
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: questions.append(args[1]) or QMessageBox.No,
    )

    window._auto_detect_calibration()

    assert questions == ["Replace well calibration"]
    assert window.current_calibration.method is CalibrationMethod.MANUAL_THREE_POINT
    assert window.current_calibration.confirmed
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_live_selection_summary_highlights_counts_and_stays_in_image_review(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=5,
        preferences_store=JsonUserPreferencesStore(tmp_path / "preferences.json"),
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window._handle_image_click(640, 520, Qt.LeftButton)
    window.show()
    window.target_summary_button.click()
    app.processEvents()

    assert window.target_summary_dock.windowTitle() == "Selection · Live"
    assert window.target_summary_filter.itemText(0) == "All positions (2)"
    assert window.target_summary_filter.itemText(1) == "Warnings (2)"
    assert "2 need attention" in window.target_summary_status_label.text()
    assert not window.remove_targets_button.isEnabled()

    window.target_summary_table.setCurrentCell(1, 0)
    second_target = window.target_summary_table.item(1, 0).data(Qt.UserRole + 2)
    assert window.image_canvas._highlighted_target_id == second_target
    window.target_summary_table.selectAll()
    assert window.remove_targets_button.text() == "Delete Selected (2)"

    window.main_tabs.setCurrentIndex(window.planning_tab_index)
    app.processEvents()
    assert not window.target_summary_dock.isVisible()
    window.main_tabs.setCurrentIndex(window.image_review_tab_index)
    app.processEvents()
    assert window.target_summary_dock.isVisible()
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_manual_calibration_clicks_do_not_create_targets(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    image = window.controller.current_image
    window._start_manual_calibration()

    for point in ((1100, 512), (612, 1000), (124, 512)):
        window._handle_image_click(*point, Qt.LeftButton)

    assert window.controller.session.target_count_for(image) == 0
    assert window.current_calibration.confirmed
    assert "Manual" in window.calibration_label.text()
    window.close()
    app.processEvents()

@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_target_selection_survives_image_navigation() -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), auto_advance_target_count=2
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    first_image = window.plate.images[0]

    window._handle_image_click(100, 120, Qt.LeftButton)
    assert len(window.controller.session.targets_for(first_image)) == 1
    window.show_next()
    assert "This well: 0 positions" in window.review_summary_label.text()
    window.show_previous()
    assert "This well: 1 position " in window.review_summary_label.text()

    window._handle_image_click(100, 120, Qt.RightButton)
    assert window.controller.session.targets_for(first_image) == ()
    window.close()
    app.processEvents()


def test_startup_missing_plate_returns_clean_error(tmp_path: Path, capsys) -> None:
    assert main(
        [
            "--root",
            str(tmp_path),
            "--plate",
            "1070",
            "--plate-format",
            SWISSCI_MIDI_3_LENS.id,
            "--review-db",
            str(tmp_path / "reviews.sqlite3"),
        ]
    ) == 2
    assert "plate directory does not exist" in capsys.readouterr().err


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_auto_advance_count_moves_and_navigation_persists(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    first_image = window.plate.images[0]

    window._handle_image_click(100, 120, Qt.LeftButton)

    assert window.controller.image_index == 1
    assert len(window.controller.session.targets_for(first_image)) == 1
    window.close()
    app.processEvents()

    restored_window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    restored_window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    restored = restored_window.controller.session.targets_for(first_image)
    assert restored_window.controller.image_index == 1
    assert len(restored) == 1
    assert (restored[0].x_px, restored[0].y_px) == (100, 120)
    restored_window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_auto_advance_count_updates_plan_and_is_restored(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    preferences_path = tmp_path / "preferences.json"
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path),
        preferences_store=JsonUserPreferencesStore(preferences_path),
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)

    window.auto_advance_input.setValue(4)
    assert window.controller.preferences.auto_advance_target_count == 4
    window.close()
    app.processEvents()

    restored_window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path),
        preferences_store=JsonUserPreferencesStore(preferences_path),
    )
    restored_window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    assert restored_window.controller.preferences.auto_advance_target_count == 4
    assert restored_window.auto_advance_input.value() == 4
    restored_window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_existing_targets_do_not_define_auto_advance_setting(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    plate = RockMakerImageRepository(FIXTURE_ROOT).load_plate("1070")
    image = plate.images[0]
    session = ReviewSession()
    targets = tuple(
        session.add_target(image, coordinate, coordinate, 1224, 1024)
        for coordinate in (10, 20, 30)
    )
    store = SQLiteReviewStore(database_path)
    store.workspace.save_image(image.image_key, targets)
    store.close()

    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)

    assert window.controller.preferences.auto_advance_target_count == 1
    assert window.auto_advance_input.value() == 1
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_auto_advance_setting_is_advisory_and_manual_next_is_always_allowed() -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), auto_advance_target_count=10
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    first_image = window.plate.images[0]
    for coordinate in range(1, 8):
        window._handle_image_click(coordinate * 10, 100, Qt.LeftButton)

    window.auto_advance_input.setValue(5)

    assert window.controller.image_index == 0
    assert window.controller.session.target_count_for(first_image) == 7
    window.show_next()
    assert window.controller.image_index == 1
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_arrow_keys_navigate_only_when_image_has_focus() -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT))
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    window.show()
    window.plate_filter_input.setFocus()
    app.processEvents()

    QTest.keyClick(window.plate_filter_input, Qt.Key_Right)
    app.processEvents()
    assert window.controller.image_index == 0

    window.image_canvas.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(window.image_canvas, Qt.Key_Right)
    app.processEvents()
    assert window.controller.image_index == 1

    QTest.keyClick(window.image_canvas, Qt.Key_Left)
    app.processEvents()
    assert window.controller.image_index == 0
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_up_down_switch_plates_only_from_image_or_plate_list_focus(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        preferences_store=JsonUserPreferencesStore(tmp_path / "preferences.json"),
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    window.load_plate("1100", SWISSCI_MIDI_3_LENS)
    window.auto_advance_input.setValue(6)
    window.show()

    window.plate_filter_input.setFocus()
    QTest.keyClick(window.plate_filter_input, Qt.Key_Up)
    app.processEvents()
    assert window.controller.plate.plate_code == "1100"

    window.image_canvas.setFocus(Qt.OtherFocusReason)
    QTest.keyClick(window.image_canvas, Qt.Key_Up)
    app.processEvents()
    assert window.controller.plate.plate_code == "1070"
    assert window.auto_advance_input.value() == 6
    assert window.controller.preferences.auto_advance_target_count == 6

    QTest.keyClick(window.image_canvas, Qt.Key_Down)
    app.processEvents()
    assert window.controller.plate.plate_code == "1100"
    assert window.auto_advance_input.value() == 6

    window.image_set_list.setFocus(Qt.OtherFocusReason)
    QTest.keyClick(window.image_set_list, Qt.Key_Up)
    app.processEvents()
    assert window.controller.plate.plate_code == "1070"
    assert window.image_set_list.currentIndex().row() == 0
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_save_status_tracks_working_changes_and_checkpoint(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=2,
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    assert window.save_status_label.text() == "✓ Saved locally"

    window._handle_image_click(100, 120, Qt.LeftButton)
    assert window.save_status_label.text() == "● Unsaved changes"

    window.show_next()
    assert window.save_status_label.text() == "✓ Saved locally"
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_failed_checkpoint_does_not_move_and_shows_failed_state(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=2,
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    window._handle_image_click(100, 120, Qt.LeftButton)
    original_checkpoint = window.controller.store.workspace.save_checkpoint

    def fail_checkpoint(*args) -> None:
        raise ReviewPersistenceError("test storage failure")

    monkeypatch.setattr(window.controller.store, "save_checkpoint", fail_checkpoint)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    window.show_next()

    assert window.controller.image_index == 0
    assert window.save_status_label.text() == "! Save failed"
    monkeypatch.setattr(window.controller.store, "save_checkpoint", original_checkpoint)
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_cancel_close_after_save_failure_keeps_window_and_store_open(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    original_checkpoint = window.controller.store.workspace.save_checkpoint

    def fail_checkpoint(*args) -> None:
        raise ReviewPersistenceError("test storage failure")

    class CloseEvent:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    monkeypatch.setattr(window.controller.store, "save_checkpoint", fail_checkpoint)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Cancel)
    event = CloseEvent()
    window.closeEvent(event)

    assert event.ignored
    assert not window.review_store._closed
    assert window.save_status_label.text() == "! Save failed"
    monkeypatch.setattr(window.controller.store, "save_checkpoint", original_checkpoint)
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_project_sidebar_manages_multiple_image_sets(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )

    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    window.load_plate("1100", SWISSCI_MIDI_3_LENS)

    assert window.image_set_model.rowCount() == 2
    assert window.controller.plate.plate_code == "1100"
    first_index = window.image_set_model.index(0, 0)
    window._image_set_selected(first_index)
    assert window.controller.plate.plate_code == "1070"
    assert "Plate 1070" in first_index.data()
    image_set_id = first_index.data(window.image_set_model.ImageSetIdRole)
    menu = window._build_image_set_context_menu(image_set_id)
    actions = {action.text(): action for action in menu.actions()}
    assert [text for text in actions if text] == [
        "Move Up", "Move Down", "Set format", "Remove from Workspace…",
        "Restore Removed Plate…",
    ]
    assert [action.text() for action in actions["Set format"].menu().actions()] == [
        plate_format.display_name for plate_format in PLATE_FORMATS
    ]
    assert not actions["Restore Removed Plate…"].isEnabled()
    window.plate_filter_input.setText("11")
    assert window.image_set_list.isRowHidden(0)
    assert not window.image_set_list.isRowHidden(1)
    window.plate_filter_input.clear()
    assert not window.image_set_list.isRowHidden(0)
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_project_filter_jumps_to_matching_image_on_another_plate(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    window.load_plate("1100", SWISSCI_MIDI_3_LENS)
    target_key = window.controller.current_image.image_key
    window._handle_image_click(10, 10, Qt.LeftButton)
    first_index = window.image_set_model.index(0, 0)
    window._image_set_selected(first_index)

    filter_index = window.image_filter_input.findData(ImageFilter.WITH_TARGETS)
    window.image_filter_input.setCurrentIndex(filter_index)

    assert window.controller.plate.plate_code == "1100"
    assert window.controller.current_image.image_key == target_key
    assert window.selection_label.text() == "Selection: 1 well · 1 position"
    window.close()
    app.processEvents()


class _PlateRepository:
    def available_batches(self, plate_code):
        return (7, 12)

    def available_profiles(self, plate_code, batch_id):
        return ("profileID_1", "profileID_10") if batch_id == 7 else ()


def test_load_plates_dialog_uses_latest_imaged_batch_for_all_codes() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = LoadPlatesDialog(_PlateRepository(), PLATE_FORMATS, SWISSCI_MRC_2_WELL)

    assert dialog.plate_format is SWISSCI_MRC_2_WELL
    assert not dialog.load_button.isEnabled()
    dialog.plate_codes_input.setText("1070, 1100, 1070")

    assert dialog.load_button.isEnabled()
    assert dialog.sources_table.isHidden()
    assert [
        (source.plate_code, source.batch_id, source.profile)
        for source in dialog.plate_sources()
    ] == [("1070", 7, "profileID_10"), ("1100", 7, "profileID_10")]
    dialog.close()
    app.processEvents()


def test_load_plates_dialog_lets_each_plate_choose_batch_and_profile() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = LoadPlatesDialog(_PlateRepository(), PLATE_FORMATS)
    dialog.plate_codes_input.setText("1070")
    dialog.use_latest_checkbox.setChecked(False)

    assert not dialog.sources_table.isHidden()
    batch_input = dialog.sources_table.cellWidget(0, 1)
    profile_input = dialog.sources_table.cellWidget(0, 2)
    assert batch_input.currentData() == 7
    profile_input.setCurrentIndex(profile_input.findData("profileID_1"))
    assert dialog.plate_sources()[0].profile == "profileID_1"

    batch_input.setCurrentIndex(batch_input.findData(12))
    assert profile_input.currentText() == "No images yet"
    assert not dialog.load_button.isEnabled()
    dialog.close()
    app.processEvents()


def test_restoring_saved_plans_keeps_image_review_tab_and_plan_status(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    window = ViewerWindow(RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path))
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.protein_input.setText("BRD4")
    monkeypatch_warning = QMessageBox.warning
    QMessageBox.warning = lambda *args, **kwargs: QMessageBox.Ok
    try:
        assert window._finalize_plan(editor) is not None
    finally:
        QMessageBox.warning = monkeypatch_warning
    window.main_tabs.setCurrentIndex(window.image_review_tab_index)
    window.close()
    app.processEvents()

    restored = ViewerWindow(RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path))
    assert restored.main_tabs.currentIndex() == restored.image_review_tab_index
    assert restored.plan_list.item(0).text().endswith("· Finalized r1")

    workspace = restored.project_controller.active_project.id
    restored._switch_planning_project(None)
    restored._switch_planning_project(workspace)
    assert restored.plan_list.item(0).text().endswith("· Finalized r1")
    restored.close()
    app.processEvents()


def test_offline_library_folder_keeps_draft_library(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    library_directory = tmp_path / "libraries"
    library_directory.mkdir()
    library_path = library_directory / "library.csv"
    library_path.write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        "Vendor,Lib,1,CMP-1,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "reviews.sqlite3"
    settings = replace(DEFAULT_SETTINGS, fragment_library_directory=library_directory)
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path), settings=settings
    )
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_fragment_plan(None, (crystal,))
    editor = window.plan_stack.currentWidget()
    editor.library_input.setCurrentIndex(1)
    window._persist_draft(editor)
    project_id = editor.project_id
    library_id = editor.library_input.currentData(Qt.UserRole)
    window.close()
    app.processEvents()

    offline = replace(settings, fragment_library_directory=tmp_path / "unmounted")
    reopened = ViewerWindow(
        RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path), settings=offline
    )
    editor = reopened.plan_stack.widget(1)
    assert editor.library_input.currentData(Qt.UserRole) == library_id
    assert "Library unavailable" in editor.library_label.text()
    reopened._persist_draft(editor)
    reopened.close()
    app.processEvents()

    store = SQLiteReviewStore(database_path)
    assert store.planning.load_planning_drafts(project_id)[0].library_id == library_id
    store.close()


def test_experiment_id_preview_reports_database_errors(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()

    def database_locked():
        raise ReviewPersistenceError("could not list experiment ids")

    store.planning.reserved_experiment_ids = database_locked
    editor.protein_input.setText("BRD4")

    assert "could not list experiment ids" in editor.experiment_id_label.text()
    window.close()
    app.processEvents()


def test_canvas_leaves_pan_mode_when_focus_is_lost() -> None:
    from PyQt5.QtGui import QFocusEvent

    from xtalflow.ui.review_widgets import ImageCanvas

    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    QTest.keyPress(canvas, Qt.Key_Space)
    assert canvas._space_pressed

    canvas.focusOutEvent(QFocusEvent(QFocusEvent.FocusOut))

    assert not canvas._space_pressed
    canvas.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_comma_separated_plate_codes_add_multiple_image_sets(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )

    shown_dialogs = []

    def enter_codes(dialog):
        shown_dialogs.append(dialog.windowTitle())
        dialog.plate_format_input.setCurrentIndex(
            dialog.plate_format_input.findData(SWISSCI_MIDI_3_LENS)
        )
        dialog.plate_codes_input.setText("1070, 1100, 1070")
        return dialog.Accepted

    monkeypatch.setattr(LoadPlatesDialog, "exec_", enter_codes)
    window.add_plates_button.click()

    assert [item.plate_code for item in window.image_set_model.image_sets] == [
        "1070",
        "1100",
    ]
    assert window.controller.plate.plate_code == "1100"
    assert shown_dialogs == ["Load Plates"]
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_review_filter_and_well_navigation_use_persisted_review_status(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    first_key = window.controller.current_image.image_key

    window.show_next()
    selected_well = window.well_input.text()
    assert window.controller.session.is_reviewed(first_key)
    window.close()
    app.processEvents()

    restored = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    filter_index = restored.image_filter_input.findData(ImageFilter.UNREVIEWED)
    restored.image_filter_input.setCurrentIndex(filter_index)

    assert restored.controller.session.is_reviewed(first_key)
    assert first_key not in {
        restored.controller.plate.images[index].image_key
        for index in restored.controller.filtered_indices
    }
    restored.well_input.setText(selected_well)
    restored._go_to_entered_well()
    assert restored.well_input.text() == selected_well
    restored.close()
    app.processEvents()


def test_new_project_ui_creates_independent_empty_workspace(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Second Project", True))

    window.create_project_interactively()

    assert window.project_controller.active_project.name == "Second Project"
    assert window.image_set_model.rowCount() == 0
    assert window.project_selector.count() == 2
    window.close()
    app.processEvents()
