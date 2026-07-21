import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QInputDialog, QMessageBox

from xtalflow.domain import (
    PLATE_FORMATS,
    ImageFilter,
    ReviewSession,
    SWISSCI_MIDI_3_LENS,
    SWISSCI_MRC_2_WELL,
)
from xtalflow.application import ReviewPersistenceError
from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
from xtalflow.viewer import ViewerWindow
from xtalflow.viewer import main


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "rmserver"


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
