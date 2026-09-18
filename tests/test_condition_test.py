from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from xtalflow.domain import SWISSCI_MIDI_3_LENS, crystal_selection_from_selected_crystals
from xtalflow.domain.condition_test import (
    Additive,
    ConditionTestDesign,
    Treatment,
    build_condition_test_plan,
    compute_doses,
    cryo_test_template,
    format_minutes,
    new_series,
    parse_minutes,
    solvent_test_template,
)
from xtalflow.domain.fragment_screening import CrystalTarget, SelectedCrystal

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


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


def _design(drop="100", percents=("0", "10"), times=(0, 60), replicates=1) -> ConditionTestDesign:
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    series = new_series(
        dmso.id, tuple(Decimal(value) for value in percents), times, replicates
    )
    return ConditionTestDesign((dmso,), (series,), Decimal(drop))


def test_minutes_are_read_and_written_as_researchers_write_them() -> None:
    assert [parse_minutes(text) for text in ("30", "30 min", "30 minutes", "0.5h", "0.5 hr")] == [
        30, 30, 30, 30, 30
    ]
    assert [parse_minutes(text) for text in ("1h", "1 h", "1hr", "1 hour", "60 mins", "60m")] == [
        60, 60, 60, 60, 60, 60
    ]
    assert [parse_minutes(text) for text in ("1 h 30 min", "1h30", "90m", "1.5 hours")] == [
        90, 90, 90, 90
    ]
    assert format_minutes(90) == "1 h 30 min"
    assert format_minutes(60, compact=True) == "1h"
    with pytest.raises(ValueError):
        parse_minutes("")
    with pytest.raises(ValueError, match="whole number of minutes"):
        parse_minutes("1.51 h")
    for bad in ("soon", "overnight", "1 day", "-30", "30 s"):
        with pytest.raises(ValueError, match="is not a soaking duration"):
            parse_minutes(bad)


def test_final_concentration_becomes_dispensed_volume_with_actual_percent() -> None:
    design = _design(drop="100")
    control, ten_percent = design.conditions()[:2]

    assert compute_doses(design, control) == ()
    (dose,) = compute_doses(design, ten_percent)
    # 10% of a 100 nL drop needs 100·10/90 = 11.1 nL, dispensed as 10 nL (9.1%).
    assert dose.volume_nl == Decimal("10.0")
    assert dose.actual_percent == Decimal("9.1")


def test_combined_treatment_uses_the_enlarged_drop_and_schedules_later_additions() -> None:
    dmso = Additive("dmso", "DMSO", Decimal("100"), "CS(=O)C", "LDV-1", "A1")
    glycerol = Additive("gly", "Glycerol", Decimal("50"), "OCC(O)CO", "LDV-1", "B1")
    series = new_series(
        glycerol.id, (Decimal("20"),), (0,), 1,
        before=(Treatment(dmso.id, Decimal("5"), 60),),
    )
    design = ConditionTestDesign((dmso, glycerol), (series,), Decimal("200"))
    (condition,) = design.conditions()

    first, second = compute_doses(design, condition)

    assert design.label(condition) == "DMSO 5% · 1 h → Glycerol 20% · 0 min"
    assert design.label(condition, compact=True) == "DMSO5%-1h+Glycerol20%-0min"
    assert (first.volume_nl, first.start_minutes) == (Decimal("10.0"), 0)
    # Drop is 210 nL after DMSO; 20% glycerol from a 50% stock needs 140 nL.
    assert (second.volume_nl, second.start_minutes) == (Decimal("140.0"), 60)
    assert condition.total_minutes == 60


def test_impossible_concentrations_are_explained() -> None:
    too_high = _design(percents=("100",))
    tiny = _design(drop="1000", percents=("0.1",))

    with pytest.raises(ValueError, match="not below its 100% stock"):
        compute_doses(too_high, too_high.conditions()[0])
    with pytest.raises(ValueError, match="needs less than 2.5 nL"):
        compute_doses(tiny, tiny.conditions()[0])
    with pytest.raises(ValueError, match="drop volume"):
        compute_doses(_design(drop="0"), _design().conditions()[1])


