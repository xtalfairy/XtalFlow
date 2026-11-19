from datetime import datetime

import pytest

from xtalflow.domain.experiment_naming import (
    normalize_protein_name,
    protein_name_for_record,
    suggest_experiment_id,
)


def test_legacy_compatible_fragment_experiment_id_uses_next_monthly_sequence() -> None:
    result = suggest_experiment_id(
        "FragSC",
        "brd4-domain-1",
        {"FragSC-202607-BRD4-DOMAIN-1-01"},
        datetime(2026, 7, 21),
    )

    assert result == "FragSC-202607-BRD4-DOMAIN-1-02"
    assert normalize_protein_name(" brd4-domain-1 ") == "BRD4-DOMAIN-1"
    # The IDs in MxLive of this shape were typed with spaces, which the ID
    # turned into hyphens while the record kept the space. A new name is
    # asked to use the hyphen, so the two cannot disagree again.
    with pytest.raises(ValueError, match="use - instead"):
        suggest_experiment_id("FragSC", "brd4 domain 1", set())


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
    assert normalize_protein_name("BRD-4") == "BRD-4"
    assert normalize_protein_name("BRD_4-x") == "BRD_4-X"

    for refused in ("BRD4.1", "BRD4%", "BRD/4", "BRD(4)"):
        with pytest.raises(ValueError, match="letters, numbers"):
            normalize_protein_name(refused)


def test_an_experiment_that_already_has_an_id_is_not_renamed_out_of_the_way() -> None:
    """Tightening the rule must not stop work that was started under the old one.

    An experiment that has its ID is no longer choosing a name: the ID is
    fixed and every later revision reuses it. Refusing the dot here would not
    take it out of the ID already written - it would only stop a draft being
    finalized, or a finalized experiment being revised, mid-beamtime.
    """
    assert normalize_protein_name("HSP90.N", already_named=True) == "HSP90.N"
    # Everything else is still refused: nothing new is excused, only the dot.
    for refused in ("HSP90%", "HSP/90", "HSP(90)", ".HSP90"):
        with pytest.raises(ValueError, match="letters, numbers"):
            normalize_protein_name(refused, already_named=True)


def test_a_space_never_reaches_the_record_however_it_got_stored() -> None:
    """The record has to match the ID, because a space breaks processing.

    The experiment ID has always turned a space into a hyphen while the
    record kept the space, so one experiment went to MxLive under two
    spellings - and the one with the space is the one that breaks data
    processing at the beamline, where the name becomes part of a path. New
    names are asked to use the hyphen, but experiments named before that
    have a space stored in their snapshot, and a record uploaded from one of
    those must still not carry it.
    """
    assert protein_name_for_record("brd4 domain 1") == "BRD4-DOMAIN-1"
    assert suggest_experiment_id(
        "FS", "brd4-domain-1", set(), datetime(2026, 7, 21)
    ) == "FS-202607-BRD4-DOMAIN-1-01"
    # The protein part of the ID and the recorded name are the same string.
    assert protein_name_for_record("brd4 domain 1") in suggest_experiment_id(
        "FS", "brd4-domain-1", set(), datetime(2026, 7, 21)
    )

    # A name too broken to normalize is left as it stands rather than raising:
    # this runs while the preview table is drawn, and the Setup step is what
    # stops such an experiment being finalized, so nothing is uploaded.
    assert protein_name_for_record("BRD4/1") == "BRD4/1"
    assert protein_name_for_record("   ") == ""

