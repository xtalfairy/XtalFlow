"""Wording every layer shares, so the same count reads the same everywhere."""

from __future__ import annotations


def counted(value: int, noun: str) -> str:
    """A number and its noun: 1 well, 3 wells."""
    return f"{value} {noun}{'' if value == 1 else 's'}"
