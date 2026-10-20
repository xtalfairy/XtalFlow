import os
import socket
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from xtalflow.domain.crystal_score import crystal_first_order
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

from xtalflow.domain import (
    PLATE_FORMATS,
    PlanType,
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
from xtalflow.infrastructure import (
    ImageSourceUnavailable,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.application.experiment_workflow import StepState, WorkflowStep
from xtalflow.domain.mxlive import MxLiveLabwork, MxLiveReadError
from xtalflow.ui import icons, theme
from xtalflow.ui.plan_editors import FragmentScreeningEditor
from xtalflow.ui.load_plates_dialog import LoadPlatesDialog
from xtalflow.viewer import ViewerWindow
from xtalflow.viewer import main
from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments
from xtalflow.infrastructure.user_preferences import JsonUserPreferencesStore
from xtalflow.domain.plan_lifecycle import PlanningDraft


FIXTURE_ROOT = DEFAULT_SETTINGS.rmserver_root


def _start(window, plan_type, crystals, name=None):
    """Start an experiment whose selected wells are the given crystals."""
    editor = window.experiments.start_experiment(plan_type, name)
    window.experiments._sync_editor_selection(editor, crystals)
    return editor


def _open_select_wells(window, plan_type=PlanType.RAW_CRYSTAL):
    editor = window.experiments.start_experiment(plan_type)
    window.experiments.go_to_step(WorkflowStep.SELECT_WELLS)
    return editor


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


def test_fragment_editor_previews_and_reassigns_by_plate_well() -> None:
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
    editor = FragmentScreeningEditor(
        FragmentLibrary("Library", (_fragment(8), _fragment(15))), crystals
    )
    editor.conditions_page = editor.conditions_widget()

    assert editor.table.item(0, 0).text() == "1"
    assert editor.table.item(0, 1).text() == "20"
    assert editor.table.item(0, 5).text() == "CMP-8"
    assert [
        editor.preview_tabs.tabText(index) for index in range(editor.preview_tabs.count())
    ] == ["Assignments", "ECHO worksheet", "SHIFTER worksheet"]
    assert editor.count_label.text() == "2 wells · 2 fragments"
    assert editor.assignment_table.item(0, 0).text() == "20 A01a"
    assert editor.assignment_table.item(0, 2).text() == "CMP-8"
    assert editor.echo_table.rowCount() == 2
    assert editor.shifter_table.rowCount() == 2
    assert editor.table.item(0, 8).text() == "25.0 nL × 1"
    editor.order_input.setCurrentIndex(
        editor.order_input.findData(AssignmentOrder.PLATE_WELL)
    )
    assert not editor.reassignment_panel.isHidden()
    assert "3 A01a: CMP-15 → CMP-8" in editor.reassignment_label.text()
    assert editor.table.item(0, 1).text() == "20"
    assert editor.order_input.currentData() is AssignmentOrder.SELECTION
    editor.apply_reassignment_button.click()
    assert editor.reassignment_panel.isHidden()
    assert editor.order_input.currentData() is AssignmentOrder.PLATE_WELL
    assert editor.table.item(0, 1).text() == "3"
    assert editor.table.item(0, 5).text() == "CMP-8"
    assert editor.all_rows_radio.isChecked() and not editor.rows_input.isEnabled()
    editor.choose_rows_radio.setChecked(True)
    editor.rows_input.setText("2")
    # One fragment for two crystals builds the smaller experiment: the first
    # well in assignment order is soaked and the other is left out of it.
    assert editor.current_plan is not None
    assert len(editor.current_plan.assignments) == 1
    assert [
        well.well_address for well in editor.current_plan.unassigned_wells
    ] == [editor.assignment_table.item(1, 0).text().split()[-1]]
    assert editor.count_label.text() == (
        "△ 2 wells · 1 fragment · 1 crystal with no fragment, left out of this "
        "experiment"
    )
    assert editor.rows_error_label.isHidden()
    assert editor.assignment_table.item(1, 2).text() == "—"
    assert editor.assignment_table.item(1, 4).text() == (
        "△ no fragment · not soaked or harvested"
    )
    # And it is in neither worksheet.
    assert editor.echo_table.rowCount() == 1
    assert editor.shifter_table.rowCount() == 1
    editor.close()
    app.processEvents()


def test_conditions_explain_a_volume_that_cannot_be_split_equally() -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime.now(timezone.utc)
    crystal = SelectedCrystal(
        "image", "20", "A01a",
        tuple(
            CrystalTarget(f"t{number}", Decimal(0), Decimal(0), now + timedelta(seconds=number))
            for number in range(3)
        ),
        SWISSCI_MIDI_3_LENS.id,
    )
    editor = FragmentScreeningEditor(FragmentLibrary("Library", (_fragment(1),)), (crystal,))
    editor.conditions_page = editor.conditions_widget()
    editor.volume_input.setValue(25)

    assert editor.current_plan is None
    assert editor.volume_error_label.text() == (
        "Cannot be split equally in 1 well. Use 22.5 nL."
    )
    assert editor.assignment_table.item(0, 4).text() == (
        "△ 25.0 nL cannot be split equally into 3"
    )
    editor.volume_input.setValue(30)
    assert editor.current_plan is not None
    assert editor.volume_error_label.isHidden()

    # Finishing the edit asks once, and never changes the value unasked.
    asked = []
    editor.volume_input.setValue(25)
    editor._ask_to_use_volume = lambda suggestion, text: (
        asked.append((suggestion, text)) or False
    )
    editor.check_volume_splits()

    assert editor.volume_input.value() == 25
    suggestion, text = asked[0]
    assert str(suggestion) == "22.5"
    assert "25 nL cannot be split into 2.5 nL steps for 1 well: A01a (3 positions)" in text
    assert "22.5 nL divides evenly for every selected well" in text
    assert "largest volume at or below" in text

    # Saying yes is what applies it.
    editor._ask_to_use_volume = lambda suggestion, text: True
    editor.check_volume_splits()

    assert editor.volume_input.value() == 22.5
    assert editor.current_plan is not None
    editor.volume_input.setValue(30)

    assert editor.assignment_table.item(0, 3).text() == "10.0 nL × 3"
    assert editor.table.item(0, 8).text() == "10.0 nL × 3"
    assert "30.0 nL per well" in editor.summary_label.text()
    editor.close()
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
    # The count that moves to the next well is the denominator of a reading of
    # this well, beside the arrows, rather than a setting of its own.
    assert window.well_progress_label.text() == "This well · 0 of"
    assert window.auto_advance_input.value() == 2
    assert window.target_summary_button.text() == "Selected wells"
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


def _share_with_one_plate(root: Path, plate_code: str = "1070") -> Path:
    """The RockMaker folder layout with a single readable image."""
    shard = RockMakerImageRepository.shard_for(plate_code)
    well = root / shard / f"plateID_{plate_code}" / "batchID_5947" / "wellNum_1"
    profile = well / "profileID_1"
    profile.mkdir(parents=True, exist_ok=True)
    image = QImage(16, 16, QImage.Format_RGB32)
    image.fill(0xFF808080)
    assert image.save(str(profile / "d1_r1_ef.jpg"), "JPEG")
    return root


def _answers(*replies):
    """A dialog stub that records what it was shown and never loops forever."""
    shown = []

    def answer(*args, **kwargs):
        shown.append(args[2])
        return replies[min(len(shown), len(replies)) - 1]

    return shown, answer


def test_load_plates_offers_to_look_again_while_the_share_is_down(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")

    class FlakyRepository(RockMakerImageRepository):
        """Unreachable until the share comes back on the third read."""

        reads = 0

        def load_plate_batch(self, *args, **kwargs):
            FlakyRepository.reads += 1
            if FlakyRepository.reads < 3:
                raise ImageSourceUnavailable(
                    Path("/smbmount/rmserver"), "the network share is not mounted"
                )
            return super().load_plate_batch(*args, **kwargs)

    share = _share_with_one_plate(tmp_path / "share")
    window = ViewerWindow(FlakyRepository(share), store)
    # Two refusals, then Retry succeeds; a third answer would end the loop.
    asked, answer = _answers(QMessageBox.Retry, QMessageBox.Retry, QMessageBox.Cancel)
    monkeypatch.setattr(QMessageBox, "warning", answer)
    window.load_plates_form.plate_codes_input.setText("1070")

    window.load_plates_form.load_button.click()

    assert FlakyRepository.reads == 3
    assert len(asked) == 2
    assert "/smbmount/rmserver" in asked[0]
    assert "XtalFlow cannot connect the share itself" in asked[0]
    # The second time, the administrator is the one who can fix it.
    assert "Ask the administrator" in asked[1]
    assert socket.gethostname() in asked[1]
    assert window.image_set_model.image_sets[0].plate_code == "1070"
    window.close()
    app.processEvents()


def test_cancelling_the_share_dialog_loads_nothing(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")

    class OfflineRepository(RockMakerImageRepository):
        reads = 0

        def load_plate_batch(self, *args, **kwargs):
            OfflineRepository.reads += 1
            raise ImageSourceUnavailable(Path("/smbmount/rmserver"), "Host is down")

    share = _share_with_one_plate(tmp_path / "share")
    window = ViewerWindow(OfflineRepository(share), store)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Cancel)
    window.load_plates_form.plate_codes_input.setText("1070")

    window.load_plates_form.load_button.click()

    assert OfflineRepository.reads == 1
    assert not window.image_set_model.image_sets
    window.close()
    app.processEvents()


def test_a_plate_that_is_not_on_the_share_is_not_a_share_problem(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    share = _share_with_one_plate(tmp_path / "share")
    window = ViewerWindow(
        RockMakerImageRepository(share), SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    )
    asked, answer = _answers(QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", answer)
    window.load_plates_form.plate_codes_input.setText("9999")

    window.load_plates_form.load_button.click()

    assert not asked  # no retry dialog: looking again would find nothing
    assert window.load_plates_form.error_label.text().startswith("No plate 9999")
    assert not window.image_set_model.image_sets
    window.close()
    app.processEvents()


def test_an_experiment_says_when_its_wells_cannot_be_read(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path / "share"), store)
    editor = window.experiments.start_experiment(PlanType.RAW_CRYSTAL)
    editor.protein_input.setText("BRD4")

    def share_is_down():
        raise ImageSourceUnavailable(
            Path("/smbmount/rmserver"), "the network share is not mounted"
        )

    monkeypatch.setattr(
        window.project_controller, "project_target_summaries", share_is_down
    )
    window.experiments._sync_editor_selection(editor)

    status = editor.experiment_status.status_of(WorkflowStep.SELECT_WELLS)
    assert status.state is StepState.ATTENTION
    assert "the network share is not mounted" in status.message
    assert not editor.experiment_status.ready_to_finalize
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


def test_main_window_starts_on_home_and_guides_an_experiment(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(RockMakerImageRepository(tmp_path))
    page = window.experiment_page

    assert window.pages.currentWidget() is window.home_page
    assert set(window.workspace_sidebar.start_buttons) == {
        "fragment_screening", "raw_crystal", "solvent_test", "cryo_test"
    }
    assert not window.home_page.recent_empty_label.isHidden()
    assert window.workspace_sidebar.new_workspace_button.toolTip() == "New workspace"

    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    editor = window.experiments.current_editor

    assert window.pages.currentWidget() is page
    assert window.experiments._current_step is WorkflowStep.SETUP
    assert list(page.stepper.buttons) == [
        WorkflowStep.SETUP, WorkflowStep.SELECT_WELLS,
        WorkflowStep.REVIEW, WorkflowStep.WORKSHEETS,
    ]
    assert page.footer_status_label.text() == "Enter a protein name to continue."
    assert not page.primary_button.isEnabled()
    assert not page.back_button.isEnabled()
    editor.protein_input.setText("BRD4")
    assert page.primary_button.text() == "Select wells"
    assert editor.setup_step.experiment_id_label.text().startswith(
        "ID when finalized: RawCrystal-"
    )
    editor.protein_input.setText("BRD4 !!")
    assert editor.setup_step.protein_error_label.text().startswith("Protein name may contain")
    assert not page.primary_button.isEnabled()
    editor.protein_input.setText("BRD4")
    # Its line stays, empty, so fixing the name moves nothing.
    assert editor.setup_step.protein_error_label.text() == ""
    page.primary_button.click()

    assert window.experiments._current_step is WorkflowStep.SELECT_WELLS
    assert page.footer_status_label.text() == "Select at least one well to continue."
    assert not page.primary_button.isEnabled()
    assert window.image_stack.currentWidget() is window.empty_state
    page.stepper.buttons[WorkflowStep.REVIEW].click()
    assert page.footer_status_label.text() == (
        "Complete the earlier steps first. · Select wells: Select at least one well "
        "to continue."
    )
    page.home_button.click()

    assert window.pages.currentWidget() is window.home_page
    assert window.home_page.recent_table.item(0, 2).text() == "Draft · Review"
    window.close()
    app.processEvents()


def test_conditions_list_libraries_from_designated_directory(
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

    editor = _start(window, PlanType.FRAGMENT_SCREENING, (crystal,))
    editor.library_input.setCurrentIndex(1)

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
    editor = _start(window, PlanType.FRAGMENT_SCREENING, (crystal,))
    editor.library_input.setCurrentIndex(1)
    assert editor.rows_input.text() == "1-3"
    editor.rows_input.setText("2-3")

    window.experiments._refresh_fragment_library_choices()

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
    plan_ids = {}
    for protein in ("DAMAGED", "HEALTHY"):
        editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
        editor.protein_input.setText(protein)
        window.experiments._persist_draft(editor)
        plan_ids[protein] = editor.plan_id
    window.close()
    app.processEvents()
    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE planning_draft SET plan_type = 'retired' WHERE plan_id = ?",
        (plan_ids["DAMAGED"],),
    )
    connection.commit()
    connection.close()

    restored = ViewerWindow(RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path))
    recent = restored.experiments._recent_experiments()

    assert [item.plan_id for item in recent] == [plan_ids["HEALTHY"]]
    editor = restored.experiments.resume_experiment(recent[0].workspace_id, recent[0].plan_id)
    assert editor.protein_input.text() == "HEALTHY"
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

    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,), name="Harvest A")
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    window.experiments._persist_draft(editor)

    assert editor.current_plan is not None
    assert editor.shifter_table.rowCount() == 1
    assert editor.summary_table.horizontalHeaderItem(3).text() == (
        "Position"
    )
    assert editor.summary_table.item(0, 3).text() == "1"
    assert editor.summary_table.item(1, 3).text() == "2"
    assert editor.summary_table.item(0, 3).data(Qt.UserRole) == "target"
    assert editor.summary_table.item(0, 4).text() == "Original"
    assert editor.preview_tabs.count() == 2
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

    reused_editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
    assert reused_editor.summary_table.item(0, 4).text() == "Reused"
    assert "Harvest A" in (
        reused_editor.summary_table.item(0, 4).toolTip()
    )
    assert editor.summary_table.item(0, 4).text() == "Original"
    window.close()
    app.processEvents()


def test_draft_experiment_can_be_deleted_from_recent_work(
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
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
    plan_id = editor.plan_id
    workspace_id = editor.project_id
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )

    window.experiments.delete_experiment(workspace_id, plan_id)
    app.processEvents()

    assert window.pages.currentWidget() is window.home_page
    assert window.experiments.current_editor is None
    assert window.home_page.recent_table.rowCount() == 0
    assert all(
        draft.id != plan_id
        for draft in store.planning.load_planning_drafts(workspace_id)
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

    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
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


def test_experiment_selection_follows_its_positions_after_finalizing(
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
    editor = _start(window, PlanType.RAW_CRYSTAL, (original,))
    editor.protein_input.setText("BRD4")
    assert window.experiments._finalize_plan(editor) is not None
    assert editor.experiment_status.status_of(WorkflowStep.REVIEW).message == "Finalized r1"

    window.experiments._sync_editor_selection(editor, (replacement,))

    assert editor.selection.wells[0].image_key == "replacement-image"
    owned = store.planning.load_experiment_project(editor.plan_id)
    assert owned.crystal_selection.wells[0].image_key == "replacement-image"
    assert editor.experiment_status.status_of(WorkflowStep.REVIEW).message.startswith(
        "Changes after r1"
    )
    assert editor.experiment_status.ready_to_finalize
    window.close()
    app.processEvents()


def test_resuming_an_experiment_returns_to_its_saved_step(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    now = datetime.now(timezone.utc)
    workspace_id = window.project_controller.active_project.id
    store.planning.save_planning_draft(
        PlanningDraft(
            "saved-draft", workspace_id, "raw_crystal", "Saved Draft", None,
            "", "BRD4", "0", "selection", now, now, None, "review",
        )
    )

    window.experiments.show_home()
    assert window.home_page.recent_table.item(0, 2).text() == "Draft · Review"
    window.home_page.recent_table.selectRow(0)
    window.home_page.resume_button.click()
    editor = window.experiments.current_editor

    assert editor.plan_id == "saved-draft"
    assert window.experiments._current_step is WorkflowStep.REVIEW
    assert window.experiment_page.title_label.text() == "Saved Draft"
    assert editor.protein_input.text() == "BRD4"
    assert not window.experiment_page.primary_button.isEnabled()
    assert project_id_of(window) == workspace_id
    window.close()
    app.processEvents()


def project_id_of(window) -> str:
    return window.project_controller.active_project.id


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
    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", _FakeMxLiveReader)
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
    )
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
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

    revision = window.experiments._finalize_plan(editor)
    assert revision is not None, unexpected_dialogs
    assert editor.webdb_upload_button.isEnabled()

    class FakeWriter:
        def __init__(self, *args, **kwargs):
            pass

        def upload_labworks(self, records):
            assert records[0]["plate_imgpath"] == "/rmserver/image.jpg"
            return {"created": len(records)}

    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", FakeWriter)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)
    window.experiments._upload_plan_labworks(editor)

    events = store.audit.list_webdb_uploads(revision.id)
    assert len(events) == 1
    assert events[0].status == "succeeded"
    assert events[0].account_id == events[0].username
    assert not editor.webdb_upload_button.isEnabled()
    assert unexpected_dialogs == []

    # The list of experiments says how far this one came, and when it started.
    # The worksheets were never saved here, and the list keeps saying so.
    draft = store.planning.load_recent_drafts()[0]
    window.experiments.show_home()
    table = window.home_page.recent_table
    assert table.item(0, 2).text() == (
        "Sent r1 · no worksheets"
    )
    assert table.item(0, 4).text() == (
        draft.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    )
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
    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", _FakeMxLiveReader)
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
    )
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    dialogs = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            lambda *args, _name=name, **kwargs: dialogs.append((_name, args[2])),
        )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_plan(editor)
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
    from xtalflow.domain.mxlive import MxLiveUncertainWriteError

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

    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", TimeoutWriter)
    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", FakeReader)

    window.experiments._upload_plan_labworks(editor)

    assert [event.status for event in store.audit.list_webdb_uploads(revision.id)] == [
        "unknown"
    ]
    assert editor.webdb_upload_button.text() == "Verify on MxLive…"
    assert editor.webdb_upload_button.isEnabled()

    window.experiments._upload_plan_labworks(editor)
    assert len(TimeoutWriter.posted) == 1
    assert [event.status for event in store.audit.list_webdb_uploads(revision.id)] == [
        "failed"
    ]
    assert editor.webdb_upload_button.text() == "Upload to MxLive…"
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

    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", Writer)
    monkeypatch.setattr(store.audit, "record_webdb_upload", audit_unavailable)

    window.experiments._upload_plan_labworks(editor)

    assert Writer.posted == []
    assert dialogs[-1] == (
        "critical", "The upload audit record could not be saved:\ndatabase is locked"
    )
    window.close()
    app.processEvents()


def test_an_experiment_already_on_mxlive_is_never_uploaded_again(
    tmp_path: Path, monkeypatch
) -> None:
    """MxLive overwrites a labwork it has, erasing the beamline's own fields.

    The local audit cannot see an upload made from another computer, so the
    server is asked before anything is sent.
    """
    app = QApplication.instance() or QApplication([])
    window, _store, editor, revision, dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )

    class Holds(_FakeMxLiveReader):
        rows = ()

        def labworks(self, experiment_id):
            return Holds.rows

    class Writer(_RecordingWriter):
        posted = []

    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", Holds
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", Writer
    )

    # Nothing was uploaded from this computer, so the local audit says the
    # experiment is free. MxLive says otherwise, and MxLive is right.
    Holds.rows = (
        MxLiveLabwork.from_mapping({"expri_id": revision.experiment_id}),
    ) * 3
    window.experiments._upload_plan_labworks(editor)

    assert Writer.posted == []
    assert "already holds 3 records" in dialogs[-1][1]
    assert "erase the harvest time" in dialogs[-1][1]

    # When MxLive cannot be asked at all, nothing is sent either: an upload
    # that overwrites cannot be taken back, a later attempt costs nothing.
    def unreachable(self, experiment_id):
        raise MxLiveReadError("MxLive request failed (HTTP 503)")

    monkeypatch.setattr(Holds, "labworks", unreachable)
    window.experiments._upload_plan_labworks(editor)
    assert Writer.posted == []
    assert "could not be asked" in dialogs[-1][1]

    # MxLive matches the identifier as a substring, so a longer one that merely
    # contains ours is not this experiment and must not block it.
    monkeypatch.setattr(
        Holds, "labworks",
        lambda self, experiment_id: (
            MxLiveLabwork.from_mapping({"expri_id": f"{experiment_id}9"}),
        ),
    )
    window.experiments._upload_plan_labworks(editor)
    assert len(Writer.posted) == 1
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

    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", Writer)
    window.experiments._upload_plan_labworks(editor)
    editor.protein_input.setText("BRD4 variant")
    second = window.experiments._finalize_plan(editor)

    assert second is not None and second.revision == 2
    assert not editor.webdb_upload_button.isEnabled()
    assert "Uploaded (earlier revision)" in editor.webdb_status_label.text()
    window.experiments._upload_plan_labworks(editor)
    assert len(Writer.posted) == 1
    assert dialogs[-1][0] == "information"
    # The experiment page knows; so must the list, because what MxLive holds
    # is r1 and a labwork cannot be replaced.
    window.experiments.show_home()
    stage = window.home_page.recent_table.item(0, 2)
    assert stage.text() == "Finalized r2 · MxLive has r1"
    assert "Sent r1 to MxLive" in _tooltip(stage)
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

    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", OfflineReader)
    crystal = SelectedCrystal(
        "image-key", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    questions = []

    def answer(response):
        def question(*args, **kwargs):
            questions.append(args[1])
            return response
        return question

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.No))
    assert window.experiments._finalize_plan(editor) is None
    assert store.planning.list_plan_revisions(editor.plan_id) == ()

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.Yes))
    assert window.experiments._finalize_plan(editor) is not None
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
    editor = _start(window, PlanType.FRAGMENT_SCREENING, (crystal,))
    editor.library_input.setCurrentIndex(1)
    editor.protein_input.setText("BRD4")
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
    page = window.experiment_page
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    window.experiments.go_to_step(WorkflowStep.REVIEW)
    assert page.primary_button.text() == "Finalize r1"
    page.primary_button.click()

    assert window.experiments._current_step is WorkflowStep.WORKSHEETS
    table = editor.worksheets_step.instrument_table
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        "ECHO 650", "SHIFTER 1", "SHIFTER 2"
    ]
    assert table.item(0, 2).text() == "1"
    assert page.primary_button.text() == "Save worksheets"
    page.primary_button.click()

    revision = editor.last_revision
    exports = store.audit.list_worksheet_exports(revision.id)
    result = editor.worksheets_step.result_label.text()
    assert result.startswith("✓ Saved r1")
    assert "nothing has run on the instruments yet" in result
    assert table.item(2, 3).text() == exports[0].path_for("shifter2")
    assert editor.lifecycle_label.text() == "Finalized r1"
    assert page.primary_button.text() == "Done"
    assert all(
        status.state is StepState.COMPLETE for status in editor.experiment_status.steps
    )
    editor.protein_input.setText("BRD4-B")
    window.experiments._persist_draft(editor)
    assert editor.lifecycle_label.text() == "Draft changes after r1"
    assert page.primary_button.text() == "Save worksheets"
    assert not page.primary_button.isEnabled()
    assert [event.status for event in exports] == ["succeeded"]
    assert Path(exports[0].path_for("echo650")).is_file()
    assert Path(exports[0].path_for("shifter2")).is_file()
    window.experiments.show_home()
    stage = window.home_page.recent_table.item(0, 2)
    assert stage.text() == "Worksheets r1"
    # Hovering the stage, or clicking it, tells the whole story.
    detail = _tooltip(stage)
    assert "in the folders of all 3 instruments that read them" in detail
    assert "Started " in detail and "Finalized r1 " in detail
    assert "ECHO 650" in detail and "SHIFTER 1" in detail
    assert not stage.icon().isNull()

    # A later attempt that fails does not take the files off the instruments,
    # so the list must not go back to saying nothing was saved.
    from xtalflow.domain.plan_lifecycle import WorksheetExportEvent

    store.audit.record_worksheet_export(WorksheetExportEvent(
        "failed-retry", revision.id, "tester", datetime.now(timezone.utc),
        "failed", (), "the share went away",
    ))
    window.experiments.show_home()
    assert window.home_page.recent_table.item(0, 2).text() == "Worksheets r1"
    window.close()
    app.processEvents()


