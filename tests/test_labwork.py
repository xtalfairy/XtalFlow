from datetime import datetime, timezone
from decimal import Decimal

from xtalflow.domain.crystal_workflow import AssignmentOrder, CrystalTarget, SelectedCrystal
from xtalflow.domain.fragment_screening import (
    Fragment, FragmentLibrary, build_fragment_screen_plan,
)
from xtalflow.domain.labwork import build_fragment_labworks, build_raw_crystal_labworks
from xtalflow.domain.plate_format import SWISSCI_MIDI_3_LENS
from xtalflow.domain.raw_crystal import build_raw_crystal_plan


def _crystal(target_count: int = 1) -> SelectedCrystal:
    selected_at = datetime.now(timezone.utc)
    return SelectedCrystal(
        "/rmserver/image.jpg", "2069", "A04a",
        tuple(
            CrystalTarget(
                f"target-{index + 1}", Decimal("0.125") + index,
                Decimal("-0.5") - index, selected_at,
            )
            for index in range(target_count)
        ),
        SWISSCI_MIDI_3_LENS.id,
        "/rmserver/image.jpg",
    )


def test_raw_labwork_uses_account_id_and_empty_soaking_fields() -> None:
    records = build_raw_crystal_labworks(
        build_raw_crystal_plan((_crystal(2),)), experiment_id="RawCrystal-1",
        protein_name="BRD4", username="remote-user", account_id="owner-42",
    )
    payload = records[0].to_payload()

    assert payload["name"] == "remote-user"
    assert payload["project_id"] == "owner-42"
    assert len(records) == 1
    assert payload["plate_x"] == 0.0
    assert payload["plate_y"] == 0.0
    assert payload["plate_imgpath"] == "/rmserver/image.jpg"
    assert payload["protein_name"] == "BRD4"
    assert payload["plate_type"] == "SwissCI-MRC-3d"
    assert payload["plate_code"] == "2069"
    assert payload["plate_well"] == "A04a"
    assert payload["crystal_no"] == 1
    assert payload["soak_id"] == ""
    assert payload["soak_smile"] == ""
    assert payload["soak_vol"] == 0.0


def test_fragment_labwork_has_one_record_per_image_drop() -> None:
    fragment = Fragment(
        "Vendor", "Library", "1", "CMP-1", "C2H6O", Decimal("46.07"),
        "CCO", Decimal("100"), "DMSO", "SOURCE", "A01",
    )
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)), (_crystal(2),), Decimal("25"),
        AssignmentOrder.SELECTION,
    )
    records = build_fragment_labworks(
        plan, experiment_id="FragSC-1", protein_name="BRD4",
        username="fbdd", account_id="fbdd",
    )
    payload = records[0].to_payload()

    assert len(records) == 1
    assert payload["crystal_no"] == 1
    assert payload["plate_x"] == 0.0
    assert payload["plate_y"] == 0.0
    assert payload["soak_id"] == "CMP-1"
    assert payload["soak_smile"] == "CCO"
    assert payload["soak_vol"] == 25.0
    assert payload["plate_type"] == "SwissCI-MRC-3d"
    assert payload["project_id"] == "fbdd"


def test_a_dose_mxlive_cannot_hold_is_rounded_by_us_not_by_the_server() -> None:
    """``soak_vol`` is an IntegerField there, and ECHO moves in 2.5 nL steps.

    MxLive validates nothing - its write path calls ``update()``, which
    bypasses the model - so a half nanolitre is the database's problem. Django
    prepares an integer field with ``int(value)``, which truncates towards
    zero: 32.5 would be stored as 32 and 2.5 as 2, always short and never
    over, and -20% for one ECHO step.

    Sending a whole number chosen here means the stored value is the same on
    every deployment instead of depending on whether that column is integer or
    float on the day.
    """
    from decimal import Decimal

    from xtalflow.domain.labwork import mxlive_soak_volume

    # Half up, not Python's half-to-even, which would send 2.5 down to 2 and
    # leave the worst case exactly as it was.
    assert mxlive_soak_volume(Decimal("2.5")) == 3
    assert mxlive_soak_volume(Decimal("32.5")) == 33
    assert mxlive_soak_volume(Decimal("67.5")) == 68
    # A whole number is left alone.
    assert mxlive_soak_volume(Decimal("85.0")) == 85


def test_the_payload_carries_a_whole_number_of_nanolitres() -> None:
    from xtalflow.domain.labwork import LabworkRecord

    def record(nanolitres: str) -> LabworkRecord:
        return LabworkRecord(
            "jjh", "PreTest-202611-BRD4-01", "BRD4", "SwissCI-MRC-3d", "2069",
            "/rmserver/image.jpg", "A04a", Decimal("0"), Decimal("0"), 1,
            "pretest", "Z00", Decimal(nanolitres), "Solvent_10%-0-r1", "CS(=O)C",
            "7", "Uploaded by XtalFlow · Condition Test",
        )

    payload = record("32.5").to_payload()
    assert payload["soak_vol"] == 33
    assert isinstance(payload["soak_vol"], int)
    # And the record knows, so somebody can be told before it is sent.
    assert record("32.5").soak_volume_is_rounded
    assert not record("50").soak_volume_is_rounded


def test_a_name_that_cannot_be_an_identifier_is_said_before_the_upload() -> None:
    """A plan composed now is refused for these; a finalized one is not.

    The liquid has already gone into the wells by then, and a record nobody
    can make is worse than an awkward name. So it is said instead, at the
    last place it can be said at all - and said separately from the names
    line, which shows three, so a bad one among ninety-six is never in it.
    """
    from xtalflow.domain.labwork import LabworkRecord
    from xtalflow.ui.experiment_coordinator import _unwritable_name_note

    def record(soak_id, plate="SRC-1", well="A1"):
        return LabworkRecord(
            "lee", "FragSC-1", "BRD4", "SWISSCI", "1070", "p.jpg", "A01a",
            Decimal(0), Decimal(0), 1, plate, well, Decimal(100), soak_id,
            "CC(=O)C=1C=CC.O", "42", "",
        )

    assert _unwritable_name_note((record("Z1001"), record("Z1002"))) == ""

    note = _unwritable_name_note(
        (record("Z1001"), record("50%/DMSO"), record("Z3", plate="SRC 1"))
    )

    assert "soak_id" in note and "50%/DMSO" in note
    assert "and 1 more" in note
    assert "cannot be corrected" in note
    # The SMILES holds a dot and brackets in every one of these, and is
    # never what this is about: that is how a structure is written.
    assert "CC(=O)" not in note

