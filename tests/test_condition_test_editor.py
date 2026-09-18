import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox, QToolButton

from xtalflow.domain import SWISSCI_MIDI_3_LENS, crystal_selection_from_selected_crystals
from xtalflow.domain.condition_test import (
    Additive,
    ConditionTestDesign,
    compute_doses,
    new_series,
    solvent_test_template,
)
from xtalflow.domain.fragment_screening import CrystalTarget, SelectedCrystal
from xtalflow.domain.plan_lifecycle import PlanningDraft

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


_APP: list[QApplication] = []


def _app() -> QApplication:
    # Kept referenced: an application nothing holds is deleted at once.
    if QApplication.instance() is None:
        _APP.append(QApplication([]))
    return QApplication.instance()


def _editor(percents=("0", "10"), times=(0, 60), replicates=2, drop="100"):
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    _app()
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1", "384PP_DMSO2")
    series = new_series(dmso.id, tuple(Decimal(value) for value in percents), times, replicates)
    editor = ConditionTestEditor(ConditionTestDesign((dmso,), (series,), Decimal(drop)))
    # Held: the page owns the fields, and an unreferenced page is deleted.
    editor.page = editor.conditions_widget()
    return editor


def _selection(count: int, positions: int = 1):
    crystals = tuple(
        SelectedCrystal(
            f"image-{index}", "2070", f"A{index + 1:02d}a",
            tuple(
                CrystalTarget(
                    f"t{index}-{number}", Decimal(0), Decimal(0),
                    NOW + timedelta(seconds=index * 10 + number),
                )
                for number in range(positions)
            ),
            SWISSCI_MIDI_3_LENS.id,
        )
        for index in range(count)
    )
    return crystal_selection_from_selected_crystals("plan", crystals)


def _cell_texts(editor):
    """Condition cells only: the last column and row are the "+" that adds values."""
    table = editor.matrix_table
    return [
        [table.item(row, column).text() for column in range(table.columnCount() - 1)]
        for row in range(table.rowCount() - 1)
    ]