def test_a_condition_test_takes_a_fresh_plate_only_when_the_record_itself_is_away(
    tmp_path: Path, monkeypatch
) -> None:
    """Without the record nobody can say which wells are free, so start where all are.

    This used to refuse the save outright, which only moved the problem: the
    plan is already finalized and the plate is already on the bench. And it
    used to ask after the ECHO, which is only where the record happened to
    live: with the record on this machine the ECHO can be away all week and
    source wells are still booked.
    """
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        instruments=standard_instruments(
            tmp_path / "missing-echo", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=tmp_path / "source-plates",
        create_missing_instrument_roots=False,
    )
    window, _store, _editor = _fragment_plan_window(tmp_path, settings)
    (tmp_path / "shifter1").mkdir(exist_ok=True)
    (tmp_path / "shifter2").mkdir(exist_ok=True)
    said = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: said.append(args[2])
    )

    from xtalflow.infrastructure.source_plate_record import record_directory
    from xtalflow.infrastructure.worksheet_exporter import pending_root

    # The ECHO is away and the record is not: nothing to assume, nothing to say.
    (tmp_path / "source-plates").mkdir()
    assert not window.experiments._source_record_is_away()
    (tmp_path / "source-plates").rmdir()
    assert window.experiments._source_record_is_away()
    # Nothing is said in a box any more: there is no question to answer.
    assert not said
    # The service is given somewhere of our own to keep the entries.
    service = window.experiments._worksheet_export_service()
    assert service.held_record is not None
    assert service.held_record.directory == record_directory(pending_root(settings))
    window.close()
    app.processEvents()


