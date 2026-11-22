import getpass
import json
import re
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
from xtalflow.domain.plan_lifecycle import PlanRevision
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QImage, QMouseEvent, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox

from xtalflow.domain import (
    PLATE_FORMATS,
    PlanType,
    ImageFilter,
    ReviewSession,
    SWISSCI_MIDI_3_LENS,
    SWISSCI_MRC_2_WELL,
    TargetPoint,
)
from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
)
from xtalflow.application import ReviewPersistenceError
from xtalflow.infrastructure.source_plate_record import (
    SourcePlateRecord,
    record_directory,
)
from xtalflow.domain.echo_source import USED, PlateRecordEntry
from xtalflow.infrastructure import mounts
from xtalflow.infrastructure import (
    ImageSourceUnavailable,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.application.experiment_workflow import (
    StepState,
    WorkflowStep,
    evaluate_experiment,
)
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


def _leave_every_boundary_to_the_person(window) -> None:
    """Before ``load_plate``, for a test that needs a well to open unconfirmed.

    A plate is trusted from the moment it loads, and its first well is
    confirmed there and then if it scores at or above the user's number -
    before a test can reach the checkbox to say otherwise. A01a of plate
    2070 scores 0.89, so it stayed unconfirmed for as long as the default
    was 90 and nobody had to say so. Say so: at 100 nothing is confirmed on
    anybody's behalf, whatever the default becomes.
    """
    window.auto_confirm_confidence_input.setValue(100)


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


def test_a_volume_that_will_not_divide_equally_is_planned_and_described() -> None:
    """It used to refuse to make a plan at all until the volume was changed.

    Now it divides what there is, and the table says how: the leftover steps
    go to the positions clicked first, so three positions sharing 25 nL get
    10, 7.5 and 7.5. The column used to print the first share times the
    count - "10.0 nL x 3" - which claimed 30 nL had been planned.
    """
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

    plan = editor.current_plan
    assert plan is not None, "a dose that will not divide is still a plan"
    (assignment,) = plan.assignments
    assert [transfer.volume_nl for transfer in assignment.transfers] == [
        Decimal("10.0"), Decimal("7.5"), Decimal("7.5")
    ]
    assert sum(transfer.volume_nl for transfer in assignment.transfers) == Decimal("25")
    assert editor.volume_error_label.isHidden(), "nothing to correct"

    # The share column names every amount, so it cannot overstate the dose.
    assert editor.assignment_table.item(0, 3).text() == "10.0 nL × 1, 7.5 nL × 2"
    # And there is nothing left to put in the column that raised objections.
    assert editor.assignment_table.item(0, 4).text() == ""

    # An even one still reads the short way.
    editor.volume_input.setValue(30)
    assert editor.assignment_table.item(0, 3).text() == "10.0 nL × 3"

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
    assert "Invalid" in window.status_message_label.message()
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
    editor.protein_input.setText("BRD4!!")
    assert editor.setup_step.protein_error_label.text().startswith("Protein name may contain")
    # A space has its own message, because it is the one the ID used to turn
    # into a hyphen while the record kept it, and the one nobody can see.
    editor.protein_input.setText("BRD4 1")
    assert editor.setup_step.protein_error_label.text() == (
        "Protein name may not contain spaces; use - instead"
    )
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

    # The name as it will be recorded, which is as the experiment ID has it.
    # The ID has always turned a space into a hyphen while the record kept
    # the space, and the spelling with the space is the one that breaks data
    # processing at the beamline, where the name becomes part of a path. The
    # preview has to show what will actually be sent, or it is not a preview.
    editor.protein_input.setText("brd4 domain 1")
    editor.protein_input.editingFinished.emit()
    app.processEvents()

    assert editor.webdb_table.item(0, webdb_columns["protein_name"]).text() == (
        "BRD4-DOMAIN-1"
    )
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


def _finalized_raw_upload_window(
    tmp_path: Path, monkeypatch, wells: tuple[str, ...] = ("A01a",)
):
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
    crystals = tuple(
        SelectedCrystal(
            f"image-key-{well}", "1070", well,
            (CrystalTarget(
                f"target-{well}", Decimal(0), Decimal(0),
                datetime.now(timezone.utc),
            ),),
            SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
        )
        for well in wells
    )
    editor = _start(window, PlanType.RAW_CRYSTAL, crystals)
    editor.set_crystals(crystals)
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
    # "Sample list", because the harvest is the other thing sent to MxLive
    # and the same person reads both lines.
    assert (
        "Sample list uploaded (earlier revision)"
        in editor.webdb_status_label.text()
    )
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


def _wait_for_share_reading(app, window, seconds: float = 5.0) -> None:
    """Pump events until the window has a reading of the shares.

    The reading runs on a worker thread, so a test that looks at anything
    drawn from it has to let it land first.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if window._share_states and not window._share_check_busy:
            app.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("the shares were never read")


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
    # Which instruments are up is read on a worker thread - asking a dropped
    # SMB mount here would freeze the window for as long as it takes to
    # answer - so the line arrives when that reading lands, not before.
    _wait_for_share_reading(app, window)

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
    _leave_every_boundary_to_the_person(window)
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
def test_a_position_past_the_count_is_asked_about_before_it_is_kept(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=1,
    )
    _leave_every_boundary_to_the_person(window)
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    window._handle_image_click(600, 500, Qt.LeftButton)
    # The view holds at an unconfirmed boundary, so the next click lands on
    # the same well - which is the ordinary way a well goes over the count.
    well = window.controller.current_image.image_key
    assert window.controller.session.target_count_for(well) == 1

    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: (asked.append(args[2]), QMessageBox.No)[1],
    )
    window._handle_image_click(610, 510, Qt.LeftButton)
    assert len(asked) == 1, "going past the count is asked about"
    assert "divided between all 2" in asked[0], asked[0]
    # It must not promise a split it cannot know: the dose belongs to the
    # plan, which does not exist while a plate is being reviewed. The
    # sentence that used to say so in words is gone - the operator called
    # the notice too long - so what holds the line now is that no amount
    # appears at all.
    assert "nL" not in asked[0], asked[0]
    assert not re.search(r"\d+\.\d", asked[0]), asked[0]
    # Short enough to be read on an ordinary click, which is when it fires.
    assert len(asked[0]) < 160, f"{len(asked[0])}: {asked[0]}"
    assert asked[0].count("\n\n") == 1, "two short lines"
    assert window.controller.session.target_count_for(well) == 1, "no keeps it off"

    # And the question is only for going past: the first position on a well
    # must not stop to ask.
    window.show_next()
    second = window.controller.current_image.image_key
    window._handle_image_click(600, 500, Qt.LeftButton)
    assert len(asked) == 1, "the first position on a well is not asked about"
    assert window.controller.session.target_count_for(second) == 1

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )
    window._handle_image_click(610, 510, Qt.LeftButton)
    assert window.controller.session.target_count_for(second) == 2, "yes keeps it"
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
    _leave_every_boundary_to_the_person(window)
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
    # An excepthook is called outside sys.exc_info(), so a logging call that
    # takes the traceback from there recorded "NoneType: None" and nothing
    # else. What goes in the operations log is this one grepable line.
    from xtalflow.infrastructure import operations_log

    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        operations_log, "problem",
        lambda name, **fields: recorded.append((name, fields)),
    )

    try:
        raise OSError(112, "Host is down")
    except OSError as error:
        window.handle_unexpected_error(type(error), error, error.__traceback__)

    assert recorded and recorded[0][0] == "unhandled-exception"
    assert recorded[0][1]["outcome"] == "OSError"
    assert "Host is down" in recorded[0][1]["reason"]

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
    _leave_every_boundary_to_the_person(window)
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
    message = window.status_message_label.message()
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
    _leave_every_boundary_to_the_person(window)
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

    def refuse(skip_blocked: bool = False) -> None:
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
    # Held in a name, which is the whole point of the assignment: nothing
    # else here refers to the application, so an unnamed one is collected
    # the moment this line ends and the QPixmap below is built with no
    # QGuiApplication alive - which Qt does not warn about, it aborts. The
    # suite hid it, because a file that ran earlier left an application
    # behind; this file on its own died at this line.
    app = QApplication.instance() or QApplication([])
    app.processEvents()

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


def test_arriving_at_conditions_says_how_the_volume_will_divide(
    tmp_path: Path, monkeypatch
) -> None:
    """Choosing a 3-position well after setting 25 nL used to raise a dialog.

    It offered to change the volume, because a dose that would not divide
    equally stopped the plan being made at all. Nothing is stopped now, so
    nothing interrupts: the note under the field says how the dose will be
    divided, and the number is never changed on anyone's behalf.
    """
    app = QApplication.instance() or QApplication([])
    window, _store, editor = _fragment_plan_window(tmp_path, DEFAULT_SETTINGS)

    # The volume fits what is selected, so there is nothing to say on the
    # way in.
    window.experiments.go_to_step(WorkflowStep.CONDITIONS)
    assert editor.volume_note_label.isHidden()

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

    window.experiments.go_to_step(WorkflowStep.CONDITIONS)

    assert not editor.volume_note_label.isHidden()
    note = editor.volume_note_label.text()
    assert "Divides unevenly in 1 well" in note, note
    assert "clicked first" in note, note
    assert "22.5 nL" in note, "the nearest volume that divides evenly"
    assert editor.volume_input.value() == 25, "never changed on anyone's behalf"
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

    from xtalflow import APP_TITLE, APP_WINDOW_TITLE, __version__
    from xtalflow.build import BUILD, READ

    app = QApplication.instance() or QApplication([])
    # What this holds together is the code and the packaging, which is worth
    # holding: this said 0.1.0 for three months, and the install in the folder
    # named 0.9 still does. It is not the folder's name - that is a deployment
    # slot, and 1.1.0 lives in the one called 1.0.
    packaged = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    )
    assert packaged["project"]["version"] == __version__
    assert APP_TITLE == f"XtalFlow {__version__}"
    # And the commit beside it, because one version number covers every commit
    # the beamline's checkout is moved to: 1.1.0 was two builds that differed
    # in what they refuse. This suite runs from a checkout, so it is here.
    assert BUILD.state == READ, "a checkout must be able to say which commit"
    assert APP_WINDOW_TITLE == f"{APP_TITLE} · {BUILD.short}"

    window = ViewerWindow(RockMakerImageRepository(FIXTURE_ROOT))
    assert window.windowTitle() == APP_WINDOW_TITLE

    # And it survives moving around: the title is rewritten on several screens.
    window.experiments.show_home()
    assert __version__ in window.windowTitle()
    assert BUILD.short in window.windowTitle()
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


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_a_spare_well_without_a_boundary_does_not_empty_a_condition_test(
    tmp_path: Path, monkeypatch
) -> None:
    """Reported from BL-5C: every table and worksheet empty, and no reason given.

    One well picked past what the conditions need, with its boundary not
    confirmed, threw the whole selection away: the Review page said "0 wells"
    and "Select at least one well" to somebody looking at the ones they had
    just picked, and the worksheets were blank.
    """
    from decimal import Decimal

    from xtalflow.domain.condition_test import Additive, ConditionTestDesign, new_series

    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page
    window.workspace_sidebar.start_buttons["solvent_test"].click()
    editor = window.experiments.current_editor
    editor.protein_input.setText("BRD4")
    # Two wells needed, so a third is spare: the same shape as the plate at
    # the beamline, without its thirty-two clicks.
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1", "384PP_DMSO2")
    editor.design = ConditionTestDesign(
        (dmso,), (new_series(dmso.id, (Decimal("10"),), (0,), 2),), Decimal("100")
    )
    editor.refresh_plan()
    assert editor.design.required_crystals == 2
    window.experiments.go_to_step(WorkflowStep.SELECT_WELLS)
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    for _well in range(2):
        window._auto_detect_calibration()
        window._accept_current_calibration()
        window._handle_image_click(600, 500, Qt.LeftButton)
        assert window.show_next()
    # The spare one, placed without confirming the boundary.
    window._handle_image_click(600, 500, Qt.LeftButton)
    window.experiments._sync_current_selection()

    assert editor.wells_left_out == 1
    assert editor.current_plan is not None, "the plan is made from the wells that are ready"
    assert len(editor.current_plan.assignments) == 2
    assert editor.assignment_table.rowCount() == 2
    assert editor.echo_table.rowCount()
    assert editor.shifter_table.rowCount() == 2
    status = window.experiments._experiment_facts(editor)
    assert evaluate_experiment(status).ready_to_finalize
    footer = page.footer_status_label.text()
    assert "1 well left out" in footer, footer
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_a_position_outside_its_well_does_not_keep_it_out_of_bulk_confirming(
    tmp_path: Path, monkeypatch
) -> None:
    """The button said "Confirm detected boundaries (0)" beside a list of wells.

    A position outside the outline is a warning everywhere else in the
    program, because the detected outline is often smaller than the well.
    Treating it as a disqualification here left the one button that confirms
    a plateful of wells refusing to confirm any of them, with no reason
    anybody could see.
    """
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    _leave_every_boundary_to_the_person(window)
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    window._auto_detect_calibration()
    calibration = window.current_calibration
    window._handle_image_click(
        calibration.center_x_px, calibration.center_y_px, Qt.LeftButton
    )
    # A corner of the picture: inside the image, outside the well.
    assert not calibration.contains(5, 5)
    window._handle_image_click(5, 5, Qt.LeftButton)

    assert window.project_controller.valid_unconfirmed_automatic_calibration_count() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window._accept_valid_auto_wells()

    assert window.current_calibration.confirmed
    summaries = window.project_controller.project_target_summaries()
    assert not any(summary.blocks_planning for summary in summaries)
    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_check_wells_walks_past_a_well_that_only_carries_a_warning(
    tmp_path: Path, monkeypatch
) -> None:
    """Reported from BL-5C: "I accepted them all and it will not go on."

    The button goes to the first row of a list that also holds wells which
    are only warned about. Once the top row was a well that had been
    accepted, the button selected the row that was already selected and
    nothing happened at all, however many times it was pressed.
    """
    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=10,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page = window.experiment_page
    # Shown: the dock refreshes nothing while it is not on screen, and this
    # whole flow is about what the dock is showing.
    window.show()
    app.processEvents()
    window.workspace_sidebar.start_buttons[PlanType.RAW_CRYSTAL.value].click()
    editor = window.experiments.current_editor
    editor.protein_input.setText("BRD4")
    page.primary_button.click()
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(False)
    # First well: one position inside, one outside, boundary left unconfirmed.
    window._auto_detect_calibration()
    first = window.current_calibration
    window._handle_image_click(first.center_x_px, first.center_y_px, Qt.LeftButton)
    window._handle_image_click(5, 5, Qt.LeftButton)
    first_address = window._current_well_address
    assert window.show_next()
    # A second well, also waiting to be confirmed.
    window._auto_detect_calibration()
    second = window.current_calibration
    window._handle_image_click(second.center_x_px, second.center_y_px, Qt.LeftButton)
    second_address = window._current_well_address
    window.experiments._sync_current_selection()
    assert editor.wells_needing_attention == 2

    window.experiments._review_target_warnings()
    assert window._current_well_address == first_address
    window._accept_current_calibration()
    window.experiments._sync_current_selection()
    assert editor.wells_needing_attention == 1

    # The accepted well stays in the list, warned about; the button must
    # walk past it to the one still in the way.
    window.experiments._review_target_warnings()

    assert window._current_well_address == second_address
    assert window.accept_calibration_button.isEnabled()
    window.close()
    app.processEvents()


def test_a_renamed_solvent_reaches_mxlive_under_the_name_that_was_typed(
    tmp_path: Path, monkeypatch
) -> None:
    """A beamline user met "Solvent 23%" in MxLive and thought 23 % had been soaked.

    Two things made that name: a name typed but never entered, and a compact
    label that ran the name straight into the percent. Both are checked here,
    through the path a researcher takes.
    """
    from PyQt5.QtTest import QTest
    from xtalflow.domain.condition_test import new_series
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    app = QApplication.instance() or QApplication([])
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    settings = replace(
        DEFAULT_SETTINGS,
        mxlive_base_url="https://mxlive.example",
        mxlive_key_path=key,
        mxlive_ca_bundle=None,
        mxlive_config_path=None,
        source_plate_record_directory=tmp_path / "source-plates",
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", _FakeMxLiveReader)

    window.workspace_sidebar.start_buttons["solvent_test"].click()
    editor = window.experiments.current_editor
    assert isinstance(editor, ConditionTestEditor)
    editor.protein_input.setText("BRD4")
    editor.drop_volume_input.setValue(600)
    editor.replicates_input.setValue(1)
    # One series, two conditions: 0 % and 3 %, both at 0 min.
    editor.design = replace(
        editor.design,
        series=(new_series(
            editor.design.additives[0].id, (Decimal("0"), Decimal("3")), (0,), 1
        ),),
    )
    editor.update_additive(
        source_plate="SOLVENT-09", source_well="a1", source_plate_type="384PP_DMSO2"
    )
    editor.add_series_button.click()
    # Hyphenated: the name a person still has to fix cannot be one the field
    # refuses, and this one reaches MxLive if nobody changes it.
    assert editor.design.additives[1].name == "Solvent-2"
    assert editor.finalize_warnings()[0].startswith("Still named Solvent-2.")
    # Its own plate and the calibration for glycols, so only the name is at issue.
    editor.update_additive(
        source_plate="SOLVENT-10", source_well="a1", source_plate_type="384PP_AQ_CP"
    )

    # Typed, not entered: no Enter, and the focus stays in the field.
    editor.additive_name_input.selectAll()
    QTest.keyClicks(editor.additive_name_input, "EG-2")
    assert editor.design.additives[1].name == "Solvent-2"

    window.experiments.go_to_step(WorkflowStep.REVIEW)
    assert editor.design.additives[1].name == "EG-2"
    assert editor.finalize_warnings() == ()

    required = editor.design.required_crystals
    now = datetime.now(timezone.utc)
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2070", f"{'ABCDEFGH'[index // 12]}{index % 12 + 1:02d}a",
            (CrystalTarget(f"target-{index}", Decimal(0), Decimal(0), now + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id, f"/rmserver/{index}.jpg",
        )
        for index in range(required)
    )
    window.experiments._sync_editor_selection(editor, crystals)
    assert editor.current_plan is not None, editor.error_label.text()

    unexpected_dialogs = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: unexpected_dialogs.append(("warning", args[2])),
    )
    revision = window.experiments._finalize_plan(editor)
    assert revision is not None, unexpected_dialogs

    class FakeWriter:
        sent: list = []

        def __init__(self, *args, **kwargs):
            pass

        def upload_labworks(self, records):
            FakeWriter.sent.extend(records)
            return {"created": len(records)}

    asked = []
    monkeypatch.setattr("xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", FakeWriter)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.Yes
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)
    # A name that cannot be an identifier is said in this dialog, which is
    # the last place it can be said: the names line shows three, so a bad
    # one among ninety-six would never appear in it. Stubbed rather than
    # provoked, because what is checked here is that the dialog carries it.
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator._unwritable_name_note",
        lambda records: "ONE OF THESE CANNOT BE WRITTEN\n\n",
    )
    window.experiments._upload_plan_labworks(editor)

    assert "ONE OF THESE CANNOT BE WRITTEN" in asked[-1]

    # The names are shown before the upload, and they are what is sent.
    assert (
        "Named in MxLive: NoDMSO-0min-r1, DMSO_3p-0min-r1, NoEG-2-0min-r1, and 1 more"
        in asked[-1]
    )
    names = {record["soak_id"] for record in FakeWriter.sent}
    assert "EG-2_10p-0min-r1" in names
    assert not any("EG210%" in name for name in names)
    # And no space anywhere in what was sent: that is what broke processing.
    assert not any(" " in name for name in names)
    assert all(" " not in record["protein_name"] for record in FakeWriter.sent)
    assert unexpected_dialogs == []
    window.close()
    app.processEvents()


def _harvest_ready(tmp_path: Path, monkeypatch, *, second_pick: str = "failed"):
    """A plate uploaded, its worksheet returned by the SHIFTER, nothing checked.

    Where every harvest question starts: the records exist on MxLive, the
    file has come back rewritten in place, and no harvest has been read or
    sent. ``second_pick`` is whether the second crystal came back picked or
    failed, which is the difference between a plate with one crystal to
    record and a plate with two.
    """
    app = QApplication.instance() or QApplication([])
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    settings = replace(
        DEFAULT_SETTINGS,
        mxlive_base_url="https://mxlive.example",
        mxlive_key_path=key,
        mxlive_ca_bundle=None,
        mxlive_config_path=None,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)

    held: list = []

    class Holds(_FakeMxLiveReader):
        def labworks(self, experiment_id):
            return tuple(held)

    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", Holds
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", _RecordingWriter
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dialogs = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            lambda *args, _name=name, **kwargs: dialogs.append((_name, args[2])),
        )

    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "1070", f"A0{index + 1}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0),
                           datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
        )
        for index in range(2)
    )
    editor = _start(window, PlanType.RAW_CRYSTAL, crystals)
    editor.set_crystals(crystals)
    editor.protein_input.setText("BRD4")

    window.experiments.go_to_step(WorkflowStep.REVIEW)
    window.experiment_page.primary_button.click()          # Finalize r1
    window.experiment_page.primary_button.click()           # Save worksheets
    revision = editor.last_revision
    step = editor.worksheets_step

    # Before the upload there is nothing on MxLive for a harvest to join, and
    # the line says which of the two is missing.
    assert not step.harvest_check_button.isEnabled()
    assert step.harvest_status_label.text() == (
        "Upload this experiment's sample list to MxLive first"
    )

    window.experiments._upload_plan_labworks(editor)
    assert _RecordingWriter.posted, dialogs
    held.extend(
        MxLiveLabwork.from_mapping(dict(payload, harvest_time=None, puck_name=None))
        for payload in _RecordingWriter.posted[-1]
    )
    assert step.harvest_check_button.isEnabled()

    # The SHIFTER rewrites our file in place, dropping the header. One crystal
    # was picked, the other failed.
    path = store.audit.list_worksheet_exports(revision.id)[0].path_for("shifter1")
    rows = Path(path).read_text(encoding="utf-8").splitlines()[1:]
    picked = rows[0].split(",")
    picked[6:13] = [
        "OK: Clear:No comment", "XTal-1", "2026-11-17 17:16:55.4",
        "2026-11-17 17:17:00.0", "00:00:04.6", "5C-0001", "1",
    ]
    failed = rows[1].split(",")
    failed[6:13] = (
        [
            "FAIL: Clear:Melted", "", "2026-11-17 17:17:03.6",
            "2026-11-17 17:17:07.4", "00:00:03.8", "", "",
        ]
        if second_pick == "failed"
        else [
            "OK: Clear:No comment", "XTal-2", "2026-11-17 17:17:03.6",
            "2026-11-17 17:17:07.4", "00:00:03.8", "5C-0001", "2",
        ]
    )
    Path(path).write_text(
        ",".join(picked) + "\r\n" + ",".join(failed) + "\r\n", encoding="utf-8"
    )

    return app, window, store, editor, step, held, dialogs


def test_the_harvest_of_an_uploaded_experiment_is_read_and_shown(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole read path, from the save's own audit to the table.

    Nothing in this is sent: the files are opened read-only, MxLive is only
    asked what it holds, and the section exists so a person can see where a
    harvest stands before anything irreversible is offered.
    """
    app, window, _store, _editor, step, _held, _dialogs = _harvest_ready(
        tmp_path, monkeypatch
    )

    step.harvest_check_button.click()

    assert step.harvest_status_label.text() == "1 harvest to record · 1 picks failed"
    # Not named: a plate sent under one experiment ID has nothing to
    # distinguish, and the name would only be in the way. It is named when
    # there are two of them - see the late-harvest split.
    assert ":" not in step.harvest_status_label.text()
    table = step.harvest_table
    assert table.rowCount() == 2
    assert [table.item(0, column).text() for column in (1, 2, 4, 5, 6, 8)] == [
        "1070", "A01a", "2026-11-17 17:17:00.0", "5C-0001", "1", "harvest will be recorded",
    ]
    # This plate went out whole, so the experiment ID is the same on every
    # row - said once in the line above, and its column kept out of the way.
    assert table.isColumnHidden(0)
    # The instrument's own words on the failed pick, shown and never sent.
    assert table.item(1, 8).text() == "pick failed; not sent · FAIL: Clear:Melted"
    assert table.item(1, 5).text() == ""
    # What no check can catch is said out loud, since a harvest write cannot
    # read a record's status back before replacing it.
    note = step.harvest_note_label.text()
    assert "status" in note and "Active" in note
    assert "attachment" in note
    window.close()
    app.processEvents()


def test_the_worksheets_step_never_asks_the_instrument_folders_anything(
    tmp_path: Path, monkeypatch
) -> None:
    """Refreshing the step must not touch the shares, however many times it runs.

    Asking a dropped SMB mount whether it is a folder answers only after the
    best part of a minute, and the step refreshes after every edit. Doing it
    here froze the window for minutes at a time with two instruments away -
    long enough that macOS left the progress sheet of whatever ran last
    stranded on screen, a small box with a striped bar and nothing else.

    What is up is read on a worker thread for the panel's lamps, and the step
    draws from that reading.
    """
    app = QApplication.instance() or QApplication([])
    (tmp_path / "echo650").mkdir()
    (tmp_path / "shifter1").mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "missing-shifter2"
        ),
        create_missing_instrument_roots=False,
    )
    window, _store, editor = _fragment_plan_window(tmp_path, settings)
    assert window.experiments._finalize_plan(editor) is not None
    window.experiments.go_to_step(WorkflowStep.WORKSHEETS)
    _wait_for_share_reading(app, window)
    roots = tuple(item.output_directory for item in settings.instruments)

    touched = []
    real_is_dir = Path.is_dir

    def counting_is_dir(self):
        if any(root == self or root in self.parents for root in roots):
            touched.append(str(self))
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", counting_is_dir)
    for _ in range(5):
        window.experiments._refresh_worksheets_step(editor)
        app.processEvents()

    assert touched == []
    # And it still says which one is away, from the reading that already ran.
    step = editor.worksheets_step
    where = {
        step.instrument_table.item(row, 0).text():
            step.instrument_table.item(row, 3).text()
        for row in range(step.instrument_table.rowCount())
    }
    assert where["SHIFTER 2"].startswith("not connected · will wait in ")
    assert where["SHIFTER 1"].startswith(str(tmp_path / "shifter1"))
    window.close()
    app.processEvents()