def test_excluded_cells_keep_identity_and_axes_changes_keep_cells() -> None:
    design = _design(percents=("0", "10"), times=(0, 60), replicates=2)
    series = design.series[0]
    cell = series.cell(Decimal("10"), 60)
    series = series.with_cell(type(cell)(cell.id, cell.percent, cell.minutes, False, 2))

    widened = series.with_axes((Decimal("0"), Decimal("10"), Decimal("20")), (0, 60))

    assert widened.cell(Decimal("10"), 60).id == cell.id
    assert not widened.cell(Decimal("10"), 60).included
    assert len(widened.cells) == 6
    assert sum(condition.replicates for condition in widened.conditions()) == 10


def test_replicates_are_grouped_by_condition_and_extra_wells_are_reported() -> None:
    design = _design(replicates=2)
    plan = build_condition_test_plan(design, _selection(9))

    labels = [
        (design.label(assignment.condition), assignment.replicate)
        for assignment in plan.assignments
    ]
    assert labels[:4] == [
        ("No DMSO added · 0 min", 1), ("No DMSO added · 0 min", 2),
        ("DMSO 10% · 0 min", 1), ("DMSO 10% · 0 min", 2),
    ]
    assert len(plan.unused_wells) == 1
    assert [minute for minute, _ in plan.harvest_groups] == [0, 60]
    assert [minute for minute, _ in plan.dispense_rounds] == [0]
    ((additive, total),) = plan.additive_totals()
    assert (additive.name, total) == ("DMSO", Decimal("40.0"))


def test_plan_refuses_too_few_wells_missing_sources_and_unequal_splits() -> None:
    design = _design(replicates=2)
    with pytest.raises(ValueError, match="8 crystals are needed; 3 wells are selected"):
        build_condition_test_plan(design, _selection(3))

    no_source = ConditionTestDesign(
        (Additive("dmso", "DMSO"),), design.series, design.drop_volume_nl
    )
    with pytest.raises(ValueError, match="source plate and well for DMSO"):
        build_condition_test_plan(no_source, _selection(8))

    with pytest.raises(ValueError, match="cannot be split equally between 3 positions"):
        build_condition_test_plan(_design(replicates=1), _selection(4, positions=3))


def test_design_round_trips_through_json_and_templates_are_ready_to_edit() -> None:
    design = _design(replicates=3)
    assert ConditionTestDesign.from_json(design.to_json()) == design
    assert "default_replicates" not in design.to_json()
    changed = replace(design, default_replicates=1, kind="cryo")
    assert ConditionTestDesign.from_json(changed.to_json()) == changed

    solvent = solvent_test_template()
    cryo = cryo_test_template()
    assert solvent.required_crystals == 32
    assert [format_minutes(value) for value in solvent.series[0].times] == [
        "0 min", "30 min", "1 h", "2 h"
    ]
    assert cryo.required_crystals == 8
    assert solvent.drop_volume_nl is None


def test_condition_worksheets_and_labworks_follow_dispense_and_harvest_times() -> None:
    from xtalflow.domain.labwork import build_condition_test_labworks
    from xtalflow.domain.worksheets import build_condition_echo_rounds, build_shifter_worksheet

    design = _design(percents=("0", "10"), times=(60, 0), replicates=1)
    plan = build_condition_test_plan(design, _selection(4, positions=2))

    ((minute, rows),) = build_condition_echo_rounds(plan)
    assert minute == 0
    assert len(rows) == 4
    assert {row.transfer_volume_nl for row in rows} == {Decimal("5.0")}
    assert {(row.source_plate, row.source_well) for row in rows} == {("LDV-1", "A1")}
    shifter = build_shifter_worksheet(plan)
    harvest = [assignment.harvest_minutes for _m, group in plan.harvest_groups for assignment in group]
    assert harvest == [0, 0, 60, 60]
    assert len(shifter) == 4

    records = build_condition_test_labworks(
        plan, experiment_id="PreTest-202609-BRD4-01", protein_name="BRD4",
        username="jjh", account_id="jjh",
    )
    payload = records[1].to_payload()
    assert (payload["soak_plate"], payload["soak_well"]) == ("pretest", "Z00")
    assert payload["soak_id"] == "DMSO10%-0min-r1"
    assert payload["soak_smile"] == "CS(=O)C"
    assert records[0].to_payload()["soak_smile"] == "none"
    assert records[0].to_payload()["soak_id"] == "NoDMSO-0min-r1"


