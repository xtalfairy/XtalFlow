"""The rule that divides a dose between the positions of one well.

A table of known answers, because this is frozen in a way most code is not:
a condition test's finalized revision stores its targets without their
volumes and works them out again when the worksheet is written. Changing
what ``split_between_positions`` returns changes worksheets for experiments
planned months ago, which is why these numbers are written out by hand
rather than computed.
"""

from decimal import Decimal

import pytest

from xtalflow.domain.soaking import (
    TRANSFER_INCREMENT_NL,
    split_between_positions,
    volume_that_splits,
)


def _nl(values):
    return tuple(Decimal(value) for value in values)


@pytest.mark.parametrize(
    ("total", "positions", "expected"),
    [
        # Divides evenly: everybody gets the same.
        ("10", 1, ("10",)),
        ("10", 2, ("5", "5")),
        ("10", 4, ("2.5", "2.5", "2.5", "2.5")),
        ("32.5", 1, ("32.5",)),
        # Does not divide: the leftover steps go to the positions clicked
        # first, which is the only thing the click order decides.
        ("10", 3, ("5", "2.5", "2.5")),
        ("32.5", 2, ("17.5", "15")),
        ("32.5", 3, ("12.5", "10", "10")),
        ("32.5", 5, ("7.5", "7.5", "7.5", "5", "5")),
        ("12.5", 2, ("7.5", "5")),
        ("12.5", 3, ("5", "5", "2.5")),
        # Fewer steps than positions: a position receives nothing, and the
        # worksheet leaves it out. One step cannot be halved.
        ("2.5", 2, ("2.5", "0")),
        ("2.5", 4, ("2.5", "0", "0", "0")),
        ("10", 5, ("2.5", "2.5", "2.5", "2.5", "0")),
    ],
)
def test_a_dose_is_divided_the_same_way_it_always_was(total, positions, expected):
    assert split_between_positions(Decimal(total), positions) == _nl(expected)


@pytest.mark.parametrize(
    ("total", "positions"),
    [("10", 1), ("10", 3), ("32.5", 3), ("2.5", 4), ("7.5", 2)],
)
def test_the_whole_dose_reaches_the_drop_however_it_is_divided(total, positions):
    # The total sets the concentration; the positions only say where it lands.
    assert sum(split_between_positions(Decimal(total), positions)) == Decimal(total)


def test_a_dose_must_be_whole_steps_and_a_well_must_have_a_position():
    with pytest.raises(ValueError, match="whole number"):
        split_between_positions(Decimal("3"), 2)
    with pytest.raises(ValueError, match="at least one"):
        split_between_positions(Decimal("10"), 0)
    assert TRANSFER_INCREMENT_NL == Decimal("2.5")


def test_the_nearest_volume_every_well_can_share_equally():
    # Wells of two and three positions share evenly only in sixes: 15 nL.
    assert volume_that_splits(Decimal("30"), [2, 3]) == (Decimal("30"), True)
    assert volume_that_splits(Decimal("10"), [2]) == (Decimal("10"), True)
    assert volume_that_splits(Decimal("10"), [3]) == (Decimal("7.5"), True)
    # Nothing at or below works, so the smallest one above comes back with
    # False, because rounding down to nothing is not an answer.
    assert volume_that_splits(Decimal("10"), [2, 3]) == (Decimal("15"), False)
    assert volume_that_splits(Decimal("2.5"), [2, 3]) == (Decimal("15"), False)