def test_a_folder_that_is_not_a_mounted_share_counts_as_away(tmp_path: Path) -> None:
    """The beamline's own case: the folder is there, the share behind it is not.

    An unmounted mountpoint is an ordinary empty directory, so a worksheet
    written into it is accepted and then vanishes when the share returns. A
    site that asked for mounted shares must be told the instrument is away;
    one that did not is looking at a local folder that works.
    """
    app = QApplication.instance() or QApplication([])
    for name in ("echo650", "shifter1", "shifter2"):
        (tmp_path / name).mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        # Named here rather than taken from the defaults, which point at a
        # fixture that is gitignored: on a fresh clone it is not there, the
        # image share reads as missing, and this test fails for a reason that
        # has nothing to do with what it is about.
        rmserver_root=_share_with_one_plate(tmp_path / "images"),
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        require_network_instrument_mounts=True,
        require_network_image_root=False,
        create_missing_instrument_roots=False,
    )
    window = ViewerWindow(
        RockMakerImageRepository(settings.rmserver_root),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"), settings=settings,
    )
    _wait_for_share_reading(app, window)

    # Every folder exists; none of them is a mounted share.
    assert all(state.exists for state in window._share_states)
    assert set(window.shares_found_away()) == {"echo650", "shifter1", "shifter2"}

    # The same machine, by a site that never asked for mounted shares.
    window.settings = replace(settings, require_network_instrument_mounts=False)
    assert window.shares_found_away() == {}
    window.close()
    app.processEvents()