def test_finalized_condition_test_snapshot_rebuilds_the_same_plan() -> None:
    from xtalflow.application.planning_service import (
        condition_test_plan_snapshot,
        plan_from_snapshot,
    )

    design = _design(percents=("0", "10"), times=(0, 60), replicates=1)
    plan = build_condition_test_plan(design, _selection(4, positions=2))
    snapshot = condition_test_plan_snapshot(plan, "BRD4")

    rebuilt = plan_from_snapshot("plan", snapshot)

    assert condition_test_plan_snapshot(rebuilt, "BRD4") == snapshot
    assert [item.condition.id for item in rebuilt.assignments] == [
        item.condition.id for item in plan.assignments
    ]
    assert rebuilt.assignments[3].doses == plan.assignments[3].doses


def test_zero_percent_is_named_for_what_is_dispensed_not_as_a_control() -> None:
    dmso = Additive("dmso", "DMSO", Decimal("100"), "", "LDV-1", "A1")
    glycerol = Additive("gly", "Glycerol", Decimal("50"), "", "LDV-1", "B1")
    series = new_series(
        glycerol.id, (Decimal("0"),), (30,), 1, before=(Treatment(dmso.id, Decimal("5"), 60),)
    )
    design = ConditionTestDesign((dmso, glycerol), (series,), Decimal("200"))
    (condition,) = design.conditions()

    assert design.label(condition) == "DMSO 5% · 1 h → No additional Glycerol · 30 min"
    assert design.label(_design().conditions()[0]) == "No DMSO added · 0 min"


def test_typed_lists_are_read_without_guessing_and_corrections_keep_condition_ids() -> None:
    from xtalflow.domain.condition_test import parse_minutes_list, parse_percent_list

    assert parse_percent_list("0, 5 %, 7.5") == (Decimal("0"), Decimal("5"), Decimal("7.5"))
    assert parse_minutes_list("0, 30 min, 1 h 30 min") == (0, 30, 90)
    for bad in ("soon", "1 fortnight", "30 s"):
        with pytest.raises(ValueError, match="is not a soaking duration"):
            parse_minutes_list(f"0, {bad}")
    with pytest.raises(ValueError, match="must be from 0 to below 100"):
        parse_percent_list("5, 120")

    series = _design(percents=("0", "10"), times=(0, 60)).series[0]
    cell = series.cell(Decimal("10"), 60)
    corrected = series.with_percent_changed(Decimal("10"), Decimal("12.5")).with_time_changed(60, 90)
    assert corrected.cell(Decimal("12.5"), 90).id == cell.id
    assert corrected.percents == (Decimal("0"), Decimal("12.5"))
    with pytest.raises(ValueError, match="already a column"):
        series.with_percent_changed(Decimal("10"), Decimal("0"))


def _sourced(plate_type="1536LDV_DMSO", wells=("A1",), percents=("20",), replicates=4):
    design = _design(drop="100", percents=percents, times=(0,), replicates=replicates)
    (dmso,) = design.additives
    dmso = replace(
        dmso, source_well=wells[0], extra_source_wells=tuple(wells[1:]),
        source_plate_type=plate_type,
    )
    return replace(design, additives=(dmso,))


def test_source_wells_are_planned_from_the_design_and_match_the_plan() -> None:
    from xtalflow.domain.condition_test import additive_transfers, source_statuses
    from xtalflow.domain.worksheets import build_condition_echo_rounds

    # 20 % of 100 nL needs 25 nL each; a 1536LDV well gives 4.09 µL, so 8 × 25 nL fits in one.
    design = _sourced(replicates=8)
    (status,) = source_statuses(design, additive_transfers(design), require_plate_type=True)
    assert status.problem is None
    assert status.allocation.instruction() == (
        "Fill A1 with 1.3 µL — 1 µL dead volume + 0.2 µL to transfer + 10 % margin."
    )
    plan = build_condition_test_plan(design, _selection(8, positions=2))
    assert plan.source_allocations()["dmso"] == status.allocation
    ((_minute, rows),) = build_condition_echo_rounds(plan)
    assert {row.source_well for row in rows} == {"A1"}
    assert len(rows) == 16  # each dose split over two positions, from the same well


