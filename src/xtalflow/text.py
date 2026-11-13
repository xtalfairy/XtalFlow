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
