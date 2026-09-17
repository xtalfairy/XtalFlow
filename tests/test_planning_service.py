import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xtalflow.application.planning_service import (
    PlanningService,
    fragment_plan_snapshot,
    raw_crystal_plan_snapshot,
    restored_plan_status,
    saved_plan_status,
)
from xtalflow.domain import (
    PlanType,
    Project,
    SWISSCI_MIDI_3_LENS,
    crystal_selection_from_selected_crystals,
)
from xtalflow.domain.fragment_screening import (
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.plan_lifecycle import PlanningDraft, PlanRevision
from xtalflow.domain.raw_crystal import build_raw_crystal_plan
from xtalflow.infrastructure import SQLiteReviewStore


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _crystals() -> tuple[SelectedCrystal, ...]:
    return (
        SelectedCrystal(
            "image", "1070", "A01a",
            (CrystalTarget("target", Decimal("0.25"), Decimal("-0.5"), NOW),),
            SWISSCI_MIDI_3_LENS.id, "/rmserver/image.jpg",
        ),
    )


def _draft(project: Project, plan_id: str = "plan") -> PlanningDraft:
    return PlanningDraft(
        plan_id, project.id, "raw_crystal", "Plan", None, "", "BRD4", "0",
        "selection", NOW, NOW,
    )


def test_snapshots_are_canonical_and_record_exact_worksheet_inputs() -> None:
    fragment = Fragment(
        "Vendor", "Library", "1", "CMP-1", "C2H6O", Decimal("46.07"), "CCO",
        Decimal("100"), "DMSO", "SRC-1", "A01",
    )
    fragment_plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), _crystals(), Decimal("25")
    )

    fragment_snapshot = fragment_plan_snapshot(
        fragment_plan, "BRD4", "/chems/library.csv", "1-1"
    )
    raw_snapshot = raw_crystal_plan_snapshot(build_raw_crystal_plan(_crystals()), "BRD4")

    assert fragment_snapshot == fragment_plan_snapshot(
        fragment_plan, "BRD4", "/chems/library.csv", "1-1"
    )
    fragment_payload = json.loads(fragment_snapshot)
    assert fragment_payload["assignments"][0]["targets"] == [
        {"id": "target", "x_mm": "0.25", "y_mm": "-0.5",
         "selected_at": NOW.isoformat(), "volume_nl": "25.0"}
    ]
    assert json.loads(raw_snapshot)["selections"][0]["image_path"] == (
        "/rmserver/image.jpg"
    )


def test_plan_status_distinguishes_finalized_changed_and_legacy_plans() -> None:
    revision = PlanRevision("r", "plan", 2, "RawCrystal-01", "{}", "jjh", NOW)

    assert saved_plan_status(revision, "{}", "{}").label == "Finalized r2"
    assert saved_plan_status(revision, "{}", '{"v":2}').list_status == (
        "Draft changes after r2"
    )
    assert saved_plan_status(None, None, "{}").list_status == "Draft"
    assert restored_plan_status(None, "{}", selection_owned=True) is None
    assert restored_plan_status(revision, '{"v":2}', True).label == (
        "Draft changes after r2"
    )
    assert restored_plan_status(revision, "{}", False).list_status == (
        "Legacy · Review selection"
    )


