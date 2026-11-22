from datetime import datetime, timezone
from decimal import Decimal

from xtalflow.domain import SWISSCI_MIDI_3_LENS
from xtalflow.domain.fragment_screening import (
    CrystalTarget,
    Fragment,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.worksheets import build_echo_worksheet, build_shifter_worksheet


def test_a_position_the_dose_cannot_reach_is_left_out_of_the_echo_worksheet() -> None:
    """Nothing is not a row.

    5 nL is two 2.5 nL steps and the well has three positions, so the third
    receives nothing. A 0 nL line would send the dispenser to a spot it has
    no liquid for, so there is no line for it. The plan summary counts it
    instead, which is the only place a dry position is visible.
    """
    at = datetime.now(timezone.utc)
    crystal = SelectedCrystal(
        "image",
        "2069",
        "A01d",
        tuple(
            CrystalTarget(f"target-{index}", Decimal("0.25"), Decimal("-0.5"), at)
            for index in range(3)
        ),
        SWISSCI_MIDI_3_LENS.id,
    )
    plan = build_fragment_screen_plan(
        FragmentLibrary(
            "Library",
            (Fragment("V", "L", "1", "CMP-1", "C2H6O", Decimal("46.07"), "CCO",
                      Decimal("100"), "DMSO", "SRC-1", "A01"),),
        ),
        (crystal,),
        Decimal("5"),
    )

    (assignment,) = plan.assignments
    assert [transfer.volume_nl for transfer in assignment.transfers] == [
        Decimal("2.5"), Decimal("2.5"), Decimal("0")
    ]

    rows = build_echo_worksheet(plan)
    assert len(rows) == 2, "two rows for two steps"
    assert sum(row.transfer_volume_nl for row in rows) == Decimal("5")
    # The whole dose still reaches the drop, which is what sets the strength.


def test_fragment_plan_builds_echo_and_shifter_preview_rows() -> None:
    fragment = Fragment(
        "Vendor",
        "Library",
        "1",
        "CMP-1",
        "C2H6O",
        Decimal("46.07"),
        "CCO",
        Decimal("100"),
        "DMSO",
        "SRC-1",
        "A01",
    )
    crystal = SelectedCrystal(
        "image",
        "2069",
        "A01d",
        (
            CrystalTarget(
                "target", Decimal("0.25"), Decimal("-0.5"), datetime.now(timezone.utc)
            ),
        ),
        SWISSCI_MIDI_3_LENS.id,
    )
    plan = build_fragment_screen_plan(
        FragmentLibrary("Library", (fragment,)),
        (crystal,),
        Decimal("25"),
    )

    echo = build_echo_worksheet(plan)[0]
    shifter = build_shifter_worksheet(plan)[0]

    assert echo.destination_well == "B02"
    assert echo.x_offset_um == Decimal("-450.0")
    assert echo.y_offset_um == Decimal("-500.0")
    assert echo.transfer_volume_nl == Decimal("25.0")
    assert shifter.values()[:6] == (
        "SwissCI-MRC-3d",
        "2069",
        "AM",
        "A",
        "1",
        "d",
    )
