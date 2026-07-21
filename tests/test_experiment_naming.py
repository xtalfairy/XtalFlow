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