def test_finalize_keeps_experiment_id_and_avoids_remote_ids(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    month = datetime.now()
    taken = f"RawCrystal-{month:%Y%m}-BRD4-01"

    first = service.finalize(
        "plan", PlanType.RAW_CRYSTAL, "BRD4", '{"v":1}', None, "jjh", {taken}
    )
    second = service.finalize(
        "plan", PlanType.RAW_CRYSTAL, "BRD4", '{"v":2}', first.experiment_id, "jjh"
    )

    assert first.experiment_id == f"RawCrystal-{month:%Y%m}-BRD4-02"
    assert (second.revision, second.experiment_id) == (2, first.experiment_id)
    assert service.latest_revision("plan") == second
    store.close()


def test_selection_snapshot_is_stored_as_experiment_project(tmp_path: Path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    service = PlanningService(store.planning)
    selection = crystal_selection_from_selected_crystals("plan", _crystals())

    service.save_selection_snapshot(
        "plan", "Plan", PlanType.FRAGMENT_SCREENING, selection, NOW
    )

    stored = store.planning.load_experiment_project("plan")
    assert stored.plan.plan_type is PlanType.FRAGMENT_SCREENING
    assert stored.crystal_selection.wells[0].image_key == "image"
    store.close()


def test_revision_snapshot_rebuilds_the_exact_plan_for_delivery() -> None:
    from xtalflow.application.planning_service import plan_from_snapshot
    from xtalflow.domain.worksheets import build_echo_worksheet, build_shifter_worksheet

    fragments = tuple(
        Fragment(
            "Vendor", "Library", str(number), f"CMP-{number}", "C2H6O",
            Decimal("46.07"), "CCO", Decimal("100"), "DMSO", "SRC-1", f"A0{number}",
        )
        for number in (1, 2)
    )
    crystals = (
        SelectedCrystal(
            "image-2", "1070", "B01d",
            (CrystalTarget("t2", Decimal("0.2"), Decimal("0"), NOW),
             CrystalTarget("t3", Decimal("-0.1"), Decimal("0.3"), NOW.replace(second=5))),
            SWISSCI_MIDI_3_LENS.id, "/rmserver/b.jpg",
        ),
        *_crystals(),
    )
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", fragments), crystals, Decimal("25")
    )
    snapshot = fragment_plan_snapshot(plan, "BRD4", "/chems/library.csv", "1-2")

    rebuilt = plan_from_snapshot("plan", snapshot)

    assert build_echo_worksheet(rebuilt) == build_echo_worksheet(plan)
    assert build_shifter_worksheet(rebuilt) == build_shifter_worksheet(plan)
    assert fragment_plan_snapshot(rebuilt, "BRD4", "/chems/library.csv", "1-2") == snapshot

    raw_plan = build_raw_crystal_plan(crystals)
    raw_snapshot = raw_crystal_plan_snapshot(raw_plan, "BRD4")
    assert build_shifter_worksheet(plan_from_snapshot("raw", raw_snapshot)) == (
        build_shifter_worksheet(raw_plan)
    )


def test_revision_finalized_with_unequal_volumes_is_still_delivered_as_fixed() -> None:
    from xtalflow.application.planning_service import plan_from_snapshot
    from xtalflow.domain.worksheets import build_echo_worksheet

    target = lambda target_id, volume: {  # noqa: E731 - compact snapshot fixture
        "id": target_id, "x_mm": "0.1", "y_mm": "0", "selected_at": NOW.isoformat(),
        "volume_nl": volume,
    }
    snapshot = json.dumps({
        "schema": 1, "plan_type": "fragment_screening", "protein": "BRD4",
        "library_name": "Library", "library_id": None, "library_rows": "1",
        "volume_per_crystal_nl": "20", "assignment_order": "selection",
        "assignments": [{
            "image_key": "image", "image_path": "", "plate": "1070", "well": "A01a",
            "plate_format_id": SWISSCI_MIDI_3_LENS.id,
            "fragment": {
                "vendor": "V", "library": "L", "number": "1", "compound_id": "CMP-1",
                "formula": "C", "molecular_weight": "46.07", "smiles": "CCO",
                "concentration_mm": "100", "solvent": "DMSO",
                "source_plate": "SRC-1", "source_well": "A01",
            },
            "targets": [target("t1", "5.0"), target("t2", "5.0"), target("t3", "10.0")],
        }],
    })

    rows = build_echo_worksheet(plan_from_snapshot("legacy", snapshot))

    assert [str(row.transfer_volume_nl) for row in rows] == ["5.0", "5.0", "10.0"]
