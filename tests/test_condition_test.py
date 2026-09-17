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
    assert [parse_minutes(text) for text in ("30", "30 min", "1h", "1 h 30 min", "90m")] == [
        30, 30, 60, 90, 90
    ]
    assert format_minutes(90) == "1 h 30 min"
    assert format_minutes(60, compact=True) == "1h"
    with pytest.raises(ValueError):
        parse_minutes("")


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
        ("Control · 0 min", 1), ("Control · 0 min", 2),
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
