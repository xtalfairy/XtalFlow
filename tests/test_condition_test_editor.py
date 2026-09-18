import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

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
    table = editor.matrix_table
    return [
        [table.item(row, column).text() for column in range(table.columnCount())]
        for row in range(table.rowCount())
    ]


def test_typed_concentrations_and_durations_add_and_remove_conditions_keeping_ids() -> None:
    editor = _editor()
    kept = editor.design.series[0].cell(Decimal("10"), 60).id

    editor.percents_input.setText("0, 10, 20")
    assert editor.set_axis_text(columns=True)
    editor.times_input.setText("0, 1 h, 2 h")
    assert editor.set_axis_text(columns=False)

    assert editor.matrix_table.columnCount() == 3
    assert editor.matrix_table.verticalHeaderItem(2).text() == "2 h"
    assert editor.design.required_crystals == 18
    assert editor.design.series[0].cell(Decimal("10"), 60).id == kept

    editor.percents_input.setText("10")
    assert editor.set_axis_text(columns=True)
    assert editor.design.required_crystals == 6
    assert editor.design.series[0].cell(Decimal("10"), 60).id == kept


def test_values_that_cannot_be_read_are_shown_at_their_field_and_change_nothing() -> None:
    editor = _editor()
    before = editor.design

    editor.times_input.setText("0, overnight")
    assert not editor.set_axis_text(columns=False)
    assert editor.times_input.property("invalid")
    assert "is not a soaking duration" in editor.times_error_label.text()
    assert "1 h 30 min" in editor.times_error_label.text()  # what to write instead
    assert editor.design == before
    # A later redraw keeps the typed text so it can be corrected.
    editor.refresh_plan()
    assert editor.times_input.text() == "0, overnight"

    editor.percents_input.setText("5, 150")
    assert not editor.set_axis_text(columns=True)
    assert "below 100" in editor.percents_error_label.text()
    assert editor.design == before

    # Hours are read however they are written, and shown back in one spelling.
    editor.times_input.setText("0, 1.5 hours, 2hr")
    assert editor.set_axis_text(columns=False)
    assert editor.design.series[0].times == (0, 90, 120)
    assert editor.times_input.text() == "0 min, 1 h 30 min, 2 h"


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
    editor.condition_include_input.setChecked(False)
    assert table.item(1, 1).text() == "Excluded"
    assert editor.design.required_crystals == 6
    editor.condition_include_input.setChecked(True)
    assert editor.design.required_crystals == 9

    editor.condition_percent_input.setText("12.5")
    editor._condition_percent_edited()
    editor.condition_time_input.setText("90 min")
    editor._condition_time_edited()
    series = editor.design.series[0]
    assert series.cell(Decimal("12.5"), 90).id == cell_id
    assert editor.condition_title_label.text() == "DMSO 12.5% · 1 h 30 min"

    editor.condition_percent_input.setText("0")
    editor._condition_percent_edited()
    assert "already a column" in editor.condition_error_label.text()
    assert editor.design.series[0] == series


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
    editor.percents_input.setText("0, 10, 20")
    editor.set_axis_text(columns=True)
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
    editor.condition_include_input.setChecked(False)
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
    assert table.columnCount() == 15 and table.rowCount() == 20
    assert table.height() <= 360
    assert table.verticalScrollBar().maximum() > 0
    widget.close()