def test_a_condition_test_opens_without_asking_the_shared_record(
    tmp_path: Path, monkeypatch
) -> None:
    """The record lives under the ECHO folder, which is a share that can drop.

    Reading it here is done on the window's own thread, so one look at a
    dropped SMB mount holds the window for the best part of a minute - and
    opening a condition test takes several. Whether it can be read comes from
    the reading the share lamps make on a worker thread instead.
    """
    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        source_plate_record_directory=None,
        instruments=standard_instruments(
            tmp_path / "gone-echo", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        require_network_instrument_mounts=True,
        require_network_image_root=False,
        create_missing_instrument_roots=False,
    )
    window = ViewerWindow(
        RockMakerImageRepository(_share_with_one_plate(tmp_path / "images")),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"), settings=settings,
    )
    _wait_for_share_reading(app, window)
    assert "echo650" in window.shares_found_away()

    touched = []
    real_is_dir, real_glob = Path.is_dir, Path.glob
    echo = tmp_path / "gone-echo"

    def counting_is_dir(self):
        if echo == self or echo in self.parents:
            touched.append(f"is_dir {self}")
        return real_is_dir(self)

    def counting_glob(self, pattern):
        if echo == self or echo in self.parents:
            touched.append(f"glob {self}")
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "is_dir", counting_is_dir)
    monkeypatch.setattr(Path, "glob", counting_glob)

    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    app.processEvents()
    editor.refresh_plate_records()
    app.processEvents()
    # A named plate is the other way in: its own entries are looked up, not
    # just the list of plates that have a record at all.
    assert editor._plate_entries("SP-2026-01") is None
    assert editor._plate_entries("SP-2026-01", fresh=True) is None
    assert editor._shared_plates() is None

    assert touched == []
    # And the user is told, so nobody books wells against a record nobody read.
    assert "cannot be reached" in editor.record_note_label.text()
    assert "fresh source plate" in editor.record_note_label.text()
    window.close()
    app.processEvents()


def test_the_record_asks_about_this_sites_own_echo(tmp_path: Path, monkeypatch) -> None:
    """Whether the record can be read is asked of the ECHO this site configured.

    The record is found by what the instrument reads, so any id works; asking
    the lamps by the BL-5C id instead would be asking about a share no
    reading ever covers, which answers "not mounted" for ever. The record
    would then be away for the whole session with the share sitting mounted:
    every plate reading as untouched, and a fresh plate taken each time.
    """
    from xtalflow.domain.instruments import SHIFTER_1, SHIFTER_2, WorksheetKind
    from xtalflow.settings import InstrumentDestination

    app = QApplication.instance() or QApplication([])
    echo = tmp_path / "echo-by-another-name"
    (echo / "source-plates").mkdir(parents=True)
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        source_plate_record_directory=None,
        instruments=(
            # Same instrument, a name this site chose.
            InstrumentDestination("echo-01", WorksheetKind.ECHO, echo),
            InstrumentDestination(SHIFTER_1, WorksheetKind.SHIFTER, tmp_path / "shifter1"),
            InstrumentDestination(SHIFTER_2, WorksheetKind.SHIFTER, tmp_path / "shifter2"),
        ),
        require_network_instrument_mounts=True,
        require_network_image_root=False,
        create_missing_instrument_roots=False,
    )
    # The ECHO folder is a mounted share, under whatever it is called here.
    monkeypatch.setattr(
        mounts, "is_network_mount", lambda path, table=None: echo == path or echo in path.parents
    )
    window = ViewerWindow(
        RockMakerImageRepository(_share_with_one_plate(tmp_path / "images")),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"), settings=settings,
    )
    _wait_for_share_reading(app, window)

    assert window.share_is_mounted("echo-01")
    record = window.experiments._source_plate_record()
    assert record is not None

    # The question the editor is handed. False here would mean a record that
    # is there and readable, reported away until the application restarts.
    assert window.experiments._record_is_reachable(record)
    window.close()
    app.processEvents()


def test_a_reading_that_cannot_be_started_does_not_end_the_readings(
    tmp_path: Path, monkeypatch
) -> None:
    """A thread that will not start must not leave the door shut behind it.

    The busy flag is what stops two readings overlapping. Set and not
    cleared, it turns one failure to start a thread - out of threads, or an
    interpreter shutting down - into no share reading for the rest of the
    session: the lamps stand at whatever they last said, and everything that
    asks them, the worksheets step and the shared source plate record, is
    answered from a reading nothing on screen dates.
    """
    import threading

    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        require_network_image_root=False,
    )
    window = ViewerWindow(
        RockMakerImageRepository(_share_with_one_plate(tmp_path / "images")),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"), settings=settings,
    )
    _wait_for_share_reading(app, window)
    before = window._share_states
    assert before

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    window._check_shares()

    assert not window._share_check_busy
    # The lamps keep the last reading rather than going blank over this.
    assert window._share_states == before

    # And the next reading happens, once threads can be started again.
    monkeypatch.undo()
    window._share_states = ()
    window._check_shares()
    _wait_for_share_reading(app, window)

    assert window._share_states
    window.close()
    app.processEvents()


def test_a_stale_reading_cannot_tip_held_wells_into_an_empty_mountpoint(
    tmp_path: Path, monkeypatch
) -> None:
    """The record's own rule is what holds when the lamps are out of date.

    Catching the record up is gated on the share reading, which is up to a
    minute old. If the share dropped inside that minute the reading still
    says mounted, and an unmounted mountpoint is an ordinary empty directory:
    merge_held would write the held entries into it and delete the only other
    copy, and both would vanish when the share came back.

    So the record asks for itself before it writes. Dropping the rule from
    the copy the merge runs through would put the entries in the mountpoint.
    """
    app = QApplication.instance() or QApplication([])
    echo = tmp_path / "echo650"
    echo.mkdir()
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        source_plate_record_directory=None,
        instruments=standard_instruments(echo, tmp_path / "s1", tmp_path / "s2"),
        require_network_instrument_mounts=True,
        require_network_image_root=False,
        create_missing_instrument_roots=False,
    )
    window = ViewerWindow(
        RockMakerImageRepository(_share_with_one_plate(tmp_path / "images")),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"), settings=settings,
    )
    # Entries taken while the record was away, waiting to go onto it.
    held = SourcePlateRecord(
        record_directory(tmp_path / "pending"), create_root=True
    )
    held.append("SOLVENT-01", [
        PlateRecordEntry(
            USED, "Offline-01", ("B1",), datetime.now(timezone.utc), "jjh"
        ),
    ])
    waiting = sorted(path.name for path in held.directory.glob("*.log"))
    assert waiting

    # The reading says the ECHO share is mounted. It is not; it is a folder.
    window._share_states = tuple(
        replace(state, mounted=True, detail="mounted")
        if state.key == "echo650" else state
        for state in mounts.share_states(settings)
    )
    assert window.share_is_mounted("echo650")

    window.experiments._catch_up_source_record()

    # Nothing was written into the mountpoint, and the held copies are intact.
    assert sorted(path.name for path in held.directory.glob("*.log")) == waiting
    assert list(record_directory(echo).glob("*.log")) == []
    window.close()
    app.processEvents()


def test_a_library_id_that_cannot_be_an_identifier_stops_the_plan(
    tmp_path: Path,
) -> None:
    """Nobody types a compound ID, so refusing typed names never reached it.

    It becomes the soak_id in MxLive, where it is read for years and cannot
    be corrected, and it goes into the ECHO worksheet too. Refused where the
    plan is composed, naming the row, so the file or the row range can be
    fixed - and not in the library or the fragment, which are rebuilt when a
    finalized revision is restored, where a new rule would refuse to record
    work already done at the bench.
    """
    app = QApplication.instance() or QApplication([])
    header = (
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
    )
    (tmp_path / "hand-made.csv").write_text(
        header
        + "Vendor,Lib,1,Z1001,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n"
        + "Vendor,Lib,2,50%/DMSO,C2H6O,46.07,CCO,100,DMSO,SRC-1,A02\n",
        encoding="utf-8",
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), store,
        settings=replace(DEFAULT_SETTINGS, fragment_library_directory=tmp_path),
    )
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "1070", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(2)
    )
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals)
    editor.library_input.setCurrentIndex(1)

    assert editor.current_plan is None
    message = editor.error_label.text()
    assert "Row 2 of hand-made" in message
    assert "compound ID" in message
    assert "50%/DMSO" in message
    assert "letters, numbers, - and _" in message
    assert editor.conditions_error() is not None

    # Leaving the row out is a way forward, and the message says so.
    editor.rows_input.setText("1")
    editor.rows_input.editingFinished.emit()

    assert editor.current_plan is not None, editor.error_label.text()
    window.close()
    app.processEvents()


def test_one_file_nobody_can_read_does_not_hide_the_libraries(
    tmp_path: Path,
) -> None:
    """A library that is not in the list is said, by name and with a reason.

    Two failures met here. A file Excel on a Korean Windows saved raised
    UnicodeDecodeError past the loop that lists libraries, so fragment
    screening had no libraries at all - not the bad one, not the good ones.
    And a file refused for any reason was passed over silently, so the
    library somebody came to use was simply not there, with nothing said
    anywhere in the window.
    """
    app = QApplication.instance() or QApplication([])
    header = (
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
    )
    (tmp_path / "good.csv").write_text(
        header + "Vendor,Lib,1,Z1,C2H6O,46.07,CCO,100,DMSO,SRC-1,A01\n",
        encoding="utf-8",
    )
    (tmp_path / "korean-excel.csv").write_bytes(
        (header + "엔아민,Lib,1,Z2,C2H6O,46.07,CCO,100,DMSO,SRC-1,A02\n").encode("cp949")
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), store,
        settings=replace(DEFAULT_SETTINGS, fragment_library_directory=tmp_path),
    )
    crystal = SelectedCrystal(
        "image", "1070", "A01a",
        (CrystalTarget("target", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
        SWISSCI_MIDI_3_LENS.id,
    )
    editor = _start(window, PlanType.FRAGMENT_SCREENING, (crystal,))

    # The good one is there, and usable.
    assert editor.library_input.count() == 2
    assert "good.csv" in editor.library_input.itemText(1)

    note = editor.library_label.text()
    assert "korean-excel.csv" in note
    assert "not UTF-8 text" in note
    assert not editor.library_label.isHidden()

    # Still said once a good library is chosen: the one somebody came for
    # may be the one that is missing.
    editor.library_input.setCurrentIndex(1)

    assert "korean-excel.csv" in editor.library_label.text()
    assert editor.current_plan is not None, editor.error_label.text()
    window.close()
    app.processEvents()


def _fragment_library_file(path: Path, rows: int = 6) -> None:
    path.write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        + "".join(
            f"Vendor,Lib,{index},Z{index:04d},C2H6O,46.07,CCO,100,DMSO,SRC-1,A{index:02d}\n"
            for index in range(1, rows + 1)
        ),
        encoding="utf-8",
    )


def test_a_moved_library_folder_keeps_the_rows_that_were_chosen(
    tmp_path: Path, monkeypatch
) -> None:
    """A library is remembered by its full path, and paths move.

    Rename the folder and the draft pointed at nothing: the list offered
    the file twice, once as itself and once as "Unavailable", with nothing
    saying they were the same. Choosing the real one reset the rows - 2-4
    became 1-6 - so finalizing again would send six fragments to the ECHO
    where three were planned, with nothing said about it.

    Found by name now, which is not proof it is the same file, so where it
    came from is said and the person decides.
    """
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "libs"
    first.mkdir()
    _fragment_library_file(first / "set.csv")
    database = tmp_path / "reviews.sqlite3"

    def viewer(folder: Path) -> ViewerWindow:
        return ViewerWindow(
            RockMakerImageRepository(tmp_path), SQLiteReviewStore(database),
            settings=replace(DEFAULT_SETTINGS, fragment_library_directory=folder),
        )

    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "1070", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(3)
    )
    window = viewer(first)
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "FragTest")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    editor.rows_input.setText("2-4")
    editor.rows_input.editingFinished.emit()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_and_continue(editor)

    assert revision is not None
    window.close()
    app.processEvents()

    # The beamline moves the library folder between runs.
    second = tmp_path / "libs-2026"
    second.mkdir()
    (first / "set.csv").rename(second / "set.csv")
    reopened = viewer(second)
    recent = reopened.experiments._recent_experiments()
    restored = reopened.experiments.resume_experiment(
        recent[0].workspace_id, recent[0].plan_id
    )

    assert restored.rows_input.text() == "2-4"
    # One entry, not the file and a dead twin of it.
    assert [
        restored.library_input.itemText(index)
        for index in range(restored.library_input.count())
    ] == ["Select imported library…", "set.csv · 6 rows"]
    assert restored.library is not None
    note = restored.library_label.text()
    assert "set.csv was recorded in" in note
    assert str(second) in note
    assert "Check it is the same library before finalizing again." in note
    reopened.close()
    app.processEvents()


