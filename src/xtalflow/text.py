"""Wording every layer shares, so the same count reads the same everywhere."""

from __future__ import annotations

import math

VOWELS = "aeiou"


def counted(value: int, noun: str) -> str:
    """A number and its noun: 1 well, 3 wells, 2 boundaries.

    English plurals, because the steps said "2 boundarys" to a researcher
    otherwise: a consonant before a final y becomes -ies, and a sibilant
    ending takes -es.
    """
    if value == 1:
        return f"{value} {noun}"
    if noun.endswith("y") and noun[-2:-1].lower() not in VOWELS:
        return f"{value} {noun[:-1]}ies"
    if noun.endswith(("s", "x", "z", "ch", "sh")):
        return f"{value} {noun}es"
    return f"{value} {noun}s"


def percent(value: float) -> str:
    """A fraction as a percentage, rounded down.

    Rounding to nearest showed "90%" for 0.895 beside a rule that wanted
    0.90, so a boundary that read as good enough was turned down as not: 38
    of the fixture's images sit in that gap. Rounding down can understate a
    score by a point; claiming one it does not have cannot be explained to
    the person reading it.
    """
    return f"{math.floor(value * 100)}%"


def bulk_confirm_question(count: int) -> str:
    """What "Confirm detected boundaries" is about to do, in its own words.

    The dialog promised a restriction the code had already dropped: "Only
    wells whose selected targets are inside the detected boundary will be
    confirmed."  A position outside the outline stopped being a bar once
    the detected outline turned out to be smaller than the well often
    enough that the button read "Confirm detected boundaries (0)" beside a
    list of wells waiting for it.  The sentence stayed behind, so the
    person was told fewer wells would be confirmed than were - and said yes
    to that.
    """
    return (
        f"Confirm {counted(count, 'automatically detected boundary')}?\n\n"
        "A position outside the detected outline does not hold a well back: "
        "the outline is often smaller than the well, so that is a warning, "
        "not a bar. Wells with no boundary detected, and boundaries set by "
        "hand, are left as they are."
    )
