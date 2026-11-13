"""Wording every layer shares, so the same count reads the same everywhere."""

from __future__ import annotations

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
