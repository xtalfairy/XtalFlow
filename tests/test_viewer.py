import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

from xtalflow.domain import ReviewSession
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

    window.load_plate("2070")
    first_text = window.navigation_label.text()
    window.show_next()

    assert "Plate 2070" in first_text
    assert "Batch 14122" in first_text
    assert window.controller.image_index == 1
    assert window.navigation_label.text() != first_text
    assert not window.image_canvas.pixmap().isNull()
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_target_selection_survives_image_navigation() -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), auto_advance_target_count=2
    )
    window.load_plate("2070")
    first_image = window.plate.images[0]

    window._handle_image_click(100, 120, Qt.LeftButton)
    assert len(window.controller.session.targets_for(first_image)) == 1
    window.show_next()
    assert "Selected: 0 · Auto-next at: 2" in window.review_summary_label.text()
    window.show_previous()
    assert "Selected: 1 · Auto-next at: 2" in window.review_summary_label.text()

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
    window.load_plate("1070")
    first_image = window.plate.images[0]

    window._handle_image_click(100, 120, Qt.LeftButton)

    assert window.controller.image_index == 1
    assert len(window.controller.session.targets_for(first_image)) == 1
    window.close()
    app.processEvents()

    restored_window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    restored_window.load_plate("1070")
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
    window.load_plate("1070")

    window.auto_advance_input.setValue(4)
    assert window.controller.preferences.auto_advance_target_count == 4
    window.close()
    app.processEvents()

    restored_window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    restored_window.load_plate("1070")
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
    window.load_plate("1070")

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
    window.load_plate("1070")
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
    window.load_plate("1070")
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
def test_save_status_tracks_working_changes_and_checkpoint(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=2,
    )
    window.load_plate("1070")
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
    window.load_plate("1070")
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
    window.load_plate("1070")
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