def test_worksheets_go_to_the_instruments_that_are_up(
    tmp_path: Path, monkeypatch
) -> None:
    """One SHIFTER is off; the experiment still reaches the other."""
    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    (tmp_path / "shifter1").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "missing-shifter2"
        ),
        create_missing_instrument_roots=False,
    )
    settings = replace(settings, pending_worksheet_directory=tmp_path / "pending")
    window, store, editor = _fragment_plan_window(tmp_path, settings)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: asked.append(args[2])
    )
    assert window.experiments._finalize_plan(editor) is not None
    window.experiments.go_to_step(WorkflowStep.WORKSHEETS)
    step = editor.worksheets_step

    # Before anybody presses save, the page already says which one is away
    # and where its worksheet will wait.
    before = {
        step.instrument_table.item(row, 0).text():
            step.instrument_table.item(row, 3).text()
        for row in range(step.instrument_table.rowCount())
    }
    assert before["SHIFTER 2"].startswith("not connected · will wait in ")
    assert str(tmp_path / "pending" / "shifter2") in before["SHIFTER 2"]
    assert before["SHIFTER 1"].startswith(str(tmp_path / "shifter1"))

    window.experiment_page.primary_button.click()

    # One save, no question: an instrument that is away is the ordinary state.
    assert asked == []
    exports = store.audit.list_worksheet_exports(editor.last_revision.id)
    assert [event.status for event in exports] == ["succeeded"]
    assert [output.instrument for output in exports[-1].outputs] == [
        "echo650", "shifter1", "shifter2"
    ]
    assert Path(exports[-1].path_for("shifter1")).is_file()
    # SHIFTER 2's worksheet waits for it; its empty mountpoint stays empty,
    # because a file left there would vanish when the share came back.
    waiting = Path(exports[-1].path_for("shifter2"))
    assert waiting.is_file()
    assert waiting.parent == tmp_path / "pending" / "shifter2"
    assert not (tmp_path / "missing-shifter2").exists()
    # The step keeps saying so, and says where the file is, because somebody
    # has to carry it across before anything runs on SHIFTER 2.
    result = step.result_label.text()
    assert "1 file for SHIFTER 2 is not on the instrument yet" in result
    assert "copy from the folder below" in result
    table = step.instrument_table
    rows = {
        table.item(row, 0).text(): table.item(row, 3).text()
        for row in range(table.rowCount())
    }
    assert rows["SHIFTER 2"] == str(waiting)
    # The step is done: the experiment can run on the instruments that have the
    # files. What is missing is said on the page, not held against the step.
    assert (
        editor.experiment_status.status_of(WorkflowStep.WORKSHEETS).state
        is StepState.COMPLETE
    )
    # And the list of experiments tells a partial delivery from a full one.
    window.experiments.show_home()
    row = window.home_page.recent_table.item(0, 2)
    assert row.text() == "Worksheets r1 · not on SHIFTER 2"
    assert "No worksheet reached SHIFTER 2." in _tooltip(row)
    assert "ECHO 650, SHIFTER 1" in _tooltip(row)
    window.close()
    app.processEvents()


