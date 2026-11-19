import json
import sqlite3
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xtalflow.application import ReviewPersistenceError
from xtalflow.application.planning_service import (
    PlanningService,
    fragment_plan_snapshot,
    raw_crystal_plan_snapshot,
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


def test_plan_status_distinguishes_finalized_and_changed_plans() -> None:
    revision = PlanRevision("r", "plan", 2, "RawCrystal-01", "{}", "jjh", NOW)

    assert saved_plan_status(revision, "{}", "{}").label == "Finalized r2"
    assert saved_plan_status(revision, "{}", "{}").finalized
    assert saved_plan_status(revision, "{}", '{"v":2}').label == "Draft changes after r2"
    assert saved_plan_status(None, None, "{}").label == "Draft · saved"


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


def _two_condition_snapshot() -> str:
    """A snapshot whose assignments sit under two conditions."""
    return json.dumps(
        {
            "design": {"v": 1},
            "protein": "BRD4",
            "assignments": [
                {"image_key": "w1", "condition_id": "soon", "replicate": 1},
                {"image_key": "w2", "condition_id": "soon", "replicate": 2},
                {"image_key": "w3", "condition_id": "late", "replicate": 1},
            ],
        }
    )


def test_a_part_of_a_revision_never_becomes_the_plan(tmp_path: Path) -> None:
    """The worksheets and the source wells belong to the whole revision.

    A part holds some of its crystals so they can be uploaded under their
    own experiment ID. If a part became the latest revision, the editor
    would read "Draft changes after rN" and refuse to save worksheets or
    upload - and the plan it composed would no longer be the plate that was
    dispensed.
    """
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    whole = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4", _two_condition_snapshot(),
        None, "jjh",
    )

    ahead, later = service.split_for_upload(
        whole, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    assert service.latest_revision("plan") == whole
    assert service.parts_of(whole) == (ahead, later)
    # Both parts are stored, so nothing was lost.
    assert len(store.planning.list_plan_revisions("plan")) == 3
    store.close()


def test_the_part_that_goes_ahead_keeps_the_experiment_id(tmp_path: Path) -> None:
    """Its upload is the one this experiment was always going to have.

    The crystals held back take the next free ID, and because every
    revision's ID is reserved, the part written first is already accounted
    for by the time the second is named.
    """
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    whole = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4", _two_condition_snapshot(),
        None, "jjh",
    )

    ahead, later = service.split_for_upload(
        whole, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    assert ahead.experiment_id == whole.experiment_id
    assert later.experiment_id != whole.experiment_id
    # The sequence number, not a letter: experiment_id_holds_protein cannot
    # read an ID ending in a letter, which silently disables the rename
    # warning and the collision avoidance.
    assert later.experiment_id.endswith("-02")
    assert ahead.experiment_id.endswith("-01")


def test_each_part_carries_only_its_own_crystals(tmp_path: Path) -> None:
    """What a part uploads is what plan_from_snapshot builds from its assignments.

    The design stays whole on both sides: a design with conditions removed
    would no longer describe the plate that was dispensed.
    """
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    whole = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4", _two_condition_snapshot(),
        None, "jjh",
    )

    ahead, later = service.split_for_upload(
        whole, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    assert [
        item["image_key"] for item in json.loads(ahead.snapshot_json)["assignments"]
    ] == ["w1", "w2"]
    assert [
        item["image_key"] for item in json.loads(later.snapshot_json)["assignments"]
    ] == ["w3"]
    assert json.loads(ahead.snapshot_json)["design"] == {"v": 1}
    assert json.loads(later.snapshot_json)["design"] == {"v": 1}
    store.close()


def test_a_snapshot_that_cannot_be_read_is_not_taken_for_a_part(tmp_path: Path) -> None:
    """Hiding a revision somebody finalized is the worse answer.

    A part is recognized by a key in its snapshot, so a snapshot that will
    not parse has to count as whole; otherwise an unreadable revision would
    disappear from the plan and the editor would offer to finalize over it.
    """
    from xtalflow.application.planning_service import is_part_revision

    class Broken:
        snapshot_json = "{not json"

    class Missing:
        snapshot_json = None

    assert not is_part_revision(Broken())
    assert not is_part_revision(Missing())


def test_the_late_half_is_named_beside_the_experiment_not_after_today(
    tmp_path: Path, monkeypatch
) -> None:
    """A plate finalized on the last of the month is uploaded the next morning.

    That is what an overnight soak is, so it is the ordinary case: naming
    the late half from today's date and the protein gave it next month's
    stamp and a sequence of 01, a stranger to the experiment it belongs
    beside. It is built from the ID instead.
    """
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    whole = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4", _two_condition_snapshot(),
        "PreTest-202611-BRD4-01", "jjh",
    )
    # The morning after, in the next month.
    monkeypatch.setattr(
        "xtalflow.application.planning_service.datetime", _December
    )

    ahead, later = service.split_for_upload(
        whole, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    assert ahead.experiment_id == "PreTest-202611-BRD4-01"
    assert later.experiment_id == "PreTest-202611-BRD4-02"
    store.close()


class _December(datetime):
    """The clock a month after the experiment was named."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 12, 1, 9, 0, tzinfo=tz)


def test_a_protein_name_the_rules_no_longer_allow_can_still_be_split(
    tmp_path: Path,
) -> None:
    """An experiment already underway must not be stranded by tightened rules.

    The naming rules are allowed to tighten, and a dot is no longer accepted
    - but an experiment named before that keeps its ID and every later
    revision reuses it. Taking the ID apart asks nothing of the name again,
    while building a new one from the protein raised and left the plate
    half split.
    """
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    whole = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4.2", _two_condition_snapshot(),
        "PreTest-202611-BRD4.2-01", "jjh",
    )

    ahead, later = service.split_for_upload(
        whole, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    assert ahead.experiment_id == "PreTest-202611-BRD4.2-01"
    assert later.experiment_id == "PreTest-202611-BRD4.2-02"
    store.close()


def test_a_split_that_cannot_be_written_whole_writes_nothing(tmp_path: Path) -> None:
    """One part alone would read as a finished split.

    parts_of would answer with it, the question would never be asked again,
    and the crystals left out would never reach MxLive - while the screen
    said the experiment was uploaded. So both rows go in one transaction.
    """
    from xtalflow.domain.plan_lifecycle import PlanRevision

    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    good = PlanRevision(
        "r-good", "plan", 0, "PreTest-202611-BRD4-01", _two_condition_snapshot(),
        "jjh", datetime.now(timezone.utc),
    )
    # A plan nobody drafted: the revision references the draft row, so this
    # one cannot be written.
    doomed = PlanRevision(
        "r-doomed", "no-such-plan", 0, "PreTest-202611-BRD4-02",
        _two_condition_snapshot(), "jjh", datetime.now(timezone.utc),
    )

    with pytest.raises(ReviewPersistenceError):
        store.planning.finalize_plan_revisions((good, doomed))

    assert store.planning.list_plan_revisions("plan") == ()
    store.close()


def test_finalizing_again_reuses_the_name_the_late_crystals_went_out_under(
    tmp_path: Path,
) -> None:
    """The late ID belongs to the plan, not to one split.

    A plan is finalized again to retry an upload, or because something was
    wrong. Minting a fresh ID for its late half would put the same crystals
    on MxLive under two names, and nothing can take either back - so a
    later split reuses the name, and its own upload lock refuses a second
    send.
    """
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3")
    project = Project.create("Planning")
    store.workspace.save_project(project)
    store.planning.save_planning_draft(_draft(project))
    service = PlanningService(store.planning)
    first = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4", _two_condition_snapshot(),
        None, "jjh",
    )
    _, late = service.split_for_upload(
        first, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    again = service.finalize(
        "plan", PlanType.CONDITION_TEST, "BRD4", _two_condition_snapshot(),
        first.experiment_id, "jjh",
    )
    _, late_again = service.split_for_upload(
        again, held_back=frozenset({"late"}), goes_ahead=frozenset({"soon"}),
        finalized_by="jjh",
    )

    assert late_again.experiment_id == late.experiment_id
    assert service.late_part_id("plan") == late.experiment_id
    store.close()


def test_a_part_with_nobody_in_it_is_refused(tmp_path: Path) -> None:
    """A part holding no crystals is not a part of anything."""
    from xtalflow.application.planning_service import snapshot_for_part

    with pytest.raises(ValueError):
        snapshot_for_part(_two_condition_snapshot(), frozenset({"nobody"}), "r1", "late")
