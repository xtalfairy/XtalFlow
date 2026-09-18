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
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
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

    # Escape leaves everything as it was.
    _header_click(editor, columns=True, section=4)
    QTest.keyClicks(editor.axis_editor, "40")
    QTest.keyClick(editor.axis_editor, Qt.Key_Escape)
    assert editor.design.series[0].percents == tuple(Decimal(v) for v in ("0", "5", "10", "20"))


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
    dmso = Additive("dmso", "DMSO", Decimal("100"), "", "LDV-1", "A1")
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
    editor.source_plate_input.setText("LDV-1")
    editor.source_plate_input.editingFinished.emit()
    assert not editor.source_plate_input.property("missing")
    assert editor.source_message_label.text() == "Enter the source well; the ECHO worksheet needs it."


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
    assert table.height() <= 360
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