def test_with_no_instrument_reachable_every_worksheet_waits_in_one_place(
    tmp_path: Path, monkeypatch
) -> None:
    """Nothing is connected: the save still happens, in one go and unasked."""
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(
            tmp_path / "missing-echo",
            tmp_path / "missing-shifter1",
            tmp_path / "missing-shifter2",
        ),
        create_missing_instrument_roots=False,
    )
    window, store, editor = _fragment_plan_window(tmp_path, settings)
    asked = []
    for name in ("warning", "question"):
        monkeypatch.setattr(
            QMessageBox, name, lambda *args, **kwargs: asked.append(args[2])
        )
    assert window.experiments._finalize_plan(editor) is not None
    window.experiments.go_to_step(WorkflowStep.WORKSHEETS)
    step = editor.worksheets_step

    window.experiment_page.primary_button.click()

    assert asked == []
    exports = store.audit.list_worksheet_exports(editor.last_revision.id)
    assert [event.status for event in exports] == ["succeeded"]
    for instrument in ("echo650", "shifter1", "shifter2"):
        waiting = Path(exports[-1].path_for(instrument))
        assert waiting.parent == tmp_path / "pending" / instrument
        assert waiting.is_file()
    # The empty mountpoints stay empty: a file there would vanish when the
    # share came back over it.
    assert not (tmp_path / "missing-echo").exists()

    result = step.result_label.text()
    assert "ECHO 650, SHIFTER 1 and SHIFTER 2" in result
    assert "not on the instruments yet" in result
    assert "copy from the folder below" in result
    assert not step.copy_paths_button.isHidden()
    step.copy_paths_button.click()
    assert QApplication.clipboard().text().splitlines()[0].startswith(
        str(tmp_path / "pending")
    )
    # Nothing reached an instrument, so the step keeps asking for attention.
    assert (
        editor.experiment_status.status_of(WorkflowStep.WORKSHEETS).state
        is StepState.ATTENTION
    )

    # Choosing a folder by hand still works for anyone who wants one.
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path / "chosen"),
    )
    step.choose_location_button.click()

    exports = store.audit.list_worksheet_exports(editor.last_revision.id)
    assert exports[-1].path_for("echo650").startswith(
        str(tmp_path / "chosen" / "echo650")
    )
    assert "Saved r1 to the folder you chose" in step.result_label.text()
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
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))
    editor.set_crystals((crystal,))
    editor.protein_input.setText("BRD4")
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Ok)

    first = window.experiments._finalize_plan(editor)
    editor.protein_input.setText("BRD4 variant")
    second = window.experiments._finalize_plan(editor)

    assert first is not None and second is not None
    assert second.revision == 2
    assert second.experiment_id == first.experiment_id
    assert "fixed for this experiment" in editor.experiment_id_label.text()
    restored = store.planning.load_planning_drafts(editor.project_id)
    assert restored[-1].experiment_id == first.experiment_id
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_raw_crystal_experiment_runs_from_setup_to_saved_worksheets(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), store, settings=settings,
        preferences_store=JsonUserPreferencesStore(tmp_path / "preferences.json"),
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page

    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    first = window.experiments.current_editor
    first.protein_input.setText("BRD4")
    page.primary_button.click()
    assert window.image_stack.currentWidget() is window.empty_state
    window.load_plates_form.plate_format_input.setCurrentIndex(
        window.load_plates_form.plate_format_input.findData(SWISSCI_MRC_2_WELL)
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._accept_current_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window.experiments._sync_current_selection()

    assert page.footer_status_label.text() == "✓ 1 well · 1 position"
    assert page.primary_button.text() == "Use 1 well"
    page.primary_button.click()
    assert window.experiments._current_step is WorkflowStep.REVIEW
    assert first.summary_table.rowCount() == 1
    page.primary_button.click()
    assert window.experiments._current_step is WorkflowStep.WORKSHEETS
    assert first.worksheets_step.instrument_table.rowCount() == 2
    page.primary_button.click()

    exports = store.audit.list_worksheet_exports(first.last_revision.id)
    assert [event.status for event in exports] == ["succeeded"]
    assert Path(exports[0].path_for("shifter1")).is_file()
    assert page.primary_button.text() == "Done"
    page.primary_button.click()
    assert window.home_page.recent_table.item(0, 2).text() == "Worksheets r1"

    second = window.experiments.start_experiment(PlanType.RAW_CRYSTAL)
    window.experiments.go_to_step(WorkflowStep.SELECT_WELLS)
    assert window.controller.plate.plate_code == "2070"
    assert window.controller.current_targets == ()
    assert f"also used in {first.plan_name}" in window.review_summary_label.text()
    assert second.selected_well_count == 0

    window.experiments.resume_experiment(first.project_id, first.plan_id)
    assert window.experiments._current_step is WorkflowStep.WORKSHEETS
    assert first.lifecycle_label.text() == "Finalized r1"
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
    # The automatic jump holds at an unconfirmed boundary, so move by hand:
    # this test wants the first well left unconfirmed.
    window.show_next()
    second_target_image_key = window.controller.current_image.image_key
    window._handle_image_click(610, 510, Qt.LeftButton)
    # One row per well, however many positions it holds.
    assert window.target_summary_table.rowCount() == 2
    assert window.target_summary_table.item(0, 1).text() == "A01a"
    assert window.target_summary_table.item(0, 3).text() == "1"
    assert window.target_summary_table.item(0, 4).text() == ""
    assert "Unconfirmed" in window.target_summary_table.item(0, 2).text()
    window.experiments._review_target_warnings()
    assert window.target_summary_filter.currentData() == "warnings"
    assert window.target_summary_dock.isVisible()
    assert window.target_summary_table.rowCount() == 2
    window.target_summary_filter.setCurrentIndex(
        window.target_summary_filter.findData("all")
    )

    window.target_summary_table.setCurrentCell(0, 0)
    assert window.controller.current_image.image_key == target_image_key
    assert window.target_summary_table.hasFocus()
    assert "Unconfirmed" in window.target_summary_table.item(0, 5).text()
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
    assert "Unconfirmed" in window.target_summary_table.item(0, 5).text()
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
def test_confirming_a_boundary_stops_the_step_asking_for_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The step kept a count of its own, so a confirmed well stayed a warning."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page
    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    editor = window.experiments.current_editor
    editor.protein_input.setText("BRD4")
    page.primary_button.click()
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window.experiments._sync_current_selection()
    # The sync a placed position schedules has already run; only confirming
    # the boundary is left to bring the step up to date.
    window._selection_sync_timer.stop()
    assert editor.wells_needing_attention == 1
    assert page.primary_button.text() == "Check 1 well"

    window._accept_current_calibration()
    QTest.qWait(600)
    app.processEvents()

    assert editor.wells_needing_attention == 0
    assert page.footer_status_label.text() == "\u2713 1 well \u00b7 1 position"
    assert page.primary_button.text() == "Use 1 well"
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_a_warning_with_nothing_left_to_check_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    """An empty warning list cannot answer for the step; the step is corrected."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    editor = window.experiments.current_editor
    editor.protein_input.setText("BRD4")
    window.experiment_page.primary_button.click()
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._accept_current_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window.experiments._sync_current_selection()
    assert editor.wells_needing_attention == 0

    editor.wells_needing_attention = 1
    window.experiments._review_target_warnings()

    assert window.target_summary_table.rowCount() == 0
    assert editor.wells_needing_attention == 0
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
    assert "Well boundary confirmed · auto, outline fit" in window.calibration_label.text()
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
    _open_select_wells(window)
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window._handle_image_click(640, 520, Qt.LeftButton)
    window.show()
    window.target_summary_button.click()
    app.processEvents()

    assert window.target_summary_dock.windowTitle() == "Selected wells"
    # Two positions in one well are one row, and the counts say both numbers.
    assert window.target_summary_filter.itemText(0) == "All wells (1)"
    assert window.target_summary_filter.itemText(1) == "Needs attention (1)"
    assert "1 well · 2 positions" in window.target_summary_status_label.text()
    assert "1 need attention" in window.target_summary_status_label.text()
    assert window.target_summary_table.rowCount() == 1
    assert window.target_summary_table.item(0, 3).text() == "2"
    assert not window.remove_targets_button.isEnabled()

    window.target_summary_table.setCurrentCell(0, 0)
    positions = window.target_summary_table.item(0, 0).data(Qt.UserRole + 2)
    assert len(positions) == 2
    assert window.image_canvas._highlighted_target_id in positions
    window.target_summary_table.selectAll()
    assert window.remove_targets_button.text() == "Delete well positions (1)"

    window.experiments.go_to_step(WorkflowStep.REVIEW)
    app.processEvents()
    assert not window.target_summary_dock.isVisible()
    window.experiments.go_to_step(WorkflowStep.SELECT_WELLS)
    app.processEvents()
    assert window.target_summary_dock.isVisible()
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_review_hint_is_shown_once_per_user_and_escape_cancels_calibration(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    preferences = JsonUserPreferencesStore(tmp_path / "preferences.json")
    window = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT), preferences_store=preferences)
    window.show()
    _open_select_wells(window)
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    assert window.review_hint.isVisible()

    window._start_manual_calibration()
    QTest.keyClick(window.image_canvas, Qt.Key_Escape)
    assert window._manual_calibration_points is None
    window._handle_image_click(10, 10, Qt.RightButton)
    assert not window.review_hint.isVisible()

    window.plates_panel_action.setChecked(False)
    assert window.review_splitter.widget(0).isHidden()
    window.close()
    app.processEvents()

    reopened = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT), preferences_store=preferences)
    reopened.show()
    _open_select_wells(reopened)
    reopened.load_plate("1070", SWISSCI_MIDI_3_LENS)
    assert not reopened.review_hint.isVisible()
    reopened.close()
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
    assert "confirmed · manual" in window.calibration_label.text()
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
    first_image = window.controller.plate.images[0]

    window._handle_image_click(100, 120, Qt.LeftButton)
    assert len(window.controller.session.targets_for(first_image)) == 1
    window.show_next()
    assert window.well_progress_label.text() == "This well · 0 of"
    window.show_previous()
    assert window.well_progress_label.text() == "This well · 1 of"

    window._handle_image_click(100, 120, Qt.RightButton)
    assert window.controller.session.targets_for(first_image) == ()
    window.close()
    app.processEvents()


def test_startup_missing_plate_returns_clean_error(tmp_path: Path, capsys) -> None:
    assert main(
        [
            "--root",
            str(tmp_path),
            "--allow-local-image-root",
            "--plate",
            "1070",
            "--plate-format",
            SWISSCI_MIDI_3_LENS.id,
            "--review-db",
            str(tmp_path / "reviews.sqlite3"),
        ]
    ) == 2
    assert "plate directory does not exist" in capsys.readouterr().err


def test_startup_says_so_when_the_image_share_is_not_mounted(
    tmp_path: Path, capsys
) -> None:
    # A root outside the repository is an operating path: a folder in place of
    # the share means the share is gone, not that the plate was deleted.
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
    assert "the network share is not mounted" in capsys.readouterr().err


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_auto_advance_count_moves_and_navigation_persists(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    database_path = tmp_path / "reviews.sqlite3"
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), SQLiteReviewStore(database_path)
    )
    window.load_plate("1070", SWISSCI_MIDI_3_LENS)
    first_image = window.controller.plate.images[0]

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
    with store._connection:
        store._connection.executemany(
            "INSERT INTO target_point(target_id, image_key, x_px, y_px, selected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (target.id, target.image_key, target.x_px, target.y_px,
                 target.selected_at.isoformat())
                for target in targets
            ),
        )
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
    first_image = window.controller.plate.images[0]
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
    _open_select_wells(window)
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
    _open_select_wells(window)
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
    def fail_checkpoint(*args) -> None:
        raise ReviewPersistenceError("test storage failure")

    monkeypatch.setattr(window.controller.store, "save_checkpoint", fail_checkpoint)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    window.show_next()

    assert window.controller.image_index == 0
    assert window.save_status_label.text() == "! Save failed"
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
    monkeypatch.undo()
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


def test_reopening_starts_at_home_with_finalized_experiment_status(
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
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,), name="BRD4 harvest")
    editor.protein_input.setText("BRD4")
    assert window.experiments._finalize_plan(editor) is not None
    window.close()
    app.processEvents()

    restored = ViewerWindow(RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path))
    assert restored.pages.currentWidget() is restored.home_page
    assert restored.home_page.recent_table.item(0, 0).text() == "BRD4 harvest"
    assert restored.home_page.recent_table.item(0, 2).text() == "Finalized r1"
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
    editor = _start(window, PlanType.FRAGMENT_SCREENING, (crystal,))
    editor.library_input.setCurrentIndex(1)
    window.experiments._persist_draft(editor)
    project_id = editor.project_id
    library_id = editor.library_input.currentData(Qt.UserRole)
    window.close()
    app.processEvents()

    offline = replace(settings, fragment_library_directory=tmp_path / "unmounted")
    reopened = ViewerWindow(
        RockMakerImageRepository(tmp_path), SQLiteReviewStore(database_path), settings=offline
    )
    recent = reopened.experiments._recent_experiments()
    editor = reopened.experiments.resume_experiment(recent[0].workspace_id, recent[0].plan_id)
    assert editor.library_input.currentData(Qt.UserRole) == library_id
    assert "Library unavailable" in editor.library_label.text()
    reopened.experiments._persist_draft(editor)
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
    editor = _start(window, PlanType.RAW_CRYSTAL, (crystal,))

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


def test_workspace_panel_creates_filters_renames_and_hides_workspaces(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    home = window.home_page
    sidebar = window.workspace_sidebar
    first_id = window.project_controller.active_project.id
    first = window.experiments.start_experiment(PlanType.RAW_CRYSTAL, "First harvest")
    first.protein_input.setText("BRD4")
    window.experiments.show_home()

    window.create_workspace()
    second_id = window.project_controller.active_project.id
    assert window.project_controller.active_project.name == "New workspace"
    assert window.image_set_model.rowCount() == 0
    assert [sidebar.workspace_list.item(row).text() for row in range(3)] == [
        "All experiments", "Untitled Workspace", "New workspace"
    ]
    assert home.title_label.text() == "New workspace"
    window.rename_workspace(second_id, "CypA campaign")
    assert home.title_label.text() == "CypA campaign"
    assert home.recent_empty_label.text() == "No experiments in this workspace yet."

    second = window.experiments.start_experiment(PlanType.RAW_CRYSTAL, "CypA harvest")
    second.protein_input.setText("CypA")
    assert window.experiment_page.home_button.text() == "‹ CypA campaign"
    assert sidebar.workspace_list.currentItem().text() == "CypA campaign"
    window.experiment_page.home_button.click()
    assert home.title_label.text() == "CypA campaign"
    window.experiments.resume_experiment(second_id, second.plan_id)
    sidebar.workspace_list.setCurrentRow(0)
    assert window.pages.currentWidget() is home
    assert home.title_label.text() == "All experiments"
    sidebar.workspace_list.setCurrentRow(2)
    assert second.project_id == second_id
    assert [home.recent_table.item(row, 0).text() for row in range(home.recent_table.rowCount())] == [
        "CypA harvest"
    ]
    assert home.recent_table.isColumnHidden(3)

    sidebar.workspace_list.setCurrentRow(0)
    assert home.title_label.text() == "All experiments"
    assert home.recent_table.rowCount() == 2
    assert not home.recent_table.isColumnHidden(3)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.hide_workspace(first_id)
    assert sidebar.workspace_list.count() == 2
    assert sidebar.hidden_toggle.text() == "▸ Hidden (1)"
    assert [row.name for row in window.experiments._recent_experiments()] == ["CypA harvest"]
    information = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: information.append(args[1])
    )
    window.hide_workspace(second_id)
    assert information == ["Cannot hide workspace"]

    window.close()
    app.processEvents()
    reopened = ViewerWindow(
        RockMakerImageRepository(tmp_path), SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    )
    assert reopened.project_controller.active_project.id == second_id
    assert reopened.workspace_sidebar.hidden_toggle.text() == "▸ Hidden (1)"
    reopened.restore_workspace(first_id)
    assert reopened.home_page.title_label.text() == "Untitled Workspace"
    assert reopened.home_page.recent_table.item(0, 0).text() == "First harvest"
    reopened.close()
    app.processEvents()


def test_examples_panel_lists_captioned_images_or_explains_setup(tmp_path: Path) -> None:
    from xtalflow.infrastructure.examples import load_examples
    from xtalflow.ui.examples_panel import ExamplesPanel

    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(8, 8)
    pixmap.fill(Qt.white)
    pixmap.save(str(tmp_path / "b-edge.png"))
    pixmap.save(str(tmp_path / "a-center.png"))
    (tmp_path / "a-center.txt").write_text("Center of a single crystal\nAvoid cracks", encoding="utf-8")

    examples = load_examples(tmp_path)
    panel = ExamplesPanel(examples, tmp_path)

    assert [example.caption for example in examples] == [
        "Center of a single crystal\nAvoid cracks", "b edge"
    ]
    assert panel.example_list.item(0).text() == "Center of a single crystal"
    assert panel.caption_label.text() == "Center of a single crystal\nAvoid cracks"
    assert load_examples(None) == ()
    assert "--examples-dir" in ExamplesPanel((), None).empty_label.text()
    panel.close()
    app.processEvents()


def test_opening_a_new_experiment_saves_no_draft_until_it_has_content(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    workspace_id = window.project_controller.active_project.id

    looked = window.experiments.start_experiment(PlanType.FRAGMENT_SCREENING)
    assert window.experiment_page.lifecycle_label.text() == "Not saved yet"
    assert "△" not in window.experiment_page.stepper.buttons[WorkflowStep.CONDITIONS].text()
    window.experiments.go_to_step(WorkflowStep.CONDITIONS)
    assert window.experiment_page.lifecycle_label.text() == "Not saved yet"
    window.experiment_page.home_button.click()

    assert store.planning.load_planning_drafts(workspace_id) == ()
    assert window.home_page.recent_table.rowCount() == 0
    assert looked.plan_id not in window.experiments._editors

    kept = window.experiments.start_experiment(PlanType.RAW_CRYSTAL)
    kept.protein_input.setText("BRD4")
    window.experiment_page.home_button.click()
    assert [draft.id for draft in store.planning.load_planning_drafts(workspace_id)] == [
        kept.plan_id
    ]

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.experiments.resume_experiment(workspace_id, kept.plan_id)
    window.experiment_page.delete_action.trigger()
    assert window.pages.currentWidget() is window.home_page
    assert store.planning.load_planning_drafts(workspace_id) == ()
    window.close()
    app.processEvents()


def test_workspace_panel_can_be_hidden_everywhere_and_is_remembered(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    preferences = JsonUserPreferencesStore(tmp_path / "preferences.json")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), preferences_store=preferences)
    window.show()
    app.processEvents()
    assert window.workspace_sidebar.isVisible()
    assert window.home_page.panel_button.isHidden()

    window.workspace_sidebar.collapse_button.click()
    assert not window.workspace_sidebar.isVisible()
    assert not window.home_page.panel_button.isHidden()
    assert not window.workspace_panel_action.isChecked()

    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    assert not window.experiment_page.panel_button.isHidden()
    window.experiment_page.panel_button.click()
    assert window.workspace_sidebar.isVisible()
    assert window.experiment_page.panel_button.isHidden()
    window.workspace_panel_action.trigger()
    assert not window.workspace_sidebar.isVisible()
    window.close()
    app.processEvents()

    reopened = ViewerWindow(RockMakerImageRepository(tmp_path), preferences_store=preferences)
    reopened.show()
    app.processEvents()
    assert not reopened.workspace_sidebar.isVisible()
    assert preferences.load().workspace_panel_visible is False
    reopened.close()
    app.processEvents()


def test_solvent_test_runs_from_conditions_to_saved_worksheets_and_reopens(
    tmp_path: Path, monkeypatch
) -> None:
    from xtalflow.domain.condition_test import ConditionTestDesign
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=tmp_path / "source-plates",
        create_missing_instrument_roots=True,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page

    window.workspace_sidebar.start_buttons["solvent_test"].click()
    editor = window.experiments.current_editor
    assert isinstance(editor, ConditionTestEditor)
    assert editor.plan_name.startswith("Solvent Test ")
    assert list(page.stepper.buttons) == [
        WorkflowStep.SETUP, WorkflowStep.CONDITIONS, WorkflowStep.SELECT_WELLS,
        WorkflowStep.REVIEW, WorkflowStep.WORKSHEETS,
    ]
    editor.protein_input.setText("BRD4")
    editor.drop_volume_input.setValue(100)
    assert page.primary_button.text() == "Set conditions"
    page.primary_button.click()
    assert window.experiments._current_step is WorkflowStep.CONDITIONS
    assert editor.title_label.text() == "Compare solvent concentration and soaking time"
    assert editor.conditions_drop_input.value() == 100
    assert editor.design_error == "Enter the source plate and well for DMSO."
    assert "Enter the source plate" in page.footer_status_label.text()
    assert editor.source_box.property("missing")
    assert editor.source_message_label.text() == (
        "Enter the source plate type, plate and wells; the ECHO worksheet needs them. "
        "No shared plates yet: choose New plate… in Plate to start one."
    )
    editor.update_additive(source_plate="LDV-1", source_well="a1")
    assert editor.design_error == "Choose the source plate type for DMSO."
    editor.update_additive(source_plate_type="384PP_DMSO2")
    assert editor.design_error is None
    assert not editor.source_box.property("missing")
    assert editor.source_well_input.text() == "A1"
    assert editor.matrix_table.horizontalHeaderItem(2).text() == "10 %"
    editor.select_condition(0, 2)
    assert (editor.transfer_label.text(), editor.actual_label.text()) == ("10 nL", "9.1 % DMSO")
    assert page.footer_status_label.text() == "✓ 16 conditions · 32 wells required"
    assert page.footer_notes_label.text() == "No wells selected yet"
    assert page.primary_button.text() == "Select 32 wells"
    editor.replicates_input.setValue(1)
    editor.select_condition(3, 3)
    editor.include_button.click()
    assert editor.matrix_table.item(3, 3).text() == "Excluded"
    assert editor.matrix_table.item(3, 2).text() == "1 well"
    assert editor.design.required_crystals == 15
    assert page.footer_status_label.text() == "✓ 15 conditions · 15 wells required"
    assert page.primary_button.text() == "Select 15 wells"
    page.primary_button.click()
    assert window.experiments._current_step is WorkflowStep.SELECT_WELLS

    now = datetime.now(timezone.utc)
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2070", f"{'ABCDEFGH'[index // 12]}{index % 12 + 1:02d}a",
            (CrystalTarget(f"target-{index}", Decimal(0), Decimal(0), now + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(15)
    )
    # Leaving Select wells re-reads the image review, so inject wells after moving on.
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    window.experiments._sync_editor_selection(editor, crystals[:10])
    assert editor.experiment_status.status_of(WorkflowStep.SELECT_WELLS).message == (
        "10 of 15 wells selected"
    )
    window.experiments.go_to_step(WorkflowStep.CONDITIONS)
    assert page.footer_notes_label.text() == "Selected 10 wells · 5 more needed"
    assert page.primary_button.text() == "Select 5 more wells"
    extra = SelectedCrystal(
        "image-extra", "2070", "H12a",
        (CrystalTarget("target-extra", Decimal(0), Decimal(0), now + timedelta(seconds=99)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    window.experiments._sync_editor_selection(editor, (*crystals, extra))
    window.experiments._refresh_experiment_status(editor)
    assert page.footer_notes_label.text() == "Selected 16 wells · 1 will remain unused"
    assert page.primary_button.text() == "Review assignments"
    page.primary_button.click()
    assert window.experiments._current_step is WorkflowStep.REVIEW
    window.experiments._sync_editor_selection(editor, crystals)
    assert editor.current_plan is not None
    assert window.experiments._finalize_plan(editor).experiment_id.startswith("PreTest-")
    window.experiments.go_to_step(WorkflowStep.WORKSHEETS)
    preparation, schedule = editor.worksheets_step.schedule_label.text().split("\n\n")
    assert preparation.splitlines()[:2] == [
        "Prepare before dispensing", "Crystal drops · 15, one per well"
    ]
    assert preparation.splitlines()[2].startswith("DMSO · LDV-1 A1 ")
    assert preparation.splitlines()[2].endswith("(15 µL dead volume + transfers + 10 % margin)")
    assert schedule.splitlines()[:3] == [
        "Planned schedule · relative to the start of dispensing (T+0)",
        "T+0 min · dispense with the ECHO file (11 wells)",
        "T+0 min · harvest A01a, A02a, A03a, A04a",
    ]
    page.primary_button.click()
    (export,) = store.audit.list_worksheet_exports(editor.last_revision.id)
    assert export.status == "succeeded", export.error_message
    assert Path(export.path_for("echo650")).is_file()
    # The source wells are now on the shared plate record, for everyone planning
    # next - in its own folder on this machine, not under the ECHO share.
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord

    assert not (tmp_path / "echo650" / "source-plates").exists()
    (used,) = SourcePlateRecord(tmp_path / "source-plates").entries("LDV-1")
    assert (used.kind, used.wells, used.experiment_id) == (
        "used", ("A1",), editor.last_revision.experiment_id
    )
    # This experiment's own wells are not someone else's.
    assert editor.design_error is None

    plan_id, workspace_id = editor.plan_id, editor.project_id
    window.experiments.show_home()
    window.close()
    app.processEvents()
    reopened = ViewerWindow(
        RockMakerImageRepository(tmp_path), SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=settings,
    )
    restored = reopened.experiments.resume_experiment(workspace_id, plan_id)
    assert isinstance(restored.design, ConditionTestDesign)
    assert restored.design.required_crystals == 15
    assert restored.design.additives[0].source_well == "A1"
    reopened.close()
    app.processEvents()


def test_setup_opens_with_the_cursor_in_the_first_blank_field(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=DEFAULT_SETTINGS)
    window.resize(1200, 800)
    window.show()
    app.processEvents()

    window.workspace_sidebar.start_buttons["fragment_screening"].click()
    editor = window.experiments.current_editor
    app.processEvents()
    assert window.focusWidget() is editor.protein_input

    # Both fields are filled in now, so coming back does not move the cursor to one.
    editor.protein_input.setText("BRD4")
    window.experiments.go_to_step(WorkflowStep.SELECT_WELLS)
    window.experiments.go_to_step(WorkflowStep.SETUP)
    app.processEvents()
    assert window.focusWidget() is not editor.name_input
    window.close()
    app.processEvents()


def test_a_second_revision_does_not_hide_the_worksheets_on_the_instruments(
    tmp_path: Path, monkeypatch
) -> None:
    """r1 is saved and the plan changes: the SHIFTERs still hold r1."""
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )
    window, store, editor = _fragment_plan_window(tmp_path, settings)
    page = window.experiment_page
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    page.primary_button.click()
    page.primary_button.click()
    assert editor.lifecycle_label.text() == "Finalized r1"

    editor.protein_input.setText("BRD4-B")
    window.experiments._persist_draft(editor)
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    page.primary_button.click()

    assert editor.lifecycle_label.text() == "Finalized r2"
    window.experiments.show_home()
    stage = window.home_page.recent_table.item(0, 2)
    assert stage.text() == "Finalized r2 · instruments have r1"
    detail = _tooltip(stage)
    assert "Harvesting now would follow that plan, not this one" in detail
    assert "Worksheets r1 saved" in detail
    window.close()
    app.processEvents()


def test_a_failed_retry_leaves_the_saved_worksheets_on_the_page(
    tmp_path: Path, monkeypatch
) -> None:
    """The instruments keep the files from the save that worked."""
    from xtalflow.domain.plan_lifecycle import WorksheetExportEvent

    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )
    window, store, editor = _fragment_plan_window(tmp_path, settings)
    page = window.experiment_page
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    page.primary_button.click()
    page.primary_button.click()
    revision = editor.last_revision
    saved_path = store.audit.list_worksheet_exports(revision.id)[0].path_for("shifter2")

    store.audit.record_worksheet_export(WorksheetExportEvent(
        "failed-retry", revision.id, "tester", datetime.now(timezone.utc),
        "failed", (), "the share went away",
    ))
    window.experiments._refresh_worksheets_step(editor)

    result = editor.worksheets_step.result_label.text()
    assert "Saved r1" in result
    assert "saving again failed" in result and "the share went away" in result
    # The files are still listed, because they are still on the instruments.
    table = editor.worksheets_step.instrument_table
    assert table.item(2, 3).text() == saved_path
    assert (
        editor.experiment_status.status_of(WorkflowStep.WORKSHEETS).state
        is StepState.COMPLETE
    )
    window.close()
    app.processEvents()


def _tooltip(item) -> str:
    """A tooltip's words, without the line breaks the list puts in for Qt."""
    return " ".join(item.toolTip().split())


def _settle(app, done, attempts: int = 100) -> None:
    """Let the background MxLive read finish without freezing the test."""
    for _ in range(attempts):
        app.processEvents()
        if done():
            return
        QTest.qWait(20)


def test_the_list_shows_what_the_beamline_did_once_mxlive_answers(
    tmp_path: Path, monkeypatch
) -> None:
    """The stages after the upload come from MxLive, in the background."""
    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, _dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", _RecordingWriter
    )
    window.experiments._upload_plan_labworks(editor)

    # Before MxLive answers, the list says only what XtalFlow itself knows.
    window.experiments.show_home()
    assert window.home_page.recent_table.item(0, 2).text() == (
        "Sent r1 · no worksheets"
    )
    _settle(app, lambda: not window.experiments._beamline_busy)

    class Harvested(_FakeMxLiveReader):
        years = []

        def labworks(self, year):
            Harvested.years.append(year)
            return tuple(
                MxLiveLabwork.from_mapping({
                    "expri_id": revision.experiment_id,
                    "puck_name": "5C-0029",
                    "harvest_time": "2026-06-02 10:0{}".format(index),
                })
                for index in range(2)
            )

    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", Harvested
    )
    window.experiments._beamline_checked_at = 0.0
    window.experiments.show_home()
    _settle(
        app,
        lambda: window.home_page.recent_table.item(0, 2).text().startswith("Harvested"),
    )

    stage = window.home_page.recent_table.item(0, 2)
    assert stage.text() == "Harvested r1 · no worksheets"
    assert "harvested all 2 crystals of r1 into puck 5C-0029" in _tooltip(stage)
    # The year comes from the experiment ID, and one read covers it.
    assert Harvested.years == [str(int(revision.experiment_id.split("-")[1][:4]))]
    window.close()
    app.processEvents()