def test_a_library_that_is_really_gone_says_so_not_that_none_was_chosen(
    tmp_path: Path, monkeypatch
) -> None:
    """"Choose a fragment library." for an experiment that chose one.

    Said with a finalized revision sitting on the Review step, which is
    the one thing the researcher needs to know is still intact: it was
    built from the library's rows and is kept whole in its own snapshot.
    """
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "libs"
    first.mkdir()
    _fragment_library_file(first / "set.csv")
    database = tmp_path / "reviews.sqlite3"

    def viewer(folder: Path) -> ViewerWindow:
        return ViewerWindow(
            RockMakerImageRepository(tmp_path), SQLiteReviewStore(database),
            settings=replace(DEFAULT_SETTINGS, fragment_library_directory=folder),
        )

    crystals = (
        SelectedCrystal(
            "image", "1070", "A01a",
            (CrystalTarget("t", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        ),
    )
    window = viewer(first)
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "FragGone")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_and_continue(editor)

    assert revision is not None
    window.close()
    app.processEvents()

    (first / "set.csv").unlink()
    reopened = viewer(first)
    recent = reopened.experiments._recent_experiments()
    restored = reopened.experiments.resume_experiment(
        recent[0].workspace_id, recent[0].plan_id
    )
    restored.refresh_plan()

    message = restored.error_label.text()
    assert "set.csv is not in" in message
    assert f"{revision.experiment_id} r1" in message
    assert "worksheets were built from it and are unaffected" in message
    assert "would replace the fragments" in message
    assert "Choose a fragment library." not in message
    reopened.close()
    app.processEvents()


def test_held_worksheets_keep_clear_of_the_names_already_on_the_instruments(
    tmp_path: Path, monkeypatch
) -> None:
    """The names an earlier save used come from our own log, not the shares.

    r1 is saved with the shares up. The shares go away, the plan changes,
    r2 is finalized, and r2's worksheets wait in a folder of ours - which
    knows nothing of what r1 left on the instruments, so all three of
    r2's files were named exactly what r1's are. A held worksheet is
    delivered by hand, so r1's files were overwritten without a word.

    The log is in our own database and answers with every share down,
    which is the only case where this happens.
    """
    app = QApplication.instance() or QApplication([])
    echo, first, second = (
        tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
    )
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        pending_worksheet_directory=tmp_path / "pending",
        instruments=standard_instruments(echo, first, second),
        # As at the beamline: the folders are the shares' own, and nothing
        # of ours creates them.
        create_missing_instrument_roots=False,
    )
    for share in (echo, first, second):
        (share / getpass.getuser()).mkdir(parents=True)
    window, store, editor = _fragment_plan_window(tmp_path, settings)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    window.experiment_page.primary_button.click()
    window.experiment_page.primary_button.click()

    first_revision = editor.last_revision
    saved = store.audit.list_worksheet_exports(first_revision.id)
    on_the_instruments = {
        Path(output.path).name for output in saved[0].outputs
    }

    assert on_the_instruments == {f"{first_revision.experiment_id}.csv"}
    assert window.experiments._worksheet_names_already_used(editor.plan_id) == (
        on_the_instruments
    )

    # The shares go away, and the plan is finalized again.
    editor.volume_input.setValue(30.0)
    window.experiments.go_to_step(WorkflowStep.REVIEW)
    real_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path, "is_dir",
        lambda self: False
        if any(self in (share, share / getpass.getuser()) for share in (echo, first, second))
        else real_is_dir(self),
    )
    window.experiment_page.primary_button.click()
    window.experiments._save_plan_worksheets(editor)

    second_revision = editor.last_revision

    assert second_revision.revision == 2
    held = store.audit.list_worksheet_exports(second_revision.id)
    names = {Path(output.path).name for output in held[-1].outputs}

    assert names == {f"{second_revision.experiment_id}_01.csv"}
    assert not names & on_the_instruments
    window.close()
    app.processEvents()


def test_mxlive_history_is_not_shut_by_a_thread_that_will_not_start(
    tmp_path: Path, monkeypatch
) -> None:
    """The same shape as the share reading, which was fixed first.

    A busy flag raised before the thread starts, and cleared only by the
    reading finishing, turns one failure to start a thread into no MxLive
    history for the rest of the session - with "Reading 2026 from MxLive…"
    still on the page saying one is underway.

    Crystal scoring has the same two lines, and no test: it needs the ONNX
    model and the image fixture, and neither is in a clone.
    """
    import threading

    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=replace(DEFAULT_SETTINGS, require_network_image_root=False),
    )
    coordinator = window.experiments
    from xtalflow.infrastructure.mxlive_config import resolve_mxlive_account

    coordinator.mxlive_account = resolve_mxlive_account(
        None, os_username="tester", base_url="https://mxlive.example",
        key_path=tmp_path / "keys.dsa",
    )

    # Standing in for the client so nothing here asks MxLive anything: the
    # reading never starts, which is the case under test.
    monkeypatch.setattr(coordinator, "_mxlive_reader", lambda: object())

    def refuse(self):
        raise RuntimeError("can't start new thread")

    # The window reads the shares on a thread that starts threads of its
    # own. Let that finish first, and undo immediately after, so refusing
    # every thread in the process does not leave it raising into pytest.
    _wait_for_share_reading(app, window)
    monkeypatch.setattr(threading.Thread, "start", refuse)
    try:
        coordinator._read_labworks([2026])
    finally:
        monkeypatch.undo()

    # Said, not swallowed: the page stops claiming a reading is underway.
    #
    # ``message()`` and not ``text()``: the label elides to its own width, so
    # the server read 'Beamline progress unavailabl…' and this failed there
    # on nothing but the font. Narrowed here so a machine that elides is what
    # runs, rather than something nobody tests on.
    coordinator.window.status_message_label.setFixedWidth(90)
    coordinator.window.status_message_label._refresh_text()

    assert coordinator.window.status_message_label.text() != (
        coordinator.window.status_message_label.message()
    )
    assert coordinator.window.status_message_label.message().startswith(
        "Beamline progress unavailable"
    )

    # The door is open again, which is the whole point: the flag is what
    # refuses the next reading. Not started here, because starting one asks
    # MxLive over the network.
    assert not coordinator._beamline_busy
    window.close()
    app.processEvents()


def test_a_finalized_revision_is_delivered_even_when_the_editor_cannot_compose(
    tmp_path: Path, monkeypatch
) -> None:
    """A plan that cannot be composed is not a different plan.

    Worksheets and upload both sat behind "the editor composes exactly the
    finalized snapshot". A library that moved, or a rule tightened since,
    leaves the editor composing nothing - and nothing was read as a
    difference, so a finalized experiment could not have its worksheets
    saved or its records uploaded, while the step said in so many words
    that its worksheets were unaffected. Its liquid may already be in the
    wells by then.

    A plan the editor *can* compose and that differs must still be
    refused, which is the second half of this test.
    """
    app = QApplication.instance() or QApplication([])
    libs = tmp_path / "libs"
    libs.mkdir()
    _fragment_library_file(libs / "set.csv")
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
        fragment_library_directory=libs,
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), store, settings=settings
    )
    crystals = (
        SelectedCrystal(
            "image", "1070", "A01a",
            (CrystalTarget("t", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        ),
    )
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "FragStrand")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_and_continue(editor)

    assert revision is not None
    assert window.experiments._revision_is_still_the_plan(editor)

    # The plan changes, and is not finalized again: refused, as before.
    editor.volume_input.setValue(30.0)

    assert editor.current_plan is not None
    assert not window.experiments._revision_is_still_the_plan(editor)

    editor.volume_input.setValue(25.0)

    # The library goes away. The editor can compose nothing at all.
    (libs / "set.csv").unlink()
    window.experiments._refresh_fragment_library_choices()
    editor.refresh_plan()

    assert editor.current_plan is None
    assert editor.plan_snapshot() is None
    assert window.experiments._revision_is_still_the_plan(editor)

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text, *args, **kwargs: said.append((title, text)),
    )
    window.experiments._save_plan_worksheets(editor)

    assert not said, said
    exports = store.audit.list_worksheet_exports(revision.id)
    assert [event.status for event in exports] == ["succeeded"]
    assert Path(exports[0].path_for("echo650")).is_file()

    # And the upload, which is the other half of what the step promises:
    # "r1 and its worksheets were built from it and are unaffected" was
    # printed while both were refused.
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"a key")
    from xtalflow.infrastructure.mxlive_config import resolve_mxlive_account

    account = resolve_mxlive_account(
        None, os_username="fbdd", base_url="https://mxlive.example", key_path=key,
    )
    window.experiments.mxlive_account = account
    editor.set_mxlive_account(account)
    window.experiments._sync_webdb_upload_state(editor)

    assert editor.webdb_upload_button.isEnabled()
    window.close()
    app.processEvents()


def test_a_library_folder_that_moved_leaves_the_revision_finalized(
    tmp_path: Path, monkeypatch
) -> None:
    """Where a library was read from is not part of the plan.

    ``library_id`` is a path in the snapshot, so the same file in a renamed
    folder read as a changed plan: the revision showed as a draft and
    refused to deliver. It stays in the snapshot - taking it out would
    change the canonical JSON of every revision already stored - and is
    left out of the comparison instead.
    """
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "libs"
    first.mkdir()
    _fragment_library_file(first / "set.csv")
    database = tmp_path / "reviews.sqlite3"

    def viewer(folder: Path) -> ViewerWindow:
        return ViewerWindow(
            RockMakerImageRepository(tmp_path), SQLiteReviewStore(database),
            settings=replace(DEFAULT_SETTINGS, fragment_library_directory=folder),
        )

    crystals = (
        SelectedCrystal(
            "image", "1070", "A01a",
            (CrystalTarget("t", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        ),
    )
    window = viewer(first)
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "FragMoved")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_and_continue(editor)

    assert revision is not None
    assert editor.lifecycle_label.text() == "Finalized r1"
    window.close()
    app.processEvents()

    second = tmp_path / "libs-2026"
    second.mkdir()
    (first / "set.csv").rename(second / "set.csv")
    reopened = viewer(second)
    recent = reopened.experiments._recent_experiments()
    restored = reopened.experiments.resume_experiment(
        recent[0].workspace_id, recent[0].plan_id
    )
    reopened.experiments._sync_editor_selection(restored, crystals)

    assert restored.current_plan is not None, restored.error_label.text()
    assert restored.plan_snapshot() != restored.last_revision_snapshot
    assert reopened.experiments._revision_is_still_the_plan(restored)

    # "Draft · saving…" is the autosave the selection kicked off; let it land.
    reopened.experiments._persist_draft(restored)

    assert restored.lifecycle_label.text() == "Finalized r1"
    reopened.close()
    app.processEvents()


def test_a_solvent_test_finalized_with_a_space_in_a_name_is_still_delivered(
    tmp_path: Path, monkeypatch
) -> None:
    """Names could hold a space until a937f57; designs saved then still exist.

    The beamline asked for the space to be refused because a solvent name
    becomes a sample name and then a data file name, and a space in a path
    loses the data. Refusing it is right, and refusing it everywhere would
    have been wrong: a design finalized with "Solvent 2" cannot compose a
    plan any more, so without care its worksheets could not be saved and its
    records could not be uploaded - for an experiment whose drops have
    already been soaked.

    The name is kept exactly as it was finalized. What goes out is what went
    out then, because it is rebuilt from the snapshot and not from today's
    rules.
    """
    from xtalflow.domain.condition_test import new_series
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    app = QApplication.instance() or QApplication([])
    settings = replace(
        DEFAULT_SETTINGS,
        worksheet_staging_directory=tmp_path / "staging",
        instruments=standard_instruments(
            tmp_path / "echo650", tmp_path / "shifter1", tmp_path / "shifter2"
        ),
        create_missing_instrument_roots=True,
        source_plate_record_directory=tmp_path / "source-plates",
    )
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store, settings=settings)
    window.workspace_sidebar.start_buttons["solvent_test"].click()
    editor = window.experiments.current_editor

    assert isinstance(editor, ConditionTestEditor)
    editor.protein_input.setText("BRD4")
    editor.drop_volume_input.setValue(600)
    editor.replicates_input.setValue(1)
    editor.design = replace(
        editor.design,
        series=(new_series(
            editor.design.additives[0].id, (Decimal("0"), Decimal("3")), (0,), 1
        ),),
    )
    editor.update_additive(
        source_plate="SOLVENT-09", source_well="a1", source_plate_type="384PP_DMSO2"
    )

    # The name a design saved before the rule carries. Put into the design
    # the way a stored one arrives, because the field refuses it now.
    editor.design = replace(
        editor.design,
        additives=(replace(editor.design.additives[0], name="Solvent 2"),),
    )
    now = datetime.now(timezone.utc)
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2070",
            f"{'ABCDEFGH'[index // 12]}{index % 12 + 1:02d}a",
            (CrystalTarget(f"target-{index}", Decimal(0), Decimal(0), now + timedelta(seconds=index)),),
            SWISSCI_MIDI_3_LENS.id, f"/rmserver/{index}.jpg",
        )
        for index in range(editor.design.required_crystals)
    )
    window.experiments._sync_editor_selection(editor, crystals)

    # Today's rules refuse it, so there is no plan to finalize - which is the
    # point: nobody can newly send a space to the beamline.
    assert editor.current_plan is None
    assert "Rename Solvent 2" in editor.design_error
    assert "a space (use -)" in editor.design_error

    # Finalized the way one finalized before a937f57 existed, which is how
    # the revisions this is about came to be.
    monkeypatch.setattr(
        "xtalflow.ui.condition_test_editor.characters_not_allowed_in_a_name",
        lambda name: (),
    )
    editor.refresh_plan()

    assert editor.current_plan is not None, editor.design_error
    revision = window.experiments._finalize_plan(editor)

    assert revision is not None
    assert "Solvent 2" in revision.snapshot_json

    # And the rule is back, as it is for everyone opening that experiment now.
    monkeypatch.undo()
    editor.refresh_plan()

    assert editor.current_plan is None
    assert window.experiments._revision_is_still_the_plan(editor)

    said: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text, *args, **kwargs: said.append(text),
    )
    window.experiments._save_plan_worksheets(editor)

    assert not said, said
    exports = store.audit.list_worksheet_exports(revision.id)
    assert [event.status for event in exports] == ["succeeded"]

    # And the record that becomes the sample name - and then the data file
    # name - carries it as it was finalized, through the same compacting it
    # always had. The space was taken out here all along; refusing it at the
    # field is about the name nobody can check afterwards, not about this.
    from xtalflow.application.planning_service import plan_from_snapshot

    records = editor.record_builder()(
        plan_from_snapshot(editor.plan_id, revision.snapshot_json),
        experiment_id=revision.experiment_id,
        protein_name="BRD4", username="tester", account_id="tester",
    )
    names = {record.soak_id for record in records}

    assert names
    assert all("Solvent2" in name for name in names), names
    assert not any(" " in name for name in names), names
    window.close()
    app.processEvents()