def test_transfers_continue_in_the_next_source_well_and_shortages_name_the_wells_to_add() -> None:
    from xtalflow.domain.condition_test import additive_transfers, source_statuses
    from xtalflow.domain.worksheets import build_condition_echo_rounds

    # 90 % of 100 nL from a 100 % stock is 900 nL: four per 1536LDV well.
    design = _sourced(percents=("90",), replicates=6)
    (status,) = source_statuses(design, additive_transfers(design), require_plate_type=True)
    assert status.problem == "DMSO needs 2 source wells; 1 entered. Add 1 more, e.g. B1."
    with pytest.raises(ValueError, match="DMSO needs 2 source wells"):
        build_condition_test_plan(design, _selection(6))

    design = _sourced(percents=("90",), replicates=6, wells=("A1", "A2"))
    plan = build_condition_test_plan(design, _selection(6))
    ((_minute, rows),) = build_condition_echo_rounds(plan)
    assert [row.source_well for row in rows] == ["A1"] * 4 + ["A2"] * 2


def test_source_wells_must_be_on_the_plate_and_not_shared() -> None:
    from xtalflow.domain.condition_test import additive_transfers, source_statuses

    design = _sourced(plate_type="384PP_DMSO2", wells=("A30",))
    (status,) = source_statuses(design, additive_transfers(design), require_plate_type=True)
    assert status.problem == "DMSO: 'A30' is not a well on a 384-well plate."

    glycerol = Additive("gly", "Glycerol", Decimal("50"), "", "LDV-1", "A1", "384PP_AQ_GP3")
    base = _sourced(plate_type="384PP_DMSO2")
    series = new_series(glycerol.id, (Decimal("10"),), (0,), 1)
    shared = replace(base, additives=(*base.additives, glycerol), series=(*base.series, series))
    statuses = source_statuses(shared, additive_transfers(shared), require_plate_type=True)
    assert statuses[1].problem == "DMSO and Glycerol use the same source well on LDV-1."


def test_tests_without_a_plate_type_keep_their_saved_form_and_worksheets() -> None:
    from xtalflow.domain.condition_test import additive_transfers, source_statuses
    from xtalflow.domain.worksheets import build_condition_echo_rounds

    design = _design(percents=("0", "10"), times=(0,), replicates=1)
    assert "source_plate_type" not in design.to_json()
    assert "extra_source_wells" not in design.to_json()
    (status,) = source_statuses(design, additive_transfers(design), require_plate_type=False)
    assert (status.allocation, status.problem) == (None, None)
    (required,) = source_statuses(design, additive_transfers(design), require_plate_type=True)
    assert required.problem == "Choose the source plate type for DMSO."
    plan = build_condition_test_plan(design, _selection(2))
    ((_minute, rows),) = build_condition_echo_rounds(plan)
    assert [(row.source_plate, row.source_well) for row in rows] == [("LDV-1", "A1")]
    sourced = _sourced(wells=("A1", "A2"))
    assert ConditionTestDesign.from_json(sourced.to_json()) == sourced


def test_a_test_is_fitted_to_fewer_wells_evenly_and_only_when_asked() -> None:
    from xtalflow.domain.condition_test import fit_to_wells

    design = _design(percents=("0", "10"), times=(0, 60), replicates=3)  # 4 conditions × 3
    fitted, changes = fit_to_wells(design, 9)
    assert fitted.required_crystals == 9
    # Three drops come off, from the later conditions first, one each.
    assert [(design.label(condition), old, new) for condition, old, new in changes] == [
        ("DMSO 10% · 0 min", 3, 2), ("No DMSO added · 1 h", 3, 2), ("DMSO 10% · 1 h", 3, 2),
    ]
    # Condition identity is kept; only the counts change.
    assert [condition.id for condition in fitted.conditions()] == [
        condition.id for condition in design.conditions()
    ]
    fitted, changes = fit_to_wells(design, 4)
    assert [condition.replicates for condition in fitted.conditions()] == [1, 1, 1, 1]
    assert fit_to_wells(design, 20) == (design, ())
    with pytest.raises(ValueError, match="3 wells cannot hold 4 conditions"):
        fit_to_wells(design, 3)
