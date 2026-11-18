from datetime import datetime

import pytest

from xtalflow.domain.experiment_naming import (
    normalize_protein_name,
    suggest_experiment_id,
)


def test_legacy_compatible_fragment_experiment_id_uses_next_monthly_sequence() -> None:
    result = suggest_experiment_id(
        "FragSC",
        "brd4 domain 1",
        {"FragSC-202607-BRD4-DOMAIN-1-01"},
        datetime(2026, 7, 21),
    )

    assert result == "FragSC-202607-BRD4-DOMAIN-1-02"
    assert normalize_protein_name(" brd4 domain 1 ") == "BRD4-DOMAIN-1"


@pytest.mark.parametrize("protein", ["", "BRD4/1", "BRD4:1"])
def test_unsafe_protein_names_are_rejected(protein: str) -> None:
    with pytest.raises(ValueError):
        normalize_protein_name(protein)


def test_a_protein_name_keeps_to_letters_digits_hyphen_and_underscore() -> None:
    """The name becomes the experiment ID, which becomes a file name.

    A dot in an identifier is read as an extension by the next tool along,
    and the ID is also what MxLive files the records under.
    """
    assert normalize_protein_name("brd4") == "BRD4"
    assert normalize_protein_name("BRD 4") == "BRD-4"
    assert normalize_protein_name("BRD_4-x") == "BRD_4-X"

    for refused in ("BRD4.1", "BRD4%", "BRD/4", "BRD(4)"):
        with pytest.raises(ValueError, match="letters, numbers"):
            normalize_protein_name(refused)