def test_a_library_of_the_same_name_that_holds_other_compounds_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """Matching a moved library on its file name is a guess, not proof.

    A file of the same name in another folder can be another library - a
    vendor's second plate, a colleague's copy with rows taken out. The row
    numbers then mean different compounds: rows 2-4 held Z0002-4 when r1
    was finalized and OTHER0002-4 now, and finalizing again would send
    those to the ECHO under the same experiment ID.

    The note used to be set once by the match, so the first Refresh cleared
    it for good, and saving the draft wrote the found path - so the next
    open had nothing left to notice.
    """
    app = QApplication.instance() or QApplication([])

    def library(path: Path, prefix: str) -> None:
        path.write_text(
            "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
            + "".join(
                f"Vendor,Lib,{index},{prefix}{index:04d},C2H6O,46.07,CCO,100,DMSO,"
                f"SRC-1,A{index:02d}\n"
                for index in range(1, 7)
            ),
            encoding="utf-8",
        )

    first = tmp_path / "libs"
    first.mkdir()
    library(first / "set.csv", "Z")
    database = tmp_path / "reviews.sqlite3"

    def viewer(folder: Path) -> ViewerWindow:
        return ViewerWindow(
            RockMakerImageRepository(tmp_path), SQLiteReviewStore(database),
            settings=replace(DEFAULT_SETTINGS, fragment_library_directory=folder),
        )

    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "1070", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(3)
    )
    window = viewer(first)
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "FragOther")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    editor.rows_input.setText("2-4")
    editor.rows_input.editingFinished.emit()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_and_continue(editor)

    assert revision is not None
    assert [
        item.fragment.compound_id for item in editor.current_plan.assignments
    ] == ["Z0002", "Z0003", "Z0004"]
    window.close()
    app.processEvents()

    # Same name, another folder, other compounds.
    second = tmp_path / "libs-2026"
    second.mkdir()
    library(second / "set.csv", "OTHER")
    reopened = viewer(second)
    recent = reopened.experiments._recent_experiments()
    restored = reopened.experiments.resume_experiment(
        recent[0].workspace_id, recent[0].plan_id
    )
    reopened.experiments._sync_editor_selection(restored, crystals)

    assert restored.current_plan is None
    assert "is not the library r1 was finalized from" in restored.error_label.text()
    assert "Z0002 is not in rows 2-4" in restored.error_label.text()
    assert "set.csv was recorded in" in restored.library_label.text()

    # The note used to go on the first Refresh, and on the next open.
    reopened.experiments._refresh_fragment_library_choices()

    assert "set.csv was recorded in" in restored.library_label.text()

    reopened.experiments._persist_draft(restored)
    reopened.close()
    app.processEvents()
    again = viewer(second)
    recent = again.experiments._recent_experiments()
    third = again.experiments.resume_experiment(
        recent[0].workspace_id, recent[0].plan_id
    )
    again.experiments._sync_editor_selection(third, crystals)

    assert "set.csv was recorded in" in third.library_label.text()
    assert third.current_plan is None

    # Choosing it from the list is a decision, and it is allowed to stand.
    # The entry is the one already showing, so the index does not change -
    # only ``activated`` says somebody chose.
    third.library_input.activated.emit(1)

    assert third.library_label.text() == ""
    assert third.current_plan is not None, third.error_label.text()
    assert third.rows_input.text() == "2-4"
    assert [
        item.fragment.compound_id for item in third.current_plan.assignments
    ] == ["OTHER0002", "OTHER0003", "OTHER0004"]
    again.close()
    app.processEvents()



def test_a_save_held_in_our_own_folder_is_not_called_delivered(
    tmp_path: Path, monkeypatch
) -> None:
    """One save, one story, on every screen that tells it.

    On a morning when every share is down, a save goes wholly to a folder of
    this account's own. Three screens described that one save three ways, two
    of them false: the panel said the files were not on the instruments yet,
    the step summary said they were in a folder you chose - nobody chose it -
    and the home list said, in green, that they were in the instrument
    folders when not one file was there.

    The cause was one boolean carrying two facts. "No file reached an
    instrument" and "the whole save went to a folder the user chose" are not
    the same thing, and callers reached for whichever predicate was nearest.
    """
    app = QApplication.instance() or QApplication([])
    gone = tmp_path / "mnt"  # Never created: the shares are down.
    pending = tmp_path / "pending"
    pending.mkdir()
    libraries = tmp_path / "libs"
    libraries.mkdir()
    (libraries / "set.csv").write_text(
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        + "".join(
            f"Vendor,Lib,{index},Z{index:04d},C2H6O,46.07,CCO,100,DMSO,SRC-1,"
            f"A{index:02d}\n"
            for index in range(1, 7)
        ),
        encoding="utf-8",
    )
    settings = replace(
        DEFAULT_SETTINGS,
        fragment_library_directory=libraries,
        pending_worksheet_directory=pending,
        create_missing_instrument_roots=False,
        instruments=tuple(
            replace(item, output_directory=gone / item.instrument)
            for item in DEFAULT_SETTINGS.instruments
        ),
    )
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=settings,
    )
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "1070", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(3)
    )
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "Held")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    editor.rows_input.setText("2-4")
    editor.rows_input.editingFinished.emit()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    revision = window.experiments._finalize_and_continue(editor)
    window.experiments._save_plan_worksheets(editor)
    for _ in range(200):
        app.processEvents()
        if window.experiments._successful_worksheet_export(revision) is not None:
            break
    saved = window.experiments._successful_worksheet_export(revision)

    assert saved is not None
    assert saved.outputs
    assert all(pending in Path(output.path).parents for output in saved.outputs)
    assert window.experiments._worksheets_held(revision)
    assert not window.experiments._worksheets_saved_elsewhere(revision)

    window.experiments._refresh_experiment_status(editor)
    panel = editor.worksheets_step.result_label.text()
    summary = editor.experiment_status.status_of(WorkflowStep.WORKSHEETS)
    home = next(
        item for item in window.experiments._recent_experiments()
        if item.plan_id == editor.plan_id
    )

    # The panel was right all along; the other two now agree with it.
    assert "not on the instruments yet" in panel
    assert summary.state is StepState.ATTENTION
    assert "waiting for the instruments" in summary.message
    assert home.stage_state == "attention"
    assert "waiting for instruments" in home.stage
    assert "waiting in this account's own folder" in home.stage_detail

    # The two sentences that were false.
    assert "a folder you chose" not in summary.message
    assert "are in the instrument folders" not in home.stage_detail
    window.close()
    app.processEvents()


def test_a_library_that_cannot_be_read_is_not_reported_as_gone(
    tmp_path: Path, monkeypatch
) -> None:
    """Present and unreadable is not the same as missing.

    A spreadsheet saving a library as cp949 leaves the file exactly where it
    was. The editor had the reason in hand - the folder is read as a whole
    and every file that failed is kept with why - and said "set.csv is not in
    <folder> now. Put it back", which cannot be acted on because it never
    left. The note under the field was dropped in this one branch, which is
    the only branch a library in use that stopped being readable reaches.
    """
    app = QApplication.instance() or QApplication([])
    libraries = tmp_path / "libs"
    libraries.mkdir()
    rows = (
        "Vendor,Library,No,ID,Formula,MW,Smile,Conc_mM,Solvent,Plate_ID,Plate_well\n"
        + "".join(
            f"Vendor,Lib,{index},Z{index:04d},C2H6O,46.07,CCO,100,DMSO,SRC-1,"
            f"A{index:02d}\n"
            for index in range(1, 7)
        )
    )
    (libraries / "set.csv").write_text(rows, encoding="utf-8")
    database = tmp_path / "reviews.sqlite3"

    def viewer() -> ViewerWindow:
        return ViewerWindow(
            RockMakerImageRepository(tmp_path), SQLiteReviewStore(database),
            settings=replace(DEFAULT_SETTINGS, fragment_library_directory=libraries),
        )

    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "1070", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0), datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(3)
    )
    window = viewer()
    editor = _start(window, PlanType.FRAGMENT_SCREENING, crystals, "Unread")
    editor.protein_input.setText("BRD4")
    editor.library_input.setCurrentIndex(1)
    editor.rows_input.setText("2-4")
    editor.rows_input.editingFinished.emit()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    revision = window.experiments._finalize_and_continue(editor)

    assert revision is not None
    window.close()
    app.processEvents()

    # The file does not move; it stops being UTF-8.
    (libraries / "set.csv").write_text(
        rows.replace("Lib", "라이브러리"), encoding="cp949"
    )

    assert (libraries / "set.csv").exists()

    reopened = viewer()
    recent = reopened.experiments._recent_experiments()
    restored = reopened.experiments.resume_experiment(
        recent[0].workspace_id, recent[0].plan_id
    )
    reopened.experiments._sync_editor_selection(restored, crystals)
    note = restored.library_label.text()
    error = restored.error_label.text()

    assert restored.library is None
    assert "not UTF-8" in note, note
    assert "cannot be read" in error, error
    assert "not UTF-8" in error, error
    # Already finalized, and the revision does not depend on reading it again.
    assert f"r{revision.revision} and its worksheets" in error

    # The two things it used to say, both of which sent people to the folder.
    assert "is not in" not in error
    assert "Put it back" not in error
    reopened.close()
    app.processEvents()


def _condition_revision(window, editor, times=(60, 720)):
    """A finalized condition-test revision whose harvests fall at these minutes."""
    from decimal import Decimal as D

    from xtalflow.application.planning_service import condition_test_plan_snapshot
    from xtalflow.domain import crystal_selection_from_selected_crystals
    from xtalflow.domain.condition_test import (
        Additive, ConditionTestDesign, build_condition_test_plan, new_series,
    )

    dmso = Additive("dmso", "DMSO", D(100), "CS(=O)C", "LDV-1", "A1", "384PP_DMSO2")
    design = ConditionTestDesign(
        (dmso,), (new_series(dmso.id, (D(10),), times, 1),), D(200)
    )
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2069", f"A{index + 1:02d}a",
            (CrystalTarget(f"t{index}", Decimal(0), Decimal(0),
                           datetime.now(timezone.utc)),),
            SWISSCI_MIDI_3_LENS.id, "/rmserver/a.jpg",
        )
        for index in range(len(times))
    )
    plan = build_condition_test_plan(
        design,
        crystal_selection_from_selected_crystals(editor.plan_id, crystals),
    )
    # The revision references the draft row, so the draft has to exist first.
    editor.persisted = True
    window.experiments._persist_draft(editor)
    snapshot = condition_test_plan_snapshot(plan, "BRD4")
    revision = window.experiments.planning_service.finalize(
        editor.plan_id, PlanType.CONDITION_TEST, "BRD4", snapshot, None, "jjh",
    )
    # What finalizing through the steps leaves behind, so the editor knows
    # which revision is its own.
    editor.last_revision = revision
    editor.last_revision_snapshot = snapshot
    editor.assigned_experiment_id = revision.experiment_id
    return revision


def test_a_late_harvest_can_be_sent_under_its_own_experiment_id(
    tmp_path: Path, monkeypatch
) -> None:
    """Asked at upload, because that is the only point of no return.

    MxLive makes one shipment per experiment ID and a shipment goes with its
    last crystal, so the crystals picked today wait for the overnight ones
    unless their records go up under a different name. Nothing on the plate
    changes: one dispense, one set of source wells, and the SHIFTER files
    were already written one per harvest time.
    """
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: (asked.append(args[2]), QMessageBox.Yes)[1],
    )

    parts = window.experiments._revisions_to_upload(editor, revision)

    assert asked and "one shipment per experiment ID" in asked[0]
    assert "10 h" in asked[0]
    # Nothing is said about preparing another plate or other wells.
    assert "source well" not in asked[0]
    assert parts is not None and len(parts) == 2
    assert parts[0].experiment_id == revision.experiment_id
    assert parts[1].experiment_id.endswith("-02")
    # And the plan is still the whole plate, not either part.
    assert window.experiments.planning_service.latest_revision(editor.plan_id) == (
        revision
    )
    window.close()
    app.processEvents()


def test_a_plate_already_split_is_not_split_again(tmp_path: Path, monkeypatch) -> None:
    """An upload that failed halfway has to be finishable.

    The records of one part may be on MxLive while the other never went, and
    asking again would make a third experiment ID for crystals that already
    have one.
    """
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    first = window.experiments._revisions_to_upload(editor, revision)
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: (asked.append(args[2]), QMessageBox.Yes)[1],
    )

    again = window.experiments._revisions_to_upload(editor, revision)

    assert not asked
    assert [part.experiment_id for part in again] == [
        part.experiment_id for part in first
    ]
    window.close()
    app.processEvents()


def test_backing_out_of_the_split_question_uploads_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """Cancel is not "send it all": an upload cannot be taken back."""
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)

    assert window.experiments._revisions_to_upload(editor, revision) is None

    # No, on the other hand, is "send it as one", which is what it always was.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    assert window.experiments._revisions_to_upload(editor, revision) == (revision,)
    assert window.experiments.planning_service.parts_of(revision) == ()
    window.close()
    app.processEvents()


def test_a_plate_harvested_in_one_session_is_never_asked_about(
    tmp_path: Path, monkeypatch
) -> None:
    """Nobody is held back, so there is nothing to offer and nothing to ask."""
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor, times=(0, 60))
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: (asked.append(args[2]), QMessageBox.Yes)[1],
    )

    assert window.experiments._revisions_to_upload(editor, revision) == (revision,)
    assert not asked
    window.close()
    app.processEvents()