def test_mxlive_history_opens_from_the_panel_and_fills_itself(
    tmp_path: Path, monkeypatch
) -> None:
    """The third place to stand, beside new experiments and workspaces."""
    app = QApplication.instance() or QApplication([])
    window, _store, _editor, _revision, _dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch
    )

    class History(_FakeMxLiveReader):
        asked = []

        def labworks(self, year):
            History.asked.append(year)
            if not year.endswith("6"):
                return ()
            return tuple(
                MxLiveLabwork.from_mapping({
                    "expri_id": "FragSC-202603-OLD-01", "protein_name": "OLD",
                    "plate_code": "1234", "plate_well": f"A0{index}a",
                    "crystal_no": index, "name": "someone-else",
                    "staff_comments": "Uploaded by XtalFlow · Fragment Screening",
                })
                for index in (1, 2)
            )

    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", History
    )
    page = window.mxlive_history_page

    window.workspace_sidebar.history_button.click()

    assert window.pages.currentWidget() is page
    _settle(app, lambda: page.experiment_table.rowCount() > 0)
    assert page.experiment_table.item(0, 0).text() == "FragSC-202603-OLD-01"
    assert page.experiment_table.item(0, 4).text() == "not yet"
    # Two years are read, newest first, and only once each.
    assert len(History.asked) == 2 and History.asked == sorted(History.asked, reverse=True)
    asked_once = list(History.asked)
    window.experiments.show_mxlive_history()
    assert History.asked == asked_once

    # An experiment that is only on MxLive stays out of the working list.
    window.experiments.show_home()
    names = [
        window.home_page.recent_table.item(row, 0).text()
        for row in range(window.home_page.recent_table.rowCount())
    ]
    assert "FragSC-202603-OLD-01" not in names
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_the_count_that_moves_to_the_next_well_is_read_while_clicking() -> None:
    """The setting is the denominator, so it cannot be forgotten."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), auto_advance_target_count=2
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    # A confirmed boundary leaves the jump free; see the test below it.
    window._accept_current_calibration()
    first_well = window.well_input.text()

    window.controller.add_target(0.5, 0.5, 100, 100)
    window._update_review_summary()
    assert window.well_progress_label.text() == "This well · 1 of"
    assert window.auto_advance_input.value() == 2

    # The second position moves on, and the status bar says why.
    window._handle_image_click(0.6, 0.6, Qt.LeftButton)

    assert window.well_input.text() != first_well
    message = window.status_message_label.text()
    assert "2 positions placed" in message
    assert first_well in message and window.well_input.text() in message
    # The reading starts again on the new well.
    assert window.well_progress_label.text() == "This well · 0 of"
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_the_view_does_not_leave_a_well_whose_boundary_is_unconfirmed(
    tmp_path: Path,
) -> None:
    """Moving on pointed the boundary controls at the next well and locked them."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=1,
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    held = window.well_input.text()
    image_key = window.controller.current_image.image_key

    window._handle_image_click(600, 500, Qt.LeftButton)

    assert window.well_input.text() == held
    assert (
        window.status_message_label.toolTip()
        == f"{held} \u00b7 confirm the boundary to move on"
    )
    # The status line names the well, so a confirmation cannot land elsewhere.
    assert window.calibration_label.text().startswith(f"{held} \u00b7")
    assert window.accept_calibration_button.isEnabled()

    window.accept_calibration_button.click()

    assert window.controller.store.load_calibration(image_key).confirmed
    assert window.well_input.text() != held
    assert f"{held} \u00b7 boundary confirmed" in window.status_message_label.toolTip()
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_wells_that_cannot_be_planned_are_not_counted_as_wells_to_check(
    tmp_path: Path, monkeypatch
) -> None:
    """Blaming the wells sent the user to an empty dock with a number that never moved."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page
    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    editor = window.experiments.current_editor
    editor.protein_input.setText("BRD4")
    page.primary_button.click()
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._accept_current_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window.experiments._sync_current_selection()
    assert page.primary_button.text() == "Use 1 well"
    assert page.primary_button.isEnabled()

    def refuse() -> None:
        raise ValueError("all target image sets need a supported plate format")

    monkeypatch.setattr(
        window.project_controller, "selected_crystals_for_plan", refuse
    )
    window.experiments._sync_current_selection()

    # The well is confirmed and in bounds, so there is nothing to check.
    assert editor.wells_needing_attention == 0
    assert "supported plate format" in page.footer_status_label.text()
    assert not page.primary_button.isEnabled()
    assert page.primary_button.toolTip() == page.footer_status_label.text()
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_a_confirmed_boundary_with_a_position_outside_it_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    """Green above the image while the well stayed in the list read as a fault."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page
    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    editor = window.experiments.current_editor
    editor.protein_input.setText("BRD4")
    page.primary_button.click()
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._accept_current_calibration()
    calibration = window.current_calibration
    assert calibration.confirmed

    window._handle_image_click(
        calibration.center_x_px, calibration.center_y_px, Qt.LeftButton
    )
    # A corner of the picture is inside the image and outside the well.
    assert not calibration.contains(5, 5)
    window._handle_image_click(5, 5, Qt.LeftButton)
    window.experiments._sync_current_selection()

    # A warning, not a bar: nothing to check, and the way on stays open.
    assert editor.wells_needing_attention == 0
    assert "1 position outside the boundary" in editor.outside_positions_warning
    assert "A01a" in editor.outside_positions_warning
    assert "mm" in editor.outside_positions_warning
    assert page.primary_button.text() == "Use 1 well"
    assert page.primary_button.isEnabled()
    # Said where it is seen, with the mark matching the words.
    assert theme.SYMBOL_ATTENTION in page.footer_status_label.text()
    assert "1 position outside the boundary" in page.footer_status_label.text()
    assert "1 position outside it" in window.calibration_label.text()
    assert theme.SYMBOL_OK not in window.calibration_label.text()
    # And the plan keeps the position, as it is.
    crystals = window.project_controller.selected_crystals_for_plan()
    assert len(crystals) == 1
    assert len(crystals[0].targets) == 2

    page.primary_button.click()
    assert window.experiments._current_step is WorkflowStep.REVIEW
    # Worth seeing again where finalizing is decided.
    assert any(
        "outside the boundary" in note
        for note in editor.experiment_status.notes
    )
    window.close()
    app.processEvents()


