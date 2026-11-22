"""How a dose is shared between the positions of one well.

Every soaking experiment divides liquid the same way, so the rule lives here
rather than in any one of them.  It used to live in ``fragment_screening``
and be imported from there by the condition test, which made one experiment
type depend on another for something neither owns.

The rule is frozen in a stronger sense than most code.  A condition test's
finalized revision stores its targets without their volumes and works them
out again when the worksheet is written, so changing what this module does
changes worksheets for experiments that were planned months ago.  There is a
table of known answers in ``tests/test_soaking.py`` to make that hard to do
by accident.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
import math


TRANSFER_INCREMENT_NL = Decimal("2.5")
"""The smallest amount the dispenser can move, and so the unit of every dose."""


def split_between_positions(total_nl: Decimal, positions: int) -> tuple[Decimal, ...]:
    """How much each position receives, in whole 2.5 nL steps, adding up to the total.

    The dispenser moves in 2.5 nL steps, so a total that is not a multiple of
    the position count cannot be shared equally: 32.5 nL over three positions
    is 13 steps over three. The leftover steps go to the first positions
    (12.5, 10, 10) rather than to nobody, because every position sits in the
    same drop - the total reaching that drop sets the concentration, and the
    positions only say where the liquid lands. A position left with nothing
    gets nothing, and the caller leaves it out of the worksheet.

    "The first positions" means the ones clicked first: a well's positions are
    ordered by when they were chosen. So the order of the clicks decides which
    spot receives the extra step, which is the only thing that order decides.
    """
    if positions < 1:
        raise ValueError("at least one soaking position is needed")
    steps = total_nl / TRANSFER_INCREMENT_NL
    if steps != steps.to_integral_value():
        raise ValueError(
            f"{total_nl.normalize():f} nL is not a whole number of "
            f"{TRANSFER_INCREMENT_NL} nL steps"
        )
    base, extra = divmod(int(steps), positions)
    return tuple(
        TRANSFER_INCREMENT_NL * (base + (1 if index < extra else 0))
        for index in range(positions)
    )


def volume_that_splits(
    volume_nl: Decimal, position_counts: "Iterable[int]"
) -> tuple[Decimal, bool]:
    """The nearest volume every selected well can share equally.

    Returns the largest volume at or below the one asked for, and True when it
    is at or below it. When nothing at or below works - the volume is smaller
    than one 2.5 nL step for each position - the smallest one above is
    returned with False, because rounding down to nothing is not an answer.
    """
    step = 1
    for count in position_counts:
        if count:
            step = step * count // math.gcd(step, count)
    units = volume_nl / TRANSFER_INCREMENT_NL
    lower = (units // step) * step
    if lower > 0:
        return TRANSFER_INCREMENT_NL * lower, True
    return TRANSFER_INCREMENT_NL * step, False