def test_a_late_half_left_unsent_can_still_be_sent(tmp_path: Path, monkeypatch) -> None:
    """Pressing Enter past the second confirmation must not strand the crystals.

    Each part gets its own confirmation, and both default to No. So the
    ordinary way to leave the late half unsent is not a network failure but
    an Enter after the first success box - and the only way back in is the
    upload button, which reading the first part's lock alone had already
    disabled. The late crystals would have been on the bench with nowhere
    to go.
    """
    app = QApplication.instance() or QApplication([])
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), store,
        settings=replace(
            DEFAULT_SETTINGS, mxlive_base_url="https://mxlive.example",
            mxlive_key_path=key, mxlive_ca_bundle=None, mxlive_config_path=None,
        ),
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", _FakeMxLiveReader
    )
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    parts = window.experiments._revisions_to_upload(editor, revision)

    assert parts is not None and len(parts) == 2
    from xtalflow.application.labwork_upload import send_labworks

    # The early half went; the late one never was offered an upload.
    window.experiments.upload_service.complete(
        window.experiments.upload_service.begin(
            parts[0], "jjh", "7", "https://mxlive/upload_labworks/BL-5C/",
            tuple(
                record.to_payload()
                for record in window.experiments._records_of(editor, parts[0])
            ),
        ),
        send_labworks(lambda: {"created": 2}),
    )
    # Composing the plan is not what is under test here, and this editor was
    # handed a revision rather than built up through the steps.
    monkeypatch.setattr(
        type(window.experiments), "_revision_is_still_the_plan",
        lambda self, editor: True,
    )
    window.experiments._update_webdb_upload_controls(editor)

    assert editor.webdb_upload_button.isEnabled()
    assert parts[1].experiment_id in editor.webdb_upload_state
    assert "still to send" in editor.webdb_upload_state
    # And asking again hands back the same two parts, not a third ID.
    assert [part.experiment_id for part in
            window.experiments._revisions_to_upload(editor, revision)] == [
        part.experiment_id for part in parts
    ]
    window.close()
    app.processEvents()


def test_two_halves_of_a_plate_say_which_is_which(tmp_path: Path, monkeypatch) -> None:
    """Two unlabelled summaries side by side say nothing about which is which.

    A plate sent under two experiment IDs is read as two, so each line has
    to carry the name the records went out under - that is where a harvest
    would be recorded, and the crystals picked overnight are not the ones
    picked in the afternoon.
    """
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    parts = window.experiments._revisions_to_upload(editor, revision)
    # Neither has been uploaded, so each reads as the same refusal - which is
    # exactly the case where two unlabelled lines would be unreadable.
    monkeypatch.setattr(
        type(window.experiments), "_harvest_review_of",
        lambda self, editor, revision, experiment_id: "no successful upload",
    )
    window.experiments._check_harvest(editor)
    said = editor.worksheets_step.harvest_status_label.text()

    assert said.count("\n") == 1
    for part in parts:
        assert f"{part.experiment_id}: no successful upload" in said
    window.close()
    app.processEvents()


def test_an_unreadable_upload_history_does_not_read_as_sent(
    tmp_path: Path, monkeypatch
) -> None:
    """A plate is not sent because the half we could read was.

    Upload history that cannot be read is what blocks an upload, so it has
    to count as outstanding. Taking the last part's state instead said
    "Uploaded" about a plate whose early half nobody could account for, and
    the button that is the only way back in was disabled with it.
    """
    from xtalflow.application.labwork_upload import (
        UploadAvailability, UploadLockState,
    )
    from xtalflow.domain.plan_lifecycle import WebDBUploadEvent

    app = QApplication.instance() or QApplication([])
    key = tmp_path / "keys.dsa"
    key.write_bytes(b"test-key-presence")
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path), store,
        settings=replace(
            DEFAULT_SETTINGS, mxlive_base_url="https://mxlive.example",
            mxlive_key_path=key, mxlive_ca_bundle=None, mxlive_config_path=None,
        ),
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", _FakeMxLiveReader
    )
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    parts = window.experiments._revisions_to_upload(editor, revision)
    early, late = parts
    # The early half's history cannot be read; the late half is on MxLive.
    uploaded = UploadLockState(
        UploadAvailability.UPLOADED,
        WebDBUploadEvent(
            "u", late.id, "jjh", "7", "https://mxlive/upload_labworks/BL-5C/",
            datetime.now(timezone.utc), "succeeded", 1, "[]",
        ),
    )
    monkeypatch.setattr(
        type(window.experiments), "_webdb_upload_lock",
        lambda self, experiment_id: (
            None if experiment_id == early.experiment_id else uploaded
        ),
    )
    monkeypatch.setattr(
        type(window.experiments), "_revision_is_still_the_plan",
        lambda self, editor: True,
    )

    outstanding, lock = window.experiments._outstanding_part(
        parts, window.experiments._webdb_upload_lock
    )

    assert outstanding.experiment_id == early.experiment_id
    assert lock is None
    window.close()
    app.processEvents()


def _records_now_holding(held: list):
    """A writer that puts what it posts where MxLive would hold it.

    So a harvest write can be read back the way it proves itself in the real
    thing: the next read finds the values on MxLive and the row says so.
    """

    class Writes(_RecordingWriter):
        def upload_labworks(self, records):
            result = super().upload_labworks(records)
            by_well = {
                (record.plate_code, record.plate_well): record for record in held
            }
            for payload in records:
                key = (payload["plate_code"], payload["plate_well"])
                if key in by_well:
                    held.remove(by_well[key])
                held.append(MxLiveLabwork.from_mapping(dict(payload)))
            return result

    return Writes


def test_a_harvest_cannot_be_sent_before_it_has_been_looked_at(
    tmp_path: Path, monkeypatch
) -> None:
    """The offer to write to MxLive comes only from a read that found something.

    A harvest write cannot be taken back, so the one thing this screen must
    never do is let somebody send a plate they have not seen. The button is
    enabled by the check and says the number it would send, because that
    number is the whole of what pressing it does.
    """
    app, window, _store, _editor, step, _held, _dialogs = _harvest_ready(
        tmp_path, monkeypatch
    )

    assert not step.harvest_record_button.isEnabled()
    assert step.harvest_record_button.text() == "Record harvest in MxLive…"

    step.harvest_check_button.click()

    assert step.harvest_record_button.isEnabled()
    assert step.harvest_record_button.text() == "Record 1 harvest in MxLive…"
    window.close()
    app.processEvents()


def test_recording_a_harvest_sends_the_picked_crystal_and_not_the_failed_one(
    tmp_path: Path, monkeypatch
) -> None:
    """And proves itself by reading MxLive again, not by its own say-so.

    The row that went in comes back as already recorded, which is what makes
    a second press safe. Nothing is written locally: an audit row would
    become this experiment ID's newest successful upload, and the next read
    takes that for the records as they were first uploaded.
    """
    app, window, store, editor, step, held, dialogs = _harvest_ready(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient",
        _records_now_holding(held),
    )
    step.harvest_check_button.click()
    before = len(_RecordingWriter.posted)
    uploads_before = len(store.audit.list_webdb_uploads_for_experiment(
        editor.last_revision.experiment_id
    ))

    step.harvest_record_button.click()

    sent = _RecordingWriter.posted[-1]
    assert len(_RecordingWriter.posted) == before + 1
    assert [payload["plate_well"] for payload in sent] == ["A01a"]
    assert sent[0]["harvest_time"] == "2026-11-17 17:17:00.0"
    assert sent[0]["puck_name"] == "5C-0001"
    assert sent[0]["puck_nohole"] == 1
    # The record this experiment was uploaded with, carried whole: MxLive
    # nulls every column it is not sent.
    assert sent[0]["project_id"] == window.experiments.mxlive_account.account_id
    assert sent[0]["name"] and sent[0]["plate_imgpath"]
    # Every column the upload sent, so that none of them is nulled: MxLive
    # builds its column dict per column from what it is sent.
    original = json.loads(
        store.audit.list_webdb_uploads_for_experiment(
            editor.last_revision.experiment_id
        )[0].payload_json
    )
    was_sent = next(
        item for item in original if item["plate_well"] == "A01a"
    )
    assert set(sent[0]) >= set(was_sent) - {"harvest_order"}
    assert sent[0]["expri_id"] == editor.last_revision.experiment_id
    # Nothing of the failed pick, not even the instrument's words about it.
    assert "Melted" not in json.dumps(list(sent))
    # Read back from MxLive, and that is the whole of the proof.
    assert step.harvest_status_label.text() == (
        "0 harvests to record · 1 already recorded · 1 picks failed"
    )
    assert not step.harvest_record_button.isEnabled()
    # Not in our own audit: that row would be read as the original upload.
    assert len(store.audit.list_webdb_uploads_for_experiment(
        editor.last_revision.experiment_id
    )) == uploads_before
    assert ("critical", ) not in [(name,) for name, _ in dialogs]
    window.close()
    app.processEvents()


def test_declining_the_harvest_question_sends_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """And the default is No, because the box cannot be undone by pressing Enter."""
    app, window, _store, _editor, step, held, _dialogs = _harvest_ready(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient",
        _records_now_holding(held),
    )
    step.harvest_check_button.click()
    before = len(_RecordingWriter.posted)
    asked: list = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: asked.append((args[2], kwargs or args[4:])) or
        QMessageBox.No,
    )

    step.harvest_record_button.click()

    assert len(_RecordingWriter.posted) == before
    # The pucks are named, because a wrong puck is the one mistake here that
    # no reading of ours could catch.
    assert "5C-0001" in asked[-1][0]
    assert "MxLive cannot undo this" in asked[-1][0]
    assert "status" in asked[-1][0] and "Active" in asked[-1][0]
    assert QMessageBox.No in asked[-1][1]
    window.close()
    app.processEvents()


def test_a_harvest_write_that_stops_halfway_leaves_the_rest_sendable(
    tmp_path: Path, monkeypatch
) -> None:
    """The one failure that must not need repairing by hand.

    MxLive is written one record at a time, so a connection that drops in
    the middle leaves some crystals recorded and some not. Nothing local
    could be trusted to say which: the program may not have been running
    when it happened. So MxLive is asked again, and what it holds is the
    answer - the crystals that went in read as already recorded and the rest
    read as still to record, which is what makes pressing again safe.
    """
    from xtalflow.domain.mxlive import MxLivePartialWriteError

    app, window, _store, _editor, step, held, dialogs = _harvest_ready(
        tmp_path, monkeypatch, second_pick="picked"
    )

    class StopsAfterOne(_records_now_holding(held)):
        def upload_labworks(self, records):
            # The first record goes in, then the connection drops.
            super().upload_labworks(records[:1])
            raise MxLivePartialWriteError(1, len(records), "connection reset")

    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", StopsAfterOne
    )
    step.harvest_check_button.click()
    assert step.harvest_status_label.text() == "2 harvests to record"
    assert step.harvest_record_button.text() == "Record 2 harvests in MxLive…"

    step.harvest_record_button.click()

    # Said, with the number that decides what to do next.
    said = [text for name, text in dialogs if name == "critical"]
    assert said and "connection reset" in said[-1]
    # And the table says where the plate actually stands now.
    assert step.harvest_status_label.text() == "1 harvest to record · 1 already recorded"
    assert step.harvest_record_button.isEnabled()
    assert step.harvest_record_button.text() == "Record 1 harvest in MxLive…"

    # Pressing again sends only what is left, and nothing twice.
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient",
        _records_now_holding(held),
    )
    step.harvest_record_button.click()

    assert [payload["plate_well"] for payload in _RecordingWriter.posted[-1]] == [
        "A02a"
    ]
    assert step.harvest_status_label.text() == "0 harvests to record · 2 already recorded"
    assert not step.harvest_record_button.isEnabled()
    window.close()
    app.processEvents()


def test_a_file_holding_a_well_the_plate_never_sent_is_nobody_s_worksheet(
    tmp_path: Path, monkeypatch
) -> None:
    """A returned file is recognised by its wells, and all of them have to fit.

    A file holding a row for another plate is not a worksheet of ours, even
    though every other row in it is: either it is not only ours, or the
    plate is not the one we think. It is refused whole rather than read for
    the rows that look right, because a record created for a well nobody
    planned cannot be deleted - and so the plate reads as not picked yet and
    nothing is offered to send.

    The domain refuses a stray well a second time, in can_be_recorded; this
    is the path a person actually reaches, and it stops earlier.
    """
    app, window, store, editor, step, held, _dialogs = _harvest_ready(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient",
        _records_now_holding(held),
    )
    # A row for a well of some other plate, appended to our returned file.
    path = store.audit.list_worksheet_exports(
        editor.last_revision.id
    )[0].path_for("shifter1")
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    stray = lines[0].split(",")
    stray[1] = "9999"
    Path(path).write_text(
        "\r\n".join(lines + [",".join(stray)]) + "\r\n", encoding="utf-8"
    )

    step.harvest_check_button.click()

    # Every well of the plate is still listed, because the wells come from
    # what was uploaded and not from the file - "not picked yet" is
    # information, and a plate half harvested is normal mid-session.
    assert step.harvest_status_label.text() == "0 harvests to record · 2 not picked yet"
    assert step.harvest_table.rowCount() == 2
    # The file is named, and what became of it, so somebody can go and look.
    assert "not a worksheet of this revision" in step.harvest_note_label.text()
    # Nothing may be sent, and the button does not offer it.
    assert not step.harvest_record_button.isEnabled()
    assert step.harvest_record_button.text() == "Record harvest in MxLive…"
    window.close()
    app.processEvents()