def test_the_panel_lights_a_lamp_for_each_share(
    tmp_path: Path, monkeypatch
) -> None:
    """Which shares are up decides what can be done, so the panel says it first."""
    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
    )
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=settings,
    )
    # The reading runs off this thread, so a dropped mount cannot freeze the
    # window; give the worker its moment.
    QTest.qWait(1500)
    app.processEvents()
    buttons = window.workspace_sidebar.share_buttons

    assert set(buttons) == {"rmserver", "echo650", "shifter1", "shifter2"}
    # The name stays plain and uncoloured; the lamp carries the state, and a
    # share that is down gets weight on top of it. A folder that is there but
    # is not a share is marked like one that is missing: neither can be saved
    # to, and only the reason differs.
    assert buttons["echo650"].text() == "ECHO 650"
    assert "font-weight" in buttons["echo650"].styleSheet()
    assert theme.ERROR not in buttons["echo650"].styleSheet()
    assert "not a mounted share" in buttons["echo650"].toolTip()
    assert "the folder is not there" in buttons["shifter2"].toolTip()
    assert str(tmp_path / "shifter2") in buttons["shifter2"].toolTip()
    assert "ECHO 650" in buttons["echo650"].accessibleName()
    assert not buttons["echo650"].icon().isNull()
    assert not window._share_check_busy

    # Pressing one says what the reading found, in words, in the status bar.
    buttons["shifter2"].click()
    QTest.qWait(1200)
    app.processEvents()
    assert "SHIFTER 2" in window.status_message_label.toolTip()
    assert "the folder is not there" in window.status_message_label.toolTip()
    window.close()
    app.processEvents()
    assert not window._share_check_timer.isActive()


