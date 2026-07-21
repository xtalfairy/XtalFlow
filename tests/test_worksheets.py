from datetime import UTC, datetime
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
                "target", Decimal("0.25"), Decimal("-0.5"), datetime.now(UTC)
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