def test_two_files_disagreeing_about_one_well_stops_the_other_crystals_too(
    tmp_path: Path, monkeypatch
) -> None:
    """One contradiction refuses the whole plate, not only its own well.

    The worksheet is saved to both stations, so two files can come back
    describing the same crystal differently - the operator loaded one, the
    other was edited, nobody remembers which. The crystals the two files
    agree about could be sent, and that is the trap: somebody would later
    have to work out which half of the plate went, and MxLive cannot take a
    record back. So nothing is offered while the contradiction stands.
    """
    app, window, store, editor, step, held, _dialogs = _harvest_ready(
        tmp_path, monkeypatch, second_pick="picked"
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient",
        _records_now_holding(held),
    )
    export = store.audit.list_worksheet_exports(editor.last_revision.id)[0]
    agreed = Path(export.path_for("shifter1")).read_text(encoding="utf-8")
    # The same worksheet at the other station, with one well's harvest an
    # hour later. Both files are this revision's, so both are read.
    Path(export.path_for("shifter2")).write_text(
        agreed.replace("2026-11-17 17:17:00.0", "2026-11-17 18:17:00.0"),
        encoding="utf-8",
    )

    step.harvest_check_button.click()

    said = step.harvest_status_label.text()
    assert "1 needing a look" in said
    # The crystal the two files agree about is still read, and still refused.
    assert "1 harvest to record" in said
    assert not step.harvest_record_button.isEnabled()
    assert step.harvest_record_button.text() == "Record harvest in MxLive…"
    window.close()
    app.processEvents()


def test_a_split_plate_says_which_shipment_each_crystal_is_in(
    tmp_path: Path, monkeypatch
) -> None:
    """The column earns its place exactly when there are two experiment IDs.

    A plate whose late harvest went separately has its crystals under two
    names, and which one a crystal is under decides where its harvest is
    recorded - and which shipment the beamline will find it in. The summary
    line names both, but a line cannot say which of twelve rows belongs to
    which, and the harvest times that divided them are not something to make
    somebody re-derive from the table.
    """
    app = QApplication.instance() or QApplication([])
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    window = ViewerWindow(RockMakerImageRepository(tmp_path), store)
    editor = window.experiments.start_experiment(PlanType.CONDITION_TEST)
    revision = _condition_revision(window, editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    early, late = window.experiments._revisions_to_upload(editor, revision)

    # The rows a read hands over carry the ID it read, which is what the
    # column shows: nothing re-derives it from the plate or the times.
    from xtalflow.application.harvest_review import HarvestReview
    from xtalflow.domain.harvest_record import HarvestOutcome, HarvestRow
    from xtalflow.ui.experiment_coordinator import _harvest_rows

    read = HarvestReview(
        early.experiment_id, early.id, "jjh",
        (HarvestRow("1070", "A01a", HarvestOutcome.NOT_PICKED),),
    )
    (built,) = _harvest_rows(read)
    assert built[0] == early.experiment_id

    editor.worksheets_step.show_harvest("read", (built,))
    # One ID in the table: nothing to tell apart, so nothing in the way.
    assert editor.worksheets_step.harvest_table.isColumnHidden(0)

    editor.worksheets_step.show_harvest(
        "read",
        (
            (early.experiment_id, "1070", "A01a", "", "", "", "", "", "x"),
            (late.experiment_id, "1070", "A02a", "", "", "", "", "", "x"),
        ),
    )

    table = editor.worksheets_step.harvest_table
    assert not table.isColumnHidden(0)
    assert table.item(0, 0).text() == early.experiment_id
    assert table.item(1, 0).text() == late.experiment_id
    assert early.experiment_id != late.experiment_id
    window.close()
    app.processEvents()


def _partly_uploaded(tmp_path: Path, monkeypatch):
    """An upload that stored its first record and was cut off on the second.

    One record per call is how MxLive's endpoint is written, so this is the
    ordinary shape of a lost connection mid-upload, not an exotic one.
    """
    from xtalflow.domain.mxlive import MxLivePartialWriteError

    window, store, editor, revision, dialogs = _finalized_raw_upload_window(
        tmp_path, monkeypatch, ("A01a", "A02a", "A03a")
    )
    stored: list[MxLiveLabwork] = []

    class Writer(_RecordingWriter):
        posted = []
        # The connection drops once, on the second record of the first
        # attempt. A later send is an ordinary one, so that what is measured
        # here is the recovery and not the fault.
        breaks = True

        def upload_labworks(self, records):
            type(self).posted.append(tuple(records))
            for index, record in enumerate(records):
                if index == 1 and type(self).breaks:
                    type(self).breaks = False
                    raise MxLivePartialWriteError(
                        index, len(records), "connection reset"
                    )
                stored.append(MxLiveLabwork.from_mapping({
                    "expri_id": record["expri_id"],
                    "protein_name": record["protein_name"],
                    "plate_code": record["plate_code"],
                    "plate_well": record["plate_well"],
                }))
            return {"created": len(records)}

    class Reader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def labworks(self, experiment_id):
            return tuple(stored)

    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient", Writer
    )
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveReadClient", Reader
    )
    window.experiments._upload_plan_labworks(editor)
    assert [
        event.status for event in store.audit.list_webdb_uploads(revision.id)
    ] == ["partial"]
    return window, store, editor, revision, dialogs, Writer, stored


def test_an_interrupted_upload_is_finished_by_sending_only_what_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """The records that landed are not sent again, so none is overwritten.

    Before this there was no way out: verifying wrote the attempt down as
    partial, which locks the experiment ID, and uploading again was refused
    because MxLive already held records. The crystals that never arrived
    could not be sent from XtalFlow at all, and a plate cannot ship until
    its last decision is on MxLive.
    """
    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, dialogs, Writer, stored = _partly_uploaded(
        tmp_path, monkeypatch
    )
    assert len(stored) == 1

    # Pressing Upload is what the person does next: the button now reads
    # Verify on MxLive, and finishing is offered from its answer.
    window.experiments._upload_plan_labworks(editor)

    sent_again = Writer.posted[-1]
    assert [record["plate_well"] for record in sent_again] == ["A02a", "A03a"]
    assert all(record["plate_well"] != "A01a" for record in sent_again)
    assert [
        event.status for event in store.audit.list_webdb_uploads(revision.id)
    ] == ["succeeded"]
    assert len({event.id for event in store.audit.list_webdb_uploads(revision.id)}) == 1
    window.close()
    app.processEvents()


def test_the_payload_of_a_finished_upload_still_holds_every_record(
    tmp_path: Path, monkeypatch
) -> None:
    """The harvest read takes it as the whole set of records it owns.

    A second audit row holding only the records sent later would leave the
    first ones with no payload, and a record with no payload cannot have a
    harvest added to it - the payload is the only proof of ownership.
    """
    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, dialogs, _writer, _stored = _partly_uploaded(
        tmp_path, monkeypatch
    )

    window.experiments._upload_plan_labworks(editor)

    (event,) = store.audit.list_webdb_uploads(revision.id)
    wells = [record["plate_well"] for record in json.loads(event.payload_json)]
    assert wells == ["A01a", "A02a", "A03a"]
    window.close()
    app.processEvents()


def test_declining_to_finish_an_upload_sends_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    window, store, editor, revision, dialogs, Writer, _stored = _partly_uploaded(
        tmp_path, monkeypatch
    )
    sent_before = len(Writer.posted)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No
    )

    window.experiments._upload_plan_labworks(editor)

    assert len(Writer.posted) == sent_before
    assert [
        event.status for event in store.audit.list_webdb_uploads(revision.id)
    ] == ["partial"]
    window.close()
    app.processEvents()


def test_a_harvest_written_while_the_question_stood_open_is_not_overwritten(
    tmp_path: Path, monkeypatch
) -> None:
    """Nothing limits how long that box can stand open.

    A person can leave it and come back after lunch, and in that time the
    beamline can record a harvest of its own through MxLive's own web page.
    Everything the question described was read before it was asked, so
    without a second read the agreement is to a state that is gone - and
    what would go out is the harvest of a crystal somebody has already
    recorded differently, which MxLive cannot undo.
    """
    app, window, _store, editor, step, held, dialogs = _harvest_ready(
        tmp_path, monkeypatch
    )
    step.harvest_check_button.click()
    assert step.harvest_record_button.isEnabled()
    monkeypatch.setattr(
        "xtalflow.ui.experiment_coordinator.LegacyMxLiveWriteClient",
        _records_now_holding(held),
    )
    before = len(_RecordingWriter.posted)

    def the_beamline_records_it_meanwhile(*args, **kwargs):
        held[:] = [
            MxLiveLabwork.from_mapping(dict(
                record.raw,
                harvest_time="2026-11-17 18:40:11.2",
                puck_name="ISA-009",
                puck_nohole=3,
            ))
            if record.plate_well == "A01a" else record
            for record in held
        ]
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", the_beamline_records_it_meanwhile)
    step.harvest_record_button.click()

    assert len(_RecordingWriter.posted) == before
    assert dialogs[-1][0] == "warning"
    assert "nothing was sent" in dialogs[-1][1].lower()
    # And the table now shows what the beamline wrote, so the person can see
    # why rather than being told only that it did not go.
    assert "ISA-009" not in step.harvest_table.item(0, 5).text()
    assert "different harvest" in step.harvest_table.item(0, 8).text()
    window.close()
    app.processEvents()