def test_a_lamp_says_which_state_it_is_in_without_its_colour() -> None:
    """Hold the colour still: a lit lamp and an unlit one must still differ."""
    QApplication.instance() or QApplication([])

    lit = icons.lamp_pixmap(True, 14, theme.OK)
    unlit = icons.lamp_pixmap(False, 14, theme.OK)

    assert lit.toImage() != unlit.toImage()
    # And the same shape in either colour, so the shape is the thing that says it.
    assert icons.lamp_pixmap(False, 14, theme.ERROR).size() == unlit.size()


def test_a_blocked_button_says_why_it_cannot_be_pressed(tmp_path: Path) -> None:
    """The reason is on the button, not only in the line above it."""
    app = QApplication.instance() or QApplication([])
    window, _store, editor = _fragment_plan_window(tmp_path, DEFAULT_SETTINGS)
    page = window.experiment_page
    window.experiments.go_to_step(WorkflowStep.CONDITIONS)

    editor.volume_input.setValue(26)
    window.experiments._refresh_experiment_status(editor)

    assert not page.primary_button.isEnabled()
    assert "2.5 nL" in page.primary_button.toolTip()
    assert page.primary_button.toolTip() == page.footer_status_label.text()

    editor.volume_input.setValue(25)
    window.experiments._refresh_experiment_status(editor)

    assert page.primary_button.isEnabled()
    assert page.primary_button.toolTip() == ""
    window.close()
    app.processEvents()


def test_arriving_at_conditions_asks_about_a_volume_the_new_wells_broke(
    tmp_path: Path, monkeypatch
) -> None:
    """Choosing a 3-position well after setting 25 nL is the same mistake."""
    app = QApplication.instance() or QApplication([])
    window, _store, editor = _fragment_plan_window(tmp_path, DEFAULT_SETTINGS)
    asked = []
    monkeypatch.setattr(
        type(editor), "_ask_to_use_volume",
        lambda self, suggestion, text: asked.append((suggestion, text)) or False,
    )

    # The volume fits what is selected, so nothing is asked on the way in.
    window.experiments.go_to_step(WorkflowStep.CONDITIONS)
    assert asked == []

    # A well with three positions is chosen, which 25 nL cannot share. The
    # question comes when the step is reached again, not while clicking.
    editor.volume_input.setValue(25)
    now = datetime.now(timezone.utc)
    editor.set_crystals((
        SelectedCrystal(
            "image", "2070", "A01a",
            tuple(
                CrystalTarget(f"t{index}", Decimal(0), Decimal(0), now)
                for index in range(3)
            ),
            SWISSCI_MIDI_3_LENS.id,
        ),
    ))
    assert asked == []

    window.experiments.go_to_step(WorkflowStep.CONDITIONS)

    assert len(asked) == 1
    assert "cannot be split" in asked[0][1]
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_the_dock_lists_wells_and_deletes_all_of_a_wells_positions(
    tmp_path: Path, monkeypatch
) -> None:
    """A well with three positions is one row, and deleting it takes all three."""
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
    )
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.show()
    app.processEvents()
    window.target_summary_button.click()
    app.processEvents()
    # Stay on the well while three positions are placed in it.
    window.auto_advance_input.setValue(3)
    for offset in range(3):
        window._handle_image_click(600 + offset, 500 + offset, Qt.LeftButton)
    window.show_next()
    window._handle_image_click(600, 500, Qt.LeftButton)
    window._refresh_target_summary()

    table = window.target_summary_table
    assert table.rowCount() == 2
    assert [table.item(row, 3).text() for row in range(2)] == ["3", "1"]
    assert "2 wells · 4 positions" in window.target_summary_status_label.text()
    assert window.target_summary_filter.itemText(0) == "All wells (2)"

    # Deleting a row takes every position in that well, and says so first.
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.Yes,
    )
    table.selectRow(0)
    window.remove_targets_button.click()

    assert "3 soaking positions from 1 well" in asked[0]
    assert "3 positions in 2070" in asked[0]
    window._refresh_target_summary()
    assert table.rowCount() == 1
    assert table.item(0, 3).text() == "1"
    window.close()
    app.processEvents()


def test_a_solvent_test_says_to_take_a_fresh_plate_while_the_echo_is_away(
    tmp_path: Path, monkeypatch
) -> None:
    """The missing record turns into one bench instruction, above the wells it doubts."""
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    app = QApplication.instance() or QApplication([])
    (tmp_path / "shifter1").mkdir()
    (tmp_path / "shifter2").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(
            tmp_path / "missing-echo", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=tmp_path / "unmade" / "source-plates",
        create_missing_instrument_roots=False,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    window.workspace_sidebar.start_buttons["solvent_test"].click()
    editor = window.experiments.current_editor
    assert isinstance(editor, ConditionTestEditor)
    editor.protein_input.setText("BRD4")
    editor.drop_volume_input.setValue(100)
    editor.update_additive(
        source_plate="SOLVENT-09", source_well="a1", source_plate_type="384PP_DMSO2"
    )
    editor.replicates_input.setValue(1)
    required = editor.design.required_crystals
    now = datetime.now(timezone.utc)
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2070", f"{'ABCDEFGH'[index // 12]}{index % 12 + 1:02d}a",
            (CrystalTarget(f"target-{index}", Decimal(0), Decimal(0), now + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(required)
    )
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    window.experiments._sync_editor_selection(editor, crystals)
    window.experiments.go_to_step(WorkflowStep.WORKSHEETS)

    preparation = editor.worksheets_step.schedule_label.text().split("\n\n")[0].splitlines()
    assert preparation[1].startswith(f"{theme.SYMBOL_ATTENTION} Label a fresh source plate")
    assert "taken as free" in preparation[1]
    # The names are right below, and they travel inside the ECHO worksheet.
    assert preparation[3].startswith("DMSO · ")
    assert str(tmp_path / "pending" / "source-plates") in preparation[1]
    # Said above the wells it doubts, not instead of them.
    assert preparation[2] == f"Crystal drops · {required}, one per well"
    window.close()
    app.processEvents()


def test_opening_a_solvent_test_puts_held_source_wells_back_on_the_record(
    tmp_path: Path, monkeypatch
) -> None:
    """What was taken while the ECHO was away joins the record the moment it is back."""
    from xtalflow.domain.echo_source import PlateRecordEntry
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord, record_directory

    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=tmp_path / "source-plates",
        create_missing_instrument_roots=True,
    )
    held = SourcePlateRecord(record_directory(tmp_path / "pending"), create_root=True)
    held.append("SOLVENT-09", [PlateRecordEntry(
        "used", ("A1",), "lee", datetime.now(timezone.utc), "SolventTest-07", "DMSO",
    )])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    window.workspace_sidebar.start_buttons["solvent_test"].click()

    shared = SourcePlateRecord(tmp_path / "source-plates")
    (recorded,) = shared.entries("SOLVENT-09")
    assert (recorded.wells, recorded.experiment_id) == (("A1",), "SolventTest-07")
    assert "SOLVENT-09" in window.status_message_label.toolTip()
    # Held no longer: the shared record is the only copy now.
    assert held.entries("SOLVENT-09") == ()
    window.close()
    app.processEvents()


def test_the_record_moves_off_the_echo_share_a_plate_at_a_time(
    tmp_path: Path, monkeypatch
) -> None:
    """The move happens whenever the old share is up, and leaves it whole."""
    from xtalflow.domain.echo_source import PlateRecordEntry
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord, record_directory

    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=tmp_path / "source-plates",
        create_missing_instrument_roots=True,
    )
    old = SourcePlateRecord(record_directory(tmp_path / "echo650"), create_root=True)
    old.append("SOLVENT-01", [PlateRecordEntry(
        "used", ("A1",), "park", datetime.now(timezone.utc), "Before-01", "DMSO",
    )])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    window.workspace_sidebar.start_buttons["solvent_test"].click()

    (moved,) = SourcePlateRecord(tmp_path / "source-plates").entries("SOLVENT-01")
    assert (moved.wells, moved.experiment_id) == (("A1",), "Before-01")
    # Copied, not taken: the old place is still readable by whoever has not
    # been moved over yet.
    assert [entry.wells for entry in old.entries("SOLVENT-01")] == [("A1",)]
    window.close()
    app.processEvents()


def test_a_dropped_echo_share_is_never_asked_about_from_the_window_thread(
    tmp_path: Path, monkeypatch
) -> None:
    """is_dir on a dead SMB mount takes the best part of a minute; the lamps know instead."""
    from xtalflow.infrastructure import source_plate_record as record_module
    from xtalflow.infrastructure.mounts import ShareState
    from xtalflow.infrastructure.source_plate_record import record_directory
    from xtalflow.infrastructure.worksheet_exporter import pending_root

    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=tmp_path / "source-plates",
        create_missing_instrument_roots=True,
        require_network_instrument_mounts=True,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    looked = []
    monkeypatch.setattr(
        record_module, "merge_held",
        lambda held, shared, consume=True: looked.append(held.directory)
        or record_module.MergeResult(),
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.merge_held", record_module.merge_held
    )

    # No reading has come back yet, so the ECHO share is not known to be up.
    assert not window.share_is_mounted("echo650")
    window.workspace_sidebar.start_buttons["solvent_test"].click()

    # The held entries are still swept - that folder is ours and local - but
    # the old place under the share is left alone.
    assert looked == [record_directory(pending_root(settings))]

    # Once a reading says it is mounted, the move is attempted again.
    window._share_states = (
        ShareState("echo650", "ECHO 650", tmp_path / "echo650", True, "mounted"),
    )
    assert window.share_is_mounted("echo650")
    window.experiments._catch_up_source_record()
    assert looked[1:] == [
        record_directory(tmp_path / "echo650"), record_directory(pending_root(settings))
    ]
    window.close()
    app.processEvents()


def test_a_record_under_a_dropped_share_is_called_away_without_asking_the_share(
    tmp_path: Path, monkeypatch
) -> None:
    """The Worksheets step asks this on every refresh; it must never be a stat."""
    from xtalflow.infrastructure.mounts import ShareState

    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        source_plate_record_directory=None,
        create_missing_instrument_roots=False,
        require_network_instrument_mounts=True,
    )
    window, _store, _editor = _fragment_plan_window(tmp_path, settings)
    asked = []
    monkeypatch.setattr(
        Path, "is_dir",
        lambda self: asked.append(self) or Path.exists(self),
    )

    # The folder is there, but no reading says the share is mounted.
    assert window.experiments._source_record_is_away()
    assert not any(str(path).startswith(str(tmp_path / "echo650")) for path in asked)

    window._share_states = (
        ShareState("echo650", "ECHO 650", tmp_path / "echo650", True, "mounted"),
    )
    assert not window.experiments._source_record_is_away()
    assert not any(str(path).startswith(str(tmp_path / "echo650")) for path in asked)
    window.close()
    app.processEvents()


def test_every_window_title_carries_the_version(tmp_path: Path) -> None:
    """Several versions are installed side by side; a window must say which it is."""
    import tomllib

    from xtalflow import APP_TITLE, __version__

    app = QApplication.instance() or QApplication([])
    # The number people type is the installed folder's name, so the number in
    # the code has to be the same one. It said 0.1.0 for three months.
    packaged = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    )
    assert packaged["project"]["version"] == __version__
    assert APP_TITLE == f"XtalFlow {__version__}"

    window = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT))
    assert window.windowTitle() == APP_TITLE

    # And it survives moving around: the title is rewritten on several screens.
    window.experiments.show_home()
    assert __version__ in window.windowTitle()
    window.close()
    app.processEvents()


