import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QPoint, Qt
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
from xtalflow.viewer import FragmentScreeningDialog, ViewerWindow
from xtalflow.viewer import main
from xtalflow.settings import DEFAULT_SETTINGS
from xtalflow.infrastructure.user_preferences import JsonUserPreferencesStore


FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root


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
    assert dialog.table.item(0, 4).text() == "CMP-8"
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
    assert dialog.table.item(0, 4).text() == "CMP-8"
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
    first_well = window.well_input.text()
    assert window.auto_advance_input.prefix() == "Targets/img: "
    assert window.load_button.text() == "Load"
    assert window.target_summary_button.text() == "View Target Summary"
    assert window.previous_button.text() == "◀"
    assert window.next_button.text() == "▶"
    assert window.save_status_label.parent() is window.statusBar()
    assert window.status_message_label.width() == 280
    assert window.status_message_label.parent() is window.statusBar()
    window.show_next()

    assert "Plate 2070" in first_text
    assert "Batch 14122" in first_text
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
    assert window.new_plan_button.text() == "+ New Plan"

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
    assert editor.table.item(0, 4).text() == "CMP-8"
    window.close()
    app.processEvents()


def test_raw_crystal_plan_has_shifter_preview_without_echo(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )

    window.main_tabs.setCurrentIndex(window.planning_tab_index)
    window._add_raw_crystal_plan((crystal,))
    editor = window.plan_stack.currentWidget()
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    window._persist_raw_crystal_draft(editor)

    assert editor.current_plan is not None
    assert editor.shifter_table.rowCount() == 1
    assert editor.preview_tabs.tabText(2) == "WebDB"
    assert editor.webdb_table.rowCount() == 1
    assert editor.webdb_table.item(0, 8).text() != ""
    assert not hasattr(editor, "echo_table")
    drafts = store.load_planning_drafts(window.project_controller.active_project.id)
    assert drafts[-1].plan_type == "raw_crystal"
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

    revision = window._finalize_raw_crystal_plan(editor)
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
    window._upload_raw_labworks(editor)

    events = store.list_webdb_uploads(revision.id)
    assert len(events) == 1
    assert events[0].status == "succeeded"
    assert events[0].account_id == events[0].username
    assert not editor.webdb_upload_button.isEnabled()
    assert unexpected_dialogs == []
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
    assert window.dockWidgetArea(window.target_summary_dock) == Qt.RightDockWidgetArea
    expansion = window._target_summary_window_expansion
    assert expansion > 0
    summary_width = window.width()
    window.target_summary_dock.hide()
    app.processEvents()
    assert window.width() == max(window.minimumWidth(), summary_width - expansion)
    assert window.width() < summary_width
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
    assert "Unconfirmed calibration" in window.target_summary_table.item(0, 6).text()
    assert window.accept_calibration_button.isEnabled()
    window.accept_calibration_button.click()
    assert window.current_calibration.confirmed
    assert window.target_summary_table.item(0, 6).text() == "Ready"
    assert not window.accept_calibration_button.isEnabled()
    QTest.keyClick(window.target_summary_table, Qt.Key_Down)
    assert window.target_summary_table.currentRow() == 1
    assert window.controller.current_image.image_key == second_target_image_key
    window.target_summary_filter.setCurrentIndex(
        window.target_summary_filter.findData("warnings")
    )
    assert window.target_summary_table.rowCount() == 1
    assert "Unconfirmed calibration" in window.target_summary_table.item(0, 6).text()
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
    assert window.review_store.target_count_for_image_set(
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
    assert "Confirmed" in window.calibration_label.text()
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
    assert "Selected: 0 · Targets/img: 2" in window.review_summary_label.text()
    window.show_previous()
    assert "Selected: 1 · Targets/img: 2" in window.review_summary_label.text()

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
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)

    window.auto_advance_input.setValue(4)
    assert window.controller.preferences.auto_advance_target_count == 4
    window.close()
    app.processEvents()

    restored_window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
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
    store.save_image(image.image_key, targets)
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
    window.plate_input.setFocus()
    app.processEvents()

    QTest.keyClick(window.plate_input, Qt.Key_Right)
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
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    window.load_plate("1100", SWISSCI_MIDI_3_LENS)
    window.show()

    window.plate_input.setFocus()
    QTest.keyClick(window.plate_input, Qt.Key_Up)
    app.processEvents()
    assert window.controller.plate.plate_code == "1100"

    window.image_canvas.setFocus(Qt.OtherFocusReason)
    QTest.keyClick(window.image_canvas, Qt.Key_Up)
    app.processEvents()
    assert window.controller.plate.plate_code == "1070"

    QTest.keyClick(window.image_canvas, Qt.Key_Down)
    app.processEvents()
    assert window.controller.plate.plate_code == "1100"

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
    assert window.save_status_label.text() == "Saved"

    window._handle_image_click(100, 120, Qt.LeftButton)
    assert window.save_status_label.text() == "Unsaved changes"

    window.show_next()
    assert window.save_status_label.text() == "Saved"
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
    original_checkpoint = window.controller.store.save_checkpoint

    def fail_checkpoint(*args) -> None:
        raise ReviewPersistenceError("test storage failure")

    monkeypatch.setattr(window.controller.store, "save_checkpoint", fail_checkpoint)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    window.show_next()

    assert window.controller.image_index == 0
    assert window.save_status_label.text() == "Save failed"
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
    original_checkpoint = window.controller.store.save_checkpoint

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
    assert window.save_status_label.text() == "Save failed"
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
    assert menu.actions()[0].text() == "Set format"
    assert [action.text() for action in menu.actions()[0].menu().actions()] == [
        plate_format.display_name for plate_format in PLATE_FORMATS
    ]
    assert not hasattr(window, "set_plate_format_button")
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
    assert "Target images 1" in window.project_progress_label.text()
    window.close()
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

    def choose_first(*args):
        return args[3][0], True

    monkeypatch.setattr(QInputDialog, "getItem", choose_first)
    window.plate_input.setText("1070, 1100, 1070")
    window.plate_format_input.setCurrentIndex(
        window.plate_format_input.findData(SWISSCI_MIDI_3_LENS)
    )
    window.load_entered_plate()

    assert [item.plate_code for item in window.image_set_model.image_sets] == [
        "1070",
        "1100",
    ]
    assert window.controller.plate.plate_code == "1100"
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