def _header_click(editor, columns: bool, section: int, double: bool = False) -> None:
    table = editor.matrix_table
    header = table.horizontalHeader() if columns else table.verticalHeader()
    start = header.sectionViewportPosition(section) + header.sectionSize(section) // 2
    position = QPoint(start, header.height() // 2) if columns else QPoint(header.width() // 2, start)
    if double:
        QTest.mouseDClick(header.viewport(), Qt.LeftButton, pos=position)
    else:
        QTest.mouseClick(header.viewport(), Qt.LeftButton, pos=position)


def test_plus_headers_add_values_in_place_and_keep_existing_conditions() -> None:
    editor = _editor()
    table = editor.matrix_table
    kept = editor.design.series[0].cell(Decimal("10"), 60).id
    assert table.horizontalHeaderItem(2).text() == "+"
    assert table.verticalHeaderItem(2).text() == "+"
    assert not table.item(0, 2).flags() & Qt.ItemIsSelectable

    # "+" opens an editor on that header; several values can go in at once.
    _header_click(editor, columns=True, section=2)
    assert editor.axis_editor.isVisibleTo(editor.matrix_table)
    # The "+" column widens for typing; the last value's header stays uncovered.
    header = table.horizontalHeader()
    last_value_end = header.viewport().mapTo(
        table, QPoint(header.sectionViewportPosition(1) + header.sectionSize(1), 0)
    ).x()
    assert editor.axis_editor.geometry().left() >= last_value_end
    assert header.sectionSize(2) == header.sectionSize(1) == editor.axis_editor.width()
    QTest.keyClicks(editor.axis_editor, "20, 5")
    QTest.keyClick(editor.axis_editor, Qt.Key_Return)
    assert not editor.axis_editor.isVisibleTo(editor.matrix_table)
    assert [table.horizontalHeaderItem(index).text() for index in range(5)] == [
        "0 %", "5 %", "10 %", "20 %", "+"
    ]
    # The new column sorted into place and is selected, so it is easy to find.
    assert editor.condition_title_label.text() == "DMSO 5% · 0 min"

    _header_click(editor, columns=False, section=2)
    QTest.keyClicks(editor.axis_editor, "2 h")
    QTest.keyClick(editor.axis_editor, Qt.Key_Return)
    assert table.verticalHeaderItem(2).text() == "2 h"
    assert editor.design.required_crystals == 4 * 3 * 2
    assert editor.design.series[0].cell(Decimal("10"), 60).id == kept

    # Escape leaves everything as it was, and the "+" column narrow again.
    _header_click(editor, columns=True, section=4)
    QTest.keyClicks(editor.axis_editor, "40")
    QTest.keyClick(editor.axis_editor, Qt.Key_Escape)
    assert editor.design.series[0].percents == tuple(Decimal(v) for v in ("0", "5", "10", "20"))
    assert header.sectionSize(4) == 36


def test_double_clicking_a_header_corrects_that_value_and_keeps_its_conditions() -> None:
    editor = _editor(percents=("0", "10"), times=(0, 60), replicates=2)
    editor.select_condition(1, 1)
    editor.include_button.click()
    cell_id = editor.design.series[0].cell(Decimal("10"), 60).id

    _header_click(editor, columns=True, section=1, double=True)
    assert editor.axis_editor.text() == "10"
    editor.axis_editor.setText("12.5")
    assert editor.commit_axis_editor()
    corrected = editor.design.series[0].cell(Decimal("12.5"), 60)
    assert corrected.id == cell_id and not corrected.included

    _header_click(editor, columns=False, section=1, double=True)
    assert editor.axis_editor.text() == "1 h"
    editor.axis_editor.setText("1.5 hours")
    assert editor.commit_axis_editor()
    assert editor.design.series[0].cell(Decimal("12.5"), 90).id == cell_id
    assert editor.matrix_table.verticalHeaderItem(1).text() == "1 h 30 min"


def _hover(header, position) -> None:
    QApplication.sendEvent(
        header.viewport(),
        QMouseEvent(QEvent.MouseMove, position, Qt.NoButton, Qt.NoButton, Qt.NoModifier),
    )


def test_hovering_a_header_offers_removal_of_that_column_or_row() -> None:
    editor = _editor(percents=("0", "10", "20"), times=(0, 60), replicates=1)
    editor.page.resize(1000, 600)
    editor.page.show()
    _app().processEvents()
    table = editor.matrix_table
    header = table.horizontalHeader()
    middle = header.sectionViewportPosition(1) + header.sectionSize(1) // 2
    _hover(header, QPoint(middle, header.height() // 2))
    assert editor.axis_remove_button.isVisible()
    assert editor.axis_remove_button.toolTip() == "Remove the 10 % column"
    editor.axis_remove_button.click()
    assert editor.design.series[0].percents == (Decimal("0"), Decimal("20"))
    # Nothing to remove over the "+" itself.
    plus = header.sectionViewportPosition(2) + header.sectionSize(2) // 2
    _hover(header, QPoint(plus, header.height() // 2))
    assert not editor.axis_remove_button.isVisible()
    editor.page.close()


def test_values_that_cannot_be_read_are_shown_at_the_header_and_change_nothing() -> None:
    editor = _editor()
    before = editor.design

    editor.open_axis_editor(columns=False)
    editor.axis_editor.setText("0, overnight")
    assert not editor.commit_axis_editor()
    assert editor.axis_editor.isVisibleTo(editor.matrix_table)
    assert editor.axis_editor.property("invalid")
    assert "is not a soaking duration" in editor.axis_error_label.text()
    assert "1 h 30 min" in editor.axis_error_label.text()  # what to write instead
    assert editor.design == before

    editor.open_axis_editor(columns=True, value=Decimal("10"))
    editor.axis_editor.setText("150")
    assert not editor.commit_axis_editor()
    assert "below 100" in editor.axis_error_label.text()
    editor.axis_editor.setText("0")
    assert not editor.commit_axis_editor()
    assert "already a column" in editor.axis_error_label.text()
    assert editor.design == before

    # Hours are read however they are written, and shown back in one spelling.
    editor.open_axis_editor(columns=False)
    editor.axis_editor.setText("1.5 hours, 2hr")
    assert editor.commit_axis_editor()
    assert editor.design.series[0].times == (0, 60, 90, 120)
    assert editor.matrix_table.verticalHeaderItem(2).text() == "1 h 30 min"


def test_invalid_earlier_treatment_time_is_not_saved_as_zero() -> None:
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    _app()
    dmso = Additive("dmso", "DMSO", Decimal("100"), "", "LDV-1", "A1", "384PP_DMSO2")
    glycerol = Additive("gly", "Glycerol", Decimal("50"), "", "LDV-1", "B1")
    design = ConditionTestDesign(
        (dmso, glycerol),
        (new_series(glycerol.id, (Decimal("0"), Decimal("20")), (30,), 1),),
        Decimal("200"),
    )
    editor = ConditionTestEditor(design)
    # Held: the page owns the fields, and an unreferenced page is deleted.
    editor.page = editor.conditions_widget()

    editor.before_additive_input.setCurrentIndex(editor.before_additive_input.findData("dmso"))
    editor.before_percent_input.setValue(5)
    editor.before_minutes_input.setText("soon")
    assert not editor.apply_earlier_treatment()
    assert editor.design.series[0].before == ()
    assert editor.before_minutes_input.property("invalid")
    assert "is not a soaking duration" in editor.before_error_label.text()

    editor.before_minutes_input.setText("1 h")
    assert editor.apply_earlier_treatment()
    (treatment,) = editor.design.series[0].before
    assert (treatment.additive_id, treatment.final_percent, treatment.minutes) == (
        "dmso", Decimal("5"), 60
    )
    assert not editor.before_error_label.isVisibleTo(editor.before_error_label.parentWidget())
    editor.select_condition(0, 0)
    assert editor.condition_title_label.text() == "DMSO 5% · 1 h → No additional Glycerol · 30 min"
    assert editor.harvest_label.text() == "T+1 h 30 min · 30 min after the Glycerol dispense at T+1 h"


def test_selecting_a_condition_edits_it_beside_the_table_without_toggling() -> None:
    editor = _editor(percents=("0", "10"), times=(0, 60), replicates=2)
    table = editor.matrix_table
    cell_id = editor.design.series[0].cell(Decimal("10"), 60).id

    rect = table.visualItemRect(table.item(1, 1))
    QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=rect.center())
    assert editor._selected_cell_id == cell_id
    assert table.item(1, 1).text() == "2 wells"
    assert editor.condition_title_label.text() == "DMSO 10% · 1 h"
    assert editor.requested_label.text() == "10 % DMSO"
    # 10% of 100 nL needs 11.1 nL, dispensed as 10 nL: the same numbers the worksheet uses.
    (dose,) = compute_doses(editor.design, editor._condition_for(editor._selected_cell()))
    assert editor.transfer_label.text() == f"{dose.volume_nl.normalize():f} nL" == "10 nL"
    assert editor.actual_label.text() == f"{dose.actual_percent} % DMSO" == "9.1 % DMSO"
    assert editor.harvest_label.text() == "T+1 h after dispensing"

    editor.condition_wells_input.setValue(3)
    assert table.item(1, 1).text() == "3 wells"
    editor.include_button.click()
    assert table.item(1, 1).text() == "Excluded"
    assert editor.include_button.text() == "+"
    assert editor.condition_state_label.text() == "Excluded from this test"
    assert editor.design.required_crystals == 6
    editor.include_button.click()
    assert editor.include_button.text() == "×"
    assert editor.design.required_crystals == 9

    # The panel reports this well; the column and row are changed at the axis.
    assert editor.condition_title_label.text() == "DMSO 10% · 1 h"
    assert not hasattr(editor, "condition_percent_input")
    assert not hasattr(editor, "remove_percent_button")


def test_keyboard_moves_selection_and_changes_the_selected_condition() -> None:
    editor = _editor(percents=("0", "10"), times=(0, 60), replicates=2)
    table = editor.matrix_table
    table.setFocus()
    editor.select_condition(0, 0)

    QTest.keyClick(table, Qt.Key_Right)
    QTest.keyClick(table, Qt.Key_Down)
    assert editor.condition_title_label.text() == "DMSO 10% · 1 h"
    QTest.keyClick(table, Qt.Key_Plus)
    assert table.item(1, 1).text() == "3 wells"
    QTest.keyClick(table, Qt.Key_Minus)
    QTest.keyClick(table, Qt.Key_Minus)
    assert table.item(1, 1).text() == "1 well"
    QTest.keyClick(table, Qt.Key_Space)
    assert table.item(1, 1).text() == "Excluded"
    assert editor.design.required_crystals == 6


def test_zero_percent_is_labelled_for_what_is_dispensed() -> None:
    editor = _editor()
    editor.select_condition(0, 0)
    assert editor.condition_title_label.text() == "No DMSO added · 0 min"
    assert editor.transfer_label.text() == "Nothing (no DMSO added)"
    assert editor.harvest_label.text() == "T+0 min · as soon as possible after dispensing"
    assert editor.matrix_table.item(0, 0).toolTip() == "No DMSO added · 0 min · 2 wells"


def test_wells_per_condition_follows_conditions_left_at_the_default() -> None:
    editor = _editor(percents=("0", "10"), times=(0, 60), replicates=2)
    editor.select_condition(1, 1)
    editor.condition_wells_input.setValue(4)

    editor.replicates_input.setValue(3)

    assert _cell_texts(editor) == [["3 wells", "3 wells"], ["3 wells", "4 wells"]]
    assert editor.design.required_crystals == 13
    assert editor.add_axis_values("20", columns=True)
    assert editor.matrix_table.item(0, 2).text() == "3 wells"


def test_positions_are_not_replicates_and_short_or_spare_wells_are_reported() -> None:
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)

    # Three wells with two positions each are still three wells, not six replicates.
    editor.set_selection(_selection(3, positions=2))
    assert editor.current_plan is None
    assert editor.selection_error == "4 crystals are needed; 3 wells are selected."
    assert editor.design.required_crystals == 4

    editor.set_selection(_selection(6))
    plan = editor.current_plan
    assert len(plan.assignments) == 4
    assert len(plan.unused_wells) == 2


def test_missing_source_and_drop_volume_are_marked_where_they_are_entered() -> None:
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    _app()
    editor = ConditionTestEditor(solvent_test_template())
    # Held: the page owns the fields, and an unreferenced page is deleted.
    editor.page = editor.conditions_widget()

    assert editor.design_error == "Enter the initial drop volume."
    assert editor.conditions_drop_input.property("missing")
    assert editor.drop_volume_input.property("missing")
    assert not editor.design_error_label.isVisibleTo(editor.design_error_label.parentWidget())
    editor.conditions_drop_input.setValue(150)
    assert editor.drop_volume_input.value() == 150
    assert editor.design.drop_volume_nl == Decimal("150")
    assert editor.source_plate_input.property("missing")
    assert editor.source_well_input.property("missing")
    editor.enter_new_plate("LDV-1")
    assert not editor.source_plate_input.property("missing")
    assert editor.source_message_label.text() == (
        "Enter the source plate type and wells; the ECHO worksheet needs them."
    )


def test_draft_restores_the_design_selection_and_condition_ids() -> None:
    from xtalflow.ui.condition_test_editor import ConditionTestEditor

    editor = _editor(percents=("0", "10"), times=(0, 60), replicates=2)
    editor.select_condition(1, 1)
    editor.include_button.click()
    editor.replicates_input.setValue(1)
    draft = PlanningDraft(
        "plan", "workspace", "condition_test", "Solvent Test", None, "", "BRD4", "",
        "selection", NOW, NOW, details_json=editor.design.to_json(),
    )

    restored = ConditionTestEditor(solvent_test_template())
    restored.page = restored.conditions_widget()
    restored.restore_draft(draft)

    assert restored.design == editor.design
    assert _cell_texts(restored) == [["1 well", "1 well"], ["1 well", "Excluded"]]
    assert restored.replicates_input.value() == 1
    assert [cell.id for cell in restored.design.series[0].cells] == [
        cell.id for cell in editor.design.series[0].cells
    ]


def test_large_matrix_scrolls_inside_its_bounds() -> None:
    editor = _editor(
        percents=tuple(str(value) for value in range(0, 30, 2)),
        times=tuple(range(0, 600, 30)),
        replicates=1,
        drop="1000",
    )
    widget = editor.page
    widget.resize(1000, 700)
    widget.show()
    _app().processEvents()
    table = editor.matrix_table
    # One more column and row: the "+" that adds values.
    assert table.columnCount() == 16 and table.rowCount() == 21
    # Rows stop at 360 px; a horizontal scroll bar, when columns overflow, comes on top.
    bar = table.horizontalScrollBar()
    assert table.height() <= 360 + (bar.height() if bar.isVisible() else 0)
    assert table.verticalScrollBar().maximum() > 0
    widget.close()


def test_each_solvent_chip_removes_that_solvent_after_asking(monkeypatch) -> None:
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    assert [chip.text() for chip in editor.series_group.buttons()] == ["DMSO"]
    editor.add_series_button.click()
    editor.add_series_button.click()
    names = [editor.design.additive(item.additive_id).name for item in editor.design.series]
    assert names == ["DMSO", "Solvent 2", "Solvent 3"]
    chips = editor.series_group.buttons()
    assert [chip.text() for chip in chips] == names
    assert editor._series_index == 2

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.No,
    )
    chips[0].findChild(QToolButton, "ChipClose").click()
    assert "Remove DMSO from this test?" in asked[0]
    assert "2 conditions (4 wells)" in asked[0]
    assert len(editor.design.series) == 3  # Answering no changes nothing.

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    # Not only the last one: the first chip removes the first solvent.
    editor.series_group.buttons()[0].findChild(QToolButton, "ChipClose").click()
    assert [
        editor.design.additive(item.additive_id).name for item in editor.design.series
    ] == ["Solvent 2", "Solvent 3"]
    assert [item.name for item in editor.design.additives] == ["Solvent 2", "Solvent 3"]
    # The solvent that was open stays open.
    assert editor._series_index == 1
    assert editor.additive_name_input.text() == "Solvent 3"

    editor.series_group.buttons()[1].findChild(QToolButton, "ChipClose").click()
    assert [item.name for item in editor.design.additives] == ["Solvent 2"]
    # The last solvent keeps its chip, but cannot be removed.
    (chip,) = editor.series_group.buttons()
    assert chip.text() == "Solvent 2"
    assert not chip.findChild(QToolButton, "ChipClose").isEnabled()



def test_source_box_comes_last_and_says_how_to_fill_the_source_wells() -> None:
    editor = _editor(percents=("0", "10"), times=(0, 60), replicates=2)
    page = editor.page.widget()
    items = [page.layout().itemAt(index) for index in range(page.layout().count())]
    placed = [item.layout() for item in items if item.layout() is not None]
    last = placed[-1]
    assert last.itemAt(0).widget() is editor.source_box

    # What to have ready, on top: crystal drops, and how much of each solvent they get.
    assert editor.preparation_rows() == [
        "Crystal drops · 8 (4 conditions × 2), one per well",
        "DMSO · 0.04 µL in total, into 4 crystal drops",
    ]
    # With the plate chosen, which wells to fill, from its dead volume and maximum:
    # 4 transfers of 10 nL from a 384PP well: 15 µL dead volume + 0.04 µL + 10 %.
    assert editor.source_message_label.text() == (
        "Fill A1 with 15.1 µL — 15 µL dead volume + 0.04 µL to transfer + 10 % margin."
    )
    assert not editor.source_message_label.property("attention")

    editor.update_additive(source_plate_type="")
    assert editor.design_error == "Choose the source plate type for DMSO."
    assert editor.source_type_input.property("missing")
    assert not editor.design_error_label.isVisibleTo(editor.page)  # said in the source box


def test_several_source_wells_are_asked_for_when_one_is_not_enough() -> None:
    # 90 % of 100 nL from a 100 % stock is 900 nL; a 1536LDV well gives four of them.
    editor = _editor(percents=("90",), times=(0, 60), replicates=3)
    editor.source_type_input.setCurrentIndex(editor.source_type_input.findData("1536LDV_DMSO"))
    editor.source_type_input.activated.emit(editor.source_type_input.currentIndex())
    assert editor.source_message_label.text() == (
        "DMSO needs 2 source wells; 1 entered. Add 1 more, e.g. B1."
    )
    assert editor.source_message_label.property("attention")
    assert editor.design_error == "DMSO needs 2 source wells; 1 entered. Add 1 more, e.g. B1."

    editor.source_well_input.setText("a1, AF48")
    editor.source_well_input.editingFinished.emit()
    assert editor.design.additives[0].source_wells == ("A1", "AF48")
    assert editor.design_error is None
    assert editor.preparation_rows()[1] == "DMSO · 5.4 µL in total, into 6 crystal drops"
    assert editor.source_message_label.text() == (
        "Fill A1 with 5 µL and AF48 with 3 µL — each 1 µL dead volume + its transfers + 10 % margin."
    )

    # A well that is not on the plate is refused where it was typed.
    editor.source_well_input.setText("A1, AG1")
    editor.source_well_input.editingFinished.emit()
    assert editor.source_well_input.property("invalid")
    assert editor.source_message_label.text() == "'AG1' is not a well on a 1536-well plate."
    assert editor.design.additives[0].source_wells == ("A1", "AF48")


def _shared_plate(tmp_path, entries=()):
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord, record_directory

    (tmp_path / "echo650").mkdir()
    record = SourcePlateRecord(record_directory(tmp_path / "echo650"))
    if entries:
        record.append("SOLVENT-01", entries)
    return record


def _entry(kind, wells, experiment="", reason=""):
    from xtalflow.domain.echo_source import PlateRecordEntry

    return PlateRecordEntry(kind, tuple(wells), "kim", NOW, experiment, "DMSO", reason)


def test_next_wells_come_after_the_last_used_well_on_the_shared_plate(tmp_path) -> None:
    record = _shared_plate(
        tmp_path, [_entry("used", ["A1", "B1"], "PreTest-01"), _entry("skipped", ["D1"], reason="Damaged")]
    )
    # 90 % of 100 nL is 900 nL; four fit a 1536LDV well, so six need two wells.
    editor = _editor(percents=("90",), times=(0, 60), replicates=3)
    editor.set_source_record(record, "lee")
    editor.update_additive(source_well="", extra_source_wells=())
    _choose_plate(editor, "SOLVENT-01")
    # On the 384PP plate it came with, one well is enough.
    assert editor.design.additives[0].source_wells == ("C1",)
    editor.source_type_input.setCurrentIndex(editor.source_type_input.findData("1536LDV_DMSO"))
    editor.source_type_input.activated.emit(editor.source_type_input.currentIndex())

    # A 1536LDV well holds less, so two. Down the column: A1, B1 used, C1 free,
    # D1 skipped, then E1.
    assert editor.design.additives[0].source_wells == ("C1", "E1")
    assert editor.design_error is None

    # A well someone used is refused when typed.
    editor.source_well_input.setText("B1, C1")
    editor.source_well_input.editingFinished.emit()
    assert editor.design_error == (
        "SOLVENT-01 B1 was used by kim on 2026-09-17 (PreTest-01). Use next wells picks free ones."
    )
    editor.next_wells_button.click()
    assert editor.design.additives[0].source_wells == ("C1", "E1")


def test_a_skipped_well_is_recorded_for_everyone_and_replaced(tmp_path) -> None:
    record = _shared_plate(tmp_path)
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    editor.set_source_record(record, "lee")
    editor.update_additive(source_plate="SOLVENT-01", source_well="A1")

    editor.skip_button.click()
    assert editor.skip_well_input.text() == "A1"
    editor.skip_reason_input.setCurrentText("Dust or debris")
    assert editor.skip_well()
    (skipped,) = record.entries("SOLVENT-01")
    assert (skipped.kind, skipped.wells, skipped.user, skipped.reason) == (
        "skipped", ("A1",), "lee", "Dust or debris"
    )
    assert editor.design.additives[0].source_wells == ("B1",)
    assert editor.record_note_label.text() == "SOLVENT-01 A1 skipped: Dust or debris."


def test_a_skip_waits_for_the_worksheets_when_the_shared_record_is_unreachable(tmp_path) -> None:
    from xtalflow.infrastructure.source_plate_record import SourcePlateRecord, record_directory

    record = SourcePlateRecord(record_directory(tmp_path / "missing" / "echo650"))
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    editor.set_source_record(record, "lee")
    editor.update_additive(source_plate="SOLVENT-01", source_well="A1")
    assert "cannot be reached" in editor.record_note_label.text()

    editor.skip_well_input.setText("A1")
    assert editor.skip_well()
    assert [entry.wells for entry in editor.pending_skips["SOLVENT-01"]] == [("A1",)]
    assert editor.record_note_label.text() == (
        "SOLVENT-01 A1 skipped: Dust or debris. It will be recorded when worksheets are saved."
    )
    assert editor.design.additives[0].source_wells == ("B1",)


def _plate_entry(wells, plate_type="1536LDV_DMSO"):
    from xtalflow.domain.echo_source import PlateRecordEntry

    return PlateRecordEntry("used", tuple(wells), "kim", NOW, "PreTest-01", "DMSO", plate_type=plate_type)


def _choose_plate(editor, data):
    combo = editor.source_plate_input
    combo.opening.emit()
    index = combo.findData(data)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)


def test_a_shared_plate_is_chosen_from_the_plate_list_with_its_type_and_next_wells(tmp_path) -> None:
    record = _shared_plate(tmp_path, [_plate_entry(["A1", "B1"])])
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    editor.update_additive(source_plate="", source_well="")
    editor.set_source_record(record, "lee")

    combo = editor.source_plate_input
    combo.opening.emit()
    assert [combo.itemText(index) for index in range(combo.count())] == [
        "Choose plate", "SOLVENT-01 · 1536LDV · 2 wells used, next C1", "", "New plate…"
    ]
    _choose_plate(editor, "SOLVENT-01")
    additive = editor.design.additives[0]
    assert (additive.source_plate, additive.source_plate_type, additive.source_wells) == (
        "SOLVENT-01", "1536LDV_DMSO", ("C1",)
    )
    assert combo.currentText() == "SOLVENT-01 · 1536LDV · 2 wells used, next C1"


def test_new_plate_asks_for_a_name_and_a_shared_name_continues_that_plate(tmp_path) -> None:
    from xtalflow.ui.condition_test_editor import NEW_PLATE

    record = _shared_plate(tmp_path, [_plate_entry(["A1", "B1"])])
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    editor.set_source_record(record, "lee")

    # Chosen from the open list, the Plate field itself becomes a text field and
    # keeps the focus, so typing goes there and not into Wells.
    editor.page.show()
    combo = editor.source_plate_input
    wells_before = editor.source_well_input.text()

    def choose_new_plate():
        combo.setFocus()
        combo.showPopup()
        _app().processEvents()
        for _ in range(combo.findData(NEW_PLATE) - combo.currentIndex()):
            QTest.keyClick(combo.view(), Qt.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key_Return)
        for _ in range(5):
            _app().processEvents()

    choose_new_plate()
    assert combo.isEditable() and combo.currentText() == ""
    # An editable combo keeps the focus itself and passes typing to its text field.
    assert QApplication.focusWidget() in (combo, combo.lineEdit())
    assert editor.new_plate_cancel_button.isVisible()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Escape)
    assert not combo.isEditable() and not editor.new_plate_cancel_button.isVisible()

    editor.enter_new_plate("solvent-01")
    assert editor.design.additives[0].source_plate == "SOLVENT-01"
    assert editor.record_note_label.text() == "SOLVENT-01 is already a shared plate; it is continued."
    assert not combo.isEditable()

    choose_new_plate()
    QTest.keyClicks(QApplication.focusWidget(), "SOLVENT-02")
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)
    additive = editor.design.additives[0]
    assert (additive.source_plate, additive.source_wells) == ("SOLVENT-02", ("A1",))
    assert combo.currentText() == "SOLVENT-02 · not a shared plate yet"
    assert not combo.isEditable()
    assert wells_before != "SOLVENT-02" and "SOLVENT" not in editor.source_well_input.text()
    editor.page.close()


def test_a_plate_type_that_does_not_match_the_shared_plate_is_refused(tmp_path) -> None:
    record = _shared_plate(tmp_path, [_plate_entry(["A1"])])
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    editor.set_source_record(record, "lee")
    editor.choose_shared_plate(record.plates()[0])

    editor.update_additive(source_plate_type="384PP_DMSO2")
    assert editor.design_error == (
        "SOLVENT-01 is a 1536LDV plate; choose a 1536LDV plate type for DMSO."
    )



def test_while_no_plate_is_chosen_the_source_box_says_where_shared_plates_are(tmp_path) -> None:
    record = _shared_plate(tmp_path)
    editor = _editor(percents=("0", "10"), times=(0,), replicates=2)
    editor.update_additive(source_plate="", source_well="")
    editor.set_source_record(record, "lee")
    assert editor.source_message_label.text().endswith(
        "No shared plates yet: choose New plate… in Plate to start one."
    )

    record.append("SOLVENT-01", [_plate_entry(["A1"])])
    editor.refresh_plate_records()
    assert editor.source_message_label.text().endswith(
        "Choose one of 1 shared plate in Plate, or start one with New plate…"
    )