def test_the_plate_map_is_absent_entirely_without_a_crystal_model(tmp_path: Path) -> None:
    """A site with no model must see today's window, not a dead control."""
    app = QApplication.instance() or QApplication([])
    settings = replace(DEFAULT_SETTINGS, crystal_model_path=None)
    window = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT), settings=settings)

    assert not window.plate_map_button.isVisible()
    assert not hasattr(window, "plate_map_action")
    assert not window.plate_map_dock.isVisible()
    # And nothing in the View menu offers it.
    assert "Plate Map" not in {
        action.text() for action in window.view_menu.actions()
    }
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_the_plate_map_opens_and_a_cell_moves_the_review_to_that_drop(
    tmp_path: Path,
) -> None:
    """The cell shows a well's best drop, so the click has to land on it."""
    from xtalflow.domain.crystal_score import CrystalScore
    from xtalflow.ui.load_plates_dialog import PlateSource

    app = QApplication.instance() or QApplication([])
    settings = replace(DEFAULT_SETTINGS, crystal_model_path=tmp_path / "crystal3.onnx")
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), store, settings=settings
    )
    window.show()
    app.processEvents()
    window.load_plates(SWISSCI_MIDI_3_LENS, (
        (PlateSource("2070", 14122, "profileID_1"),
         RockMakerImageRepository(FIXTURE_ROOT).load_plate("2070")),
    ))
    app.processEvents()
    assert window.controller is not None

    # Say the second drop of some well is the one the model liked.
    images = window.controller.plate.images
    target = next(
        index for index, image in enumerate(images)
        if image.drop_number == 2 and image.well_number != images[0].well_number
    )
    window._crystal_scores = {
        images[target].image_key: CrystalScore(
            images[target].image_key, ("crystal", "drop", "empty"), (0.95, 0.03, 0.02), "m"
        )
    }

    window.plate_map_button.setChecked(True)
    app.processEvents()
    assert window.plate_map_dock.isVisible()
    assert window.plate_map_panel.map.rows == 8

    cell = next(
        item for item in window.plate_map_panel.map._cells.values()
        if item.image_index == target
    )
    assert cell.label == "crystal"
    window.plate_map_panel.map.well_clicked.emit(cell.image_index)
    app.processEvents()

    assert window.controller.image_index == target
    assert window.controller.current_image.drop_number == 2
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_a_plate_is_scored_in_the_background_and_crystal_first_reorders_the_walk(
    tmp_path: Path,
) -> None:
    """End to end with the real model: open a plate, score it, walk by rank."""
    from xtalflow.ui.load_plates_dialog import PlateSource

    model = Path(__file__).resolve().parent.parent / "models" / "crystal3.onnx"
    if not model.is_file():
        pytest.skip("no models/crystal3.onnx; make it with codex/export_crystal_onnx.py")

    app = QApplication.instance() or QApplication([])
    settings = replace(DEFAULT_SETTINGS, crystal_model_path=model)
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), store, settings=settings
    )
    window.show()
    app.processEvents()
    window.load_plates(SWISSCI_MIDI_3_LENS, (
        (PlateSource("2070", 14122, "profileID_1"),
         RockMakerImageRepository(FIXTURE_ROOT).load_plate("2070")),
    ))

    # 192 images at about a tenth of a second each; wait, pumping the loop.
    deadline = time.monotonic() + 120
    while window._crystal_scoring_busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(100)
    assert not window._crystal_scoring_busy, "scoring did not finish"
    assert len(window._crystal_scores) == len(window.controller.plate.images)

    # Well order until asked; the view never moves when the order changes.
    assert window.controller.walk_order == tuple(range(len(window.controller.plate.images)))
    here = window.controller.image_index
    window.crystal_first_input.setChecked(True)
    app.processEvents()
    assert window.controller.image_index == here

    order = window.controller.walk_order
    assert sorted(order) == list(range(len(order))), "nothing may be hidden"
    probabilities = [
        window._crystal_scores[window.controller.plate.images[index].image_key]
        .crystal_probability
        for index in order
    ]
    assert probabilities == sorted(probabilities, reverse=True)

    # And the walk really follows it: next goes to the second-ranked image.
    window.controller.move_to(order[0])
    assert window.controller.move_next()
    assert window.controller.image_index == order[1]

    # Opening the same plate again is immediate: the scores are on disk.
    scored_again = window._crystal_service().stored(
        window.project_controller.active_image_set.id
    )
    assert len(scored_again) == len(window._crystal_scores)
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_closing_the_window_stops_a_plate_that_is_still_being_scored(
    tmp_path: Path,
) -> None:
    """Thirty seconds of worker thread must not outlive the window it was for."""
    from xtalflow.ui.load_plates_dialog import PlateSource

    model = Path(__file__).resolve().parent.parent / "models" / "crystal3.onnx"
    if not model.is_file():
        pytest.skip("no models/crystal3.onnx; make it with codex/export_crystal_onnx.py")

    app = QApplication.instance() or QApplication([])
    settings = replace(DEFAULT_SETTINGS, crystal_model_path=model)
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), store, settings=settings
    )
    window.show()
    app.processEvents()
    window.load_plates(SWISSCI_MIDI_3_LENS, (
        (PlateSource("2070", 14122, "profileID_1"),
         RockMakerImageRepository(FIXTURE_ROOT).load_plate("2070")),
    ))
    assert window._crystal_scoring_busy, "scoring should have started"

    window.close()
    app.processEvents()

    assert window._crystal_stop_scoring
    # It gives up between batches, so a part-scored plate is what is kept.
    deadline = time.monotonic() + 30
    while window._crystal_scoring_busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(50)
    assert not window._crystal_scoring_busy, "scoring went on after the window closed"


def _scored_window(tmp_path: Path):
    """A window with fixture plate 2070 scored by the real model."""
    from xtalflow.ui.load_plates_dialog import PlateSource

    model = Path(__file__).resolve().parent.parent / "models" / "crystal3.onnx"
    if not model.is_file():
        pytest.skip("no models/crystal3.onnx; make it with codex/export_crystal_onnx.py")
    if not FIXTURE_ROOT.is_dir():
        pytest.skip("local RMServer fixture is not available")
    app = QApplication.instance() or QApplication([])
    settings = replace(DEFAULT_SETTINGS, crystal_model_path=model)
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT), store, settings=settings
    )
    window.show()
    app.processEvents()
    window.load_plates(SWISSCI_MIDI_3_LENS, (
        (PlateSource("2070", 14122, "profileID_1"),
         RockMakerImageRepository(FIXTURE_ROOT).load_plate("2070")),
    ))
    deadline = time.monotonic() + 120
    while window._crystal_scoring_busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(100)
    assert window._crystal_scores
    return app, window


def test_marks_placed_while_walking_crystal_first_do_not_count_for_the_check(
    tmp_path: Path,
) -> None:
    """Otherwise the check marks its own homework and always passes.

    Walking crystal first shows the top-ranked images first, so marks made
    there rank 1, 2, 3 by construction and say nothing about the model.
    """
    from xtalflow.domain.crystal_score import CRYSTAL_FIRST, WELL_ORDER

    app, window = _scored_window(tmp_path)
    image_set_id = window.project_controller.active_image_set.id
    order = crystal_first_order(
        [image.image_key for image in window.controller.plate.images],
        window._crystal_scores,
    )
    best = [window.controller.plate.images[index] for index in order[:4]]
    store = window._crystal_scoring.store

    # Four marks on the four best images - but placed while crystal first was on.
    for image in best:
        window.controller.session.add_target(image, 10, 10, 100, 100)
        store.remember_mark_ordering(image_set_id, image.image_key, CRYSTAL_FIRST)

    assert window._crystal_self_check().state == "unverified", (
        "marks made under crystal first must not be counted"
    )

    # The same four, placed in well order, are a real measurement.
    for image in best:
        store.remember_mark_ordering(image_set_id, image.image_key, WELL_ORDER)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE mark_ordering SET ordering = ? WHERE image_set_id = ?",
            (WELL_ORDER, image_set_id),
        )
    check = window._crystal_self_check()
    assert check.state == "pass"
    assert check.ranks == (1, 2, 3, 4)
    window.close()
    app.processEvents()


def test_a_plate_the_order_gets_wrong_says_so_and_unticks_itself(
    tmp_path: Path,
) -> None:
    """Never turned off under the user: it stays usable, it just stops claiming."""
    app, window = _scored_window(tmp_path)
    order = crystal_first_order(
        [image.image_key for image in window.controller.plate.images],
        window._crystal_scores,
    )
    # Mark the four the model liked least - in well order, so they count.
    for index in order[-4:]:
        window.controller.session.add_target(
            window.controller.plate.images[index], 10, 10, 100, 100
        )

    window.crystal_first_input.setChecked(True)
    app.processEvents()

    check = window._crystal_self_check()
    assert check.state == "fail"
    assert not window.crystal_first_input.isChecked(), "a failing plate starts unticked"
    assert window.crystal_first_input.text().startswith(theme.SYMBOL_ATTENTION)
    assert "not used on this plate" in window.crystal_first_input.toolTip()
    # Still usable: it is a permutation, so it hides nothing even when wrong.
    assert window.crystal_first_input.isEnabled()
    window.crystal_first_input.setChecked(True)
    app.processEvents()
    assert window.controller.walk_order != tuple(
        range(len(window.controller.plate.images))
    )
    window.close()
    app.processEvents()


def test_the_rank_says_why_this_image_is_in_front_of_you(tmp_path: Path) -> None:
    """Crystal first jumps across the plate; "47 / 288" alone explains nothing."""
    app, window = _scored_window(tmp_path)
    total = len(window.controller.plate.images)

    # Well order: unchanged, and the model stays silent.
    assert window.position_label.text().startswith("1 / ")
    assert "crystal" not in window.position_label.text()

    window.crystal_first_input.setChecked(True)
    app.processEvents()
    window.controller.move_to(window.controller.walk_order[2])
    window._show_current_image()

    assert window.position_label.text().startswith(f"crystal rank 3 of {total}")
    # Still no percentage anywhere: this model's accuracy has never been
    # measured, so a figure would be a claim nobody can back.
    assert "%" not in window.position_label.text()

    # An image the model could not read says so, and says it comes first.
    window._crystal_scores.pop(window.controller.current_image.image_key)
    window.controller.set_walk_order(None)
    window._apply_crystal_order()
    here = window.controller.walk_order[0]
    window.controller.move_to(here)
    window._show_current_image()
    if window.controller.current_image.image_key not in window._crystal_scores:
        assert "not scored · shown first" in window.position_label.text()
    window.close()
    app.processEvents()


def test_a_position_shows_on_the_plate_map_without_moving_first(
    tmp_path: Path,
) -> None:
    """The count replaces the glyph, so it has to appear when the mark does."""
    app, window = _scored_window(tmp_path)
    window.plate_map_button.setChecked(True)
    app.processEvents()
    well = window.controller.current_image.well_number
    assert window.plate_map_panel.map._cells[well].positions == 0

    window.controller.session.add_target(
        window.controller.current_image, 10, 10, 100, 100
    )
    window._refresh_plate_map()

    assert window.plate_map_panel.map._cells[well].positions == 1
    window.close()
    app.processEvents()