def test_what_this_copy_is_is_read_from_each_layout_a_checkout_uses(
    tmp_path: Path,
) -> None:
    """Four layouts, and this repository is only ever one of them.

    The worktree case is here because it was wrong and silently so: a
    worktree's own git directory holds its HEAD and nothing else, with the
    branch refs in the common directory, so reading the ref beside HEAD found
    nothing and every real worktree answered "no commit". The first version of
    this test built refs beside HEAD - a layout closer to a submodule - and so
    agreed with the bug. Checked against a worktree git itself made before
    being written this way.

    The packed case matters most in practice: a fresh clone keeps its refs
    packed until something writes one, and a fresh clone is what the beamline
    makes to test a commit.
    """
    from xtalflow.build import NONE, READ, UNREADABLE, Build, read_build

    sha = "0123456789abcdef0123456789abcdef01234567"

    # A loose ref, which a checkout has once it commits.
    loose = tmp_path / "loose"
    (loose / ".git" / "refs" / "heads").mkdir(parents=True)
    (loose / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (loose / ".git" / "refs" / "heads" / "main").write_text(
        f"{sha}\n", encoding="utf-8"
    )
    assert read_build(loose) == Build(sha, READ, "", str(loose))

    # Packed, which a fresh clone has.
    packed = tmp_path / "packed"
    (packed / ".git").mkdir(parents=True)
    (packed / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (packed / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted \n"
        f"{sha} refs/heads/main\n"
        f"{'f' * 40} refs/remotes/origin/other\n",
        encoding="utf-8",
    )
    assert read_build(packed).commit == sha

    # Detached: HEAD is the commit itself.
    detached = tmp_path / "detached"
    (detached / ".git").mkdir(parents=True)
    (detached / ".git" / "HEAD").write_text(f"{sha}\n", encoding="utf-8")
    assert read_build(detached).commit == sha

    # A worktree, as git lays one out: .git is a file naming a directory
    # under the main repository, that directory holds HEAD and commondir,
    # and the branch ref is only in the common directory.
    main = tmp_path / "main"
    (main / ".git" / "refs" / "heads").mkdir(parents=True)
    (main / ".git" / "refs" / "heads" / "side").write_text(
        f"{sha}\n", encoding="utf-8"
    )
    held = main / ".git" / "worktrees" / "side"
    held.mkdir(parents=True)
    (held / "HEAD").write_text("ref: refs/heads/side\n", encoding="utf-8")
    (held / "commondir").write_text("../..\n", encoding="utf-8")
    tree = tmp_path / "side"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {held}\n", encoding="utf-8")
    assert read_build(tree).commit == sha, "a worktree must find its branch"

    # No checkout: an install from a tarball, which says the version alone.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert read_build(plain) == Build("", NONE, "", str(plain))
    assert read_build(plain).title_suffix == ""

    # A checkout whose commit cannot be read is not the same as having none,
    # and says so rather than passing for a tarball.
    broken = tmp_path / "broken"
    (broken / ".git").mkdir(parents=True)
    (broken / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    unreadable = read_build(broken)
    assert unreadable.state == UNREADABLE
    assert "refs/heads/main" in unreadable.detail
    assert unreadable.title_suffix == " · ?"
    assert unreadable.for_log == UNREADABLE


def test_the_log_and_the_title_cannot_disagree_about_the_build() -> None:
    """They did: seven characters against twelve, "" against "unknown".

    And one was read at import while the other was read per call, so a
    diagnostics report written after the install was fast-forwarded named a
    commit the running process was not running.
    """
    from xtalflow import APP_WINDOW_TITLE
    from xtalflow.build import BUILD
    from xtalflow.infrastructure import operations_log

    assert operations_log.commit_hash() == BUILD.for_log
    assert BUILD.for_log.startswith(BUILD.short)
    assert BUILD.short in APP_WINDOW_TITLE


def test_a_revision_nothing_can_rebuild_is_not_delivered_on_an_old_division(
    tmp_path: Path,
) -> None:
    """The gate's last question when there is nothing to compare with.

    A finalized revision whose fragment library has moved composes to
    nothing, and "nothing to compare" used to pass as "nothing changed" -
    which let a revision divided by a rule this build no longer uses write
    its worksheet. That file goes to the instrument, and the liquid goes
    where the file says. The snapshot alone can answer whether its division
    is this build's, so it is asked.
    """
    app = QApplication.instance() or QApplication([])
    window, _store, editor = _fragment_plan_window(tmp_path, DEFAULT_SETTINGS)
    gate = window.experiments

    def snapshot(shares):
        return json.dumps({
            "schema": 1, "plan_type": "fragment_screening", "protein": "BRD4",
            "assignments": [{
                "well": "A01a",
                "targets": [{"id": f"t{i}", "volume_nl": share}
                            for i, share in enumerate(shares)],
            }],
        })

    # Nothing can be composed: that is the branch under test.
    gate._plan_snapshot = lambda _editor: None

    # 20 nL over three positions is 7.5, 7.5, 5 here. A revision holding
    # 5, 5, 10 was divided by another rule.
    editor.last_revision_snapshot = snapshot(["7.5", "7.5", "5.0"])
    assert gate._revision_is_still_the_plan(editor), "its own division, still the plan"

    editor.last_revision_snapshot = snapshot(["5.0", "5.0", "10.0"])
    assert not gate._revision_is_still_the_plan(editor), "another rule divided this"

    window.close()
    app.processEvents()


def test_a_library_read_from_a_moved_folder_is_not_a_changed_plan(
    tmp_path: Path,
) -> None:
    """The sentence beside the header used to disagree with the header.

    Where a fragment library is read from is not part of the plan, and the
    status label knew it. The fact that drives the step's message compared
    the snapshots with == instead, so moving the folder made the step say
    the plan had changed - and its save button dead - under a header still
    reading "Finalized r1".
    """
    app = QApplication.instance() or QApplication([])
    window, _store, editor = _fragment_plan_window(tmp_path, DEFAULT_SETTINGS)

    composed = editor.plan_snapshot()
    assert composed is not None, editor.error_label.text()
    moved = json.loads(composed)
    assert "library_id" in moved, moved.keys()
    moved["library_id"] = "/somewhere/else/library.csv"
    editor.last_revision_snapshot = json.dumps(moved)
    editor.last_revision = PlanRevision(
        "revision", "plan", 1, "BRD4-2069-1",
        editor.last_revision_snapshot, "someone", datetime.now(timezone.utc),
    )

    facts = editor.facts(False, False)
    assert facts.revision_matches_current, "the same plan, read from elsewhere"

    # And a real edit is still a real edit.
    changed = json.loads(composed)
    changed["protein"] = "MDM2"
    editor.last_revision_snapshot = json.dumps(changed)
    assert not editor.facts(False, False).revision_matches_current

    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_one_double_click_marks_one_position(tmp_path: Path, monkeypatch) -> None:
    """Qt makes a second press out of a double click, and it was being kept.

    QWidget.mouseDoubleClickEvent calls mousePressEvent by default, so one
    double click arrived as two presses at the same point. A slow or shaky
    double click is how a mouse behaves, not a second instruction.

    Where the extra position lands is what makes it serious. On the same
    well it goes past the configured count, and since the count is asked
    about it can be refused. But when the first click fills the well and the
    view moves on - which is the ordinary flow, one position per well - the
    second press is the FIRST position on the next well, and nothing asks
    about a first position. It is kept, soaked, and sent to MxLive inside
    that well's dose, on a well the operator never looked at.

    Measured before the fix: two positions on two wells, one gesture, no
    question. So the count here is over the whole plate.
    """
    app = QApplication.instance() or QApplication([])
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: (asked.append(args[2]), QMessageBox.No)[1],
    )
    window = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        auto_advance_target_count=1,
    )
    # Low enough that A01a (0.89) is confirmed on load, so the view advances
    # once the well has its one position. That is the dangerous path.
    window.auto_confirm_confidence_input.setValue(50)
    window.load_plate("2070", SWISSCI_MRC_2_WELL)
    window.auto_confirm_plate_checkbox.setChecked(True)
    window._auto_detect_calibration()
    app.processEvents()
    asked.clear()  # the calibration question is not what this is about

    canvas = window.image_canvas
    at = QPoint(canvas.width() // 2, canvas.height() // 2)
    for kind in (
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
        QEvent.MouseButtonRelease,
    ):
        app.sendEvent(
            canvas,
            QMouseEvent(kind, at, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier),
        )
    app.processEvents()

    session = window.controller.session
    held = {
        key: session.target_count_for(key)
        for key in session._targets_by_image
        if session.target_count_for(key)
    }
    assert sum(held.values()) == 1, held
    assert len(held) == 1, f"a second well was marked: {held}"
    assert asked == [], f"and it was not even asked about: {asked}"

    window.close()
    app.processEvents()


def test_the_canvas_reports_a_double_click_once() -> None:
    """The same fix as the test above, provable where there are no images.

    That one drives a loaded plate, so it needs the local RMServer fixture
    and skips on every machine without one - including the beamline server,
    where it was pointed out that the fix therefore went unproven. The
    override itself lives on the canvas and needs no plate, no well and no
    calibration: a pixmap and four events are enough, and this runs
    everywhere.

    Qt sends press, release, double-click, release for one double click, and
    the default double-click handler calls mousePressEvent, so the canvas
    used to report two clicks at one point. The window test says why that
    matters; this one says it does not happen.
    """
    from xtalflow.ui.review_widgets import ImageCanvas

    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(400, 300)
    pixmap = QPixmap(400, 300)
    pixmap.fill(Qt.gray)
    canvas.set_image(pixmap, ())
    canvas.show()
    app.processEvents()

    seen: list[tuple[int, int, int]] = []
    canvas.image_clicked.connect(
        lambda x, y, button: seen.append((round(x), round(y), button))
    )

    at = QPoint(200, 150)
    for kind in (
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
        QEvent.MouseButtonRelease,
    ):
        app.sendEvent(
            canvas,
            QMouseEvent(kind, at, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier),
        )
    app.processEvents()

    assert len(seen) == 1, seen

    # A second, separate click is a second click and must still arrive.
    app.sendEvent(
        canvas,
        QMouseEvent(
            QEvent.MouseButtonPress, at, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        ),
    )
    app.processEvents()
    assert len(seen) == 2, seen

    canvas.close()
    app.processEvents()


def test_a_marker_is_numbered_in_the_order_its_dose_is_divided() -> None:
    """The number on a marker has to be the number the dose is split by.

    A dose that will not go round evenly gives its leftover 2.5 nL steps to
    the lowest position orders, so 25 nL over four positions is 7.5, 7.5, 5,
    5 and the first-clicked spot takes the biggest drop. Until now which
    spot was clicked first lived only in the operator's memory, so a
    worksheet could not be checked against a plate at all.

    The canvas therefore may not number by the order the targets happen to
    arrive in - restored from the database, that is whatever the query gave.
    It sorts by selected_at, which is the same key
    ``crystal_selection_from_selected_crystals`` uses to assign
    position_order, and this test holds the two together rather than
    trusting that they agree.
    """
    from xtalflow.domain.crystal_selection import (
        crystal_selection_from_selected_crystals,
    )
    from xtalflow.domain.crystal_workflow import CrystalTarget, SelectedCrystal
    from xtalflow.domain.soaking import split_between_positions
    from xtalflow.ui.review_widgets import ImageCanvas

    app = QApplication.instance() or QApplication([])
    base = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    clicked = {"first": 0, "second": 1, "third": 2, "fourth": 3}
    # Deliberately not in click order, the way a restored tuple arrives.
    arrived = ("third", "first", "fourth", "second")

    canvas = ImageCanvas()
    canvas.resize(420, 320)
    pixmap = QPixmap(420, 320)
    pixmap.fill(Qt.darkGray)
    canvas.set_image(
        pixmap,
        tuple(
            TargetPoint(name, "image", 100 + 40 * index, 100,
                        base + timedelta(seconds=clicked[name]))
            for index, name in enumerate(arrived)
        ),
    )

    on_screen = canvas.position_numbers()
    assert on_screen == {"first": 1, "second": 2, "third": 3, "fourth": 4}, on_screen

    # And that is the order the dose is actually divided in.
    selection = crystal_selection_from_selected_crystals(
        "project",
        (
            SelectedCrystal(
                "image", "1070", "A01a",
                tuple(
                    CrystalTarget(name, Decimal("1"), Decimal("1"),
                                  base + timedelta(seconds=clicked[name]))
                    for name in arrived
                ),
            ),
        ),
    )
    in_the_plan = {
        position.source_target_id: position.position_order
        for position in selection.wells[0].soaking_positions
    }
    assert in_the_plan == on_screen, (in_the_plan, on_screen)

    shares = split_between_positions(Decimal("25"), 4)
    assert shares[0] > shares[-1], shares
    assert in_the_plan["first"] == 1, "the biggest drop is the one marked 1"

    canvas.close()
    app.processEvents()


def test_position_numbers_are_off_until_the_view_option_is_ticked() -> None:
    """Asked for by the operator after seeing them always on.

    The number answers one question - which spot takes the biggest drop when
    a dose will not divide evenly - and that question is asked while checking
    a worksheet against a plate, not while looking for crystals. Shown
    always, it is ink on top of the thing being looked at.

    So the canvas draws them only when asked, and the window's View menu is
    where the asking happens, beside the other things that can be shown and
    hidden.
    """
    from xtalflow.ui.review_widgets import ImageCanvas

    app = QApplication.instance() or QApplication([])
    base = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    canvas = ImageCanvas()
    canvas.resize(300, 200)
    pixmap = QPixmap(300, 200)
    pixmap.fill(Qt.black)
    canvas.set_image(
        pixmap,
        (TargetPoint("t", "image", 150, 100, base),),
    )
    canvas.show()
    app.processEvents()

    def light_pixels_beside_the_marker() -> int:
        shot = canvas.grab().toImage()
        x, y = canvas.transform().image_to_viewport(150, 100)
        found = 0
        for badge_x in range(round(x) + 8, round(x) + 26):
            for badge_y in range(round(y) - 26, round(y) - 8):
                if shot.valid(badge_x, badge_y):
                    colour = shot.pixelColor(badge_x, badge_y)
                    if colour.red() > 200 and colour.green() > 200 and colour.blue() > 200:
                        found += 1
        return found

    assert light_pixels_beside_the_marker() == 0, "numbers are off to begin with"

    canvas.set_position_numbers_visible(True)
    app.processEvents()
    assert light_pixels_beside_the_marker() > 4, "and drawn once asked for"

    canvas.set_position_numbers_visible(False)
    app.processEvents()
    assert light_pixels_beside_the_marker() == 0, "and gone again"

    canvas.close()
    app.processEvents()


def test_every_marker_carries_a_number_that_can_be_read() -> None:
    """Drawn, and drawn so it survives a bright image and a dark one.

    A number nobody can read is the same as no number, and a crystal image
    is bright in places and dark in others, so the badge is filled and the
    digit reversed out of it. The check is that white pixels appear beside
    each marker on both backgrounds - not what shape they make, which only
    a person can judge.
    """
    from xtalflow.ui.review_widgets import ImageCanvas

    app = QApplication.instance() or QApplication([])
    base = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

    for background in (Qt.black, Qt.white):
        canvas = ImageCanvas()
        canvas.resize(300, 200)
        pixmap = QPixmap(300, 200)
        pixmap.fill(background)
        canvas.set_image(
            pixmap,
            tuple(
                TargetPoint(f"t{index}", "image", 80 + 60 * index, 100,
                            base + timedelta(seconds=index))
                for index in range(3)
            ),
        )
        canvas.set_position_numbers_visible(True)
        canvas.show()
        app.processEvents()
        shot = canvas.grab().toImage()

        for index in range(3):
            x, y = canvas.transform().image_to_viewport(80 + 60 * index, 100)
            white = 0
            for badge_x in range(round(x) + 8, round(x) + 26):
                for badge_y in range(round(y) - 26, round(y) - 8):
                    if shot.valid(badge_x, badge_y):
                        colour = shot.pixelColor(badge_x, badge_y)
                        if colour.red() > 200 and colour.green() > 200 and colour.blue() > 200:
                            white += 1
            assert white > 4, f"no readable digit beside marker {index} on {background}"

        canvas.close()
        app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_the_view_menu_turns_the_position_numbers_on(tmp_path: Path) -> None:
    """The canvas tests prove the drawing; this proves the menu reaches it.

    A switch that is wired to nothing passes every test of the thing it was
    meant to switch.
    """
    app = QApplication.instance() or QApplication([])
    window, _store, _editor = _fragment_plan_window(tmp_path, DEFAULT_SETTINGS)

    assert not window.position_numbers_action.isChecked(), "off when the window opens"
    assert not window.image_canvas._show_position_numbers

    window.position_numbers_action.setChecked(True)
    app.processEvents()
    assert window.image_canvas._show_position_numbers, "the menu item is connected"

    window.position_numbers_action.setChecked(False)
    app.processEvents()
    assert not window.image_canvas._show_position_numbers

    window.close()
    app.processEvents()


@pytest.mark.requires_rmserver_fixture
@pytest.mark.skipif(not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available")
def test_the_position_number_choice_outlives_the_window(tmp_path: Path) -> None:
    """Ticked once, still ticked tomorrow - asked for by the operator.

    Kept the way the workspace panel is kept, in this account's own
    preferences file, because it is the same kind of choice: how one
    person's window looks, nothing about a plan. The test opens a second
    window against the same file rather than reading the field back, since
    saving a value nothing reloads would pass that and fail a person.
    """
    preferences = tmp_path / "preferences.json"
    settings = replace(DEFAULT_SETTINGS, rmserver_root=FIXTURE_ROOT)
    app = QApplication.instance() or QApplication([])

    first = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=settings,
        preferences_store=JsonUserPreferencesStore(preferences),
    )
    assert not first.position_numbers_action.isChecked(), "off for somebody new"
    first.position_numbers_action.setChecked(True)
    app.processEvents()
    assert first.image_canvas._show_position_numbers
    first.close()
    app.processEvents()

    second = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=settings,
        preferences_store=JsonUserPreferencesStore(preferences),
    )
    assert second.position_numbers_action.isChecked(), "the tick came back"
    assert second.image_canvas._show_position_numbers, "and so did the numbers"

    # And turning it off is remembered just as well.
    second.position_numbers_action.setChecked(False)
    app.processEvents()
    second.close()
    app.processEvents()

    third = ViewerWindow(
        RockMakerImageRepository(FIXTURE_ROOT),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=settings,
        preferences_store=JsonUserPreferencesStore(preferences),
    )
    assert not third.position_numbers_action.isChecked()
    assert not third.image_canvas._show_position_numbers
    third.close()
    app.processEvents()
