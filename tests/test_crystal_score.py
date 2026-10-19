import pytest

from xtalflow.domain.crystal_score import (
    CrystalScore,
    crystal_first_order,
)

CLASSES = ("crystal", "drop", "empty")


def _score(key: str, crystal: float, drop: float = 0.0, empty: float = 0.0):
    return CrystalScore(key, CLASSES, (crystal, drop, empty), "crystal3-abc")


def test_a_score_names_the_class_it_chose_and_the_word_shown_for_it() -> None:
    assert _score("a", 0.9, 0.05, 0.05).label == "crystal"
    assert _score("a", 0.9, 0.05, 0.05).display_label == "crystal"
    # "drop" already means one of a well's three sub-drops in this program,
    # and the navigation says so on screen: Well 12 · Drop 2.
    assert _score("a", 0.1, 0.8, 0.1).label == "drop"
    assert _score("a", 0.1, 0.8, 0.1).display_label == "clear"
    assert _score("a", 0.0, 0.1, 0.9).display_label == "empty"


def test_a_score_has_to_say_which_model_made_it() -> None:
    """A model retrained later gives the same image a different answer."""
    with pytest.raises(ValueError, match="which model"):
        CrystalScore("a", CLASSES, (1.0, 0.0, 0.0), "")
    with pytest.raises(ValueError, match="between 0 and 1"):
        CrystalScore("a", CLASSES, (1.4, 0.0, 0.0), "m")
    with pytest.raises(ValueError, match="every class"):
        CrystalScore("a", CLASSES, (1.0, 0.0), "m")


def test_the_order_puts_the_likeliest_crystal_first() -> None:
    keys = ("a", "b", "c")
    scores = {"a": _score("a", 0.1), "b": _score("b", 0.9), "c": _score("c", 0.5)}

    assert crystal_first_order(keys, scores) == (1, 2, 0)


def test_an_image_nobody_could_score_is_looked_at_first_not_last() -> None:
    """Unscored means the model could not look; the answer is a person looking."""
    keys = ("a", "b", "c")
    scores = {"a": _score("a", 0.9), "c": _score("c", 0.2)}

    assert crystal_first_order(keys, scores) == (1, 0, 2)


def test_images_that_score_alike_stay_in_well_order() -> None:
    """A tie should read as the plate's own order, not as noise."""
    keys = ("a", "b", "c", "d")
    scores = {key: _score(key, 0.5) for key in keys}

    assert crystal_first_order(keys, scores) == (0, 1, 2, 3)


def test_the_order_is_always_a_permutation() -> None:
    """Nothing can be hidden: every image keeps a place, whatever is known."""
    keys = tuple(f"image-{index}" for index in range(50))
    partial = {
        key: _score(key, (index * 37 % 100) / 100)
        for index, key in enumerate(keys)
        if index % 3
    }

    for scores in ({}, partial, {key: _score(key, 0.5) for key in keys}):
        order = crystal_first_order(keys, scores)
        assert sorted(order) == list(range(len(keys)))
