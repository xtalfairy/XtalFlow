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


def test_the_check_passes_when_the_marks_sit_near_the_front() -> None:
    from xtalflow.domain.crystal_score import self_check

    keys = tuple(f"i{index}" for index in range(100))
    # Probability falls with the index, so key i0 ranks 1 and i99 ranks 100.
    scores = {key: _score(key, 1.0 - index / 100) for index, key in enumerate(keys)}

    check = self_check(keys, scores, ["i1", "i2", "i4"])

    assert check.state == "pass"
    assert check.ranks == (2, 3, 5)
    assert check.sentence() == (
        "◆ Crystal first · your 3 marked drops rank 2, 3 and 5 of 100"
    )


def test_the_check_fails_and_says_so_rather_than_going_quiet() -> None:
    from xtalflow.domain.crystal_score import self_check

    keys = tuple(f"i{index}" for index in range(100))
    scores = {key: _score(key, 1.0 - index / 100) for index, key in enumerate(keys)}

    check = self_check(keys, scores, ["i40", "i60", "i80"])

    assert check.state == "fail"
    assert check.sentence().startswith("△ Crystal first not used on this plate")
    assert "41, 61 and 81 of 100" in check.sentence()


def test_too_few_marks_is_unverified_rather_than_a_verdict() -> None:
    """One marked drop proves neither that the order works nor that it does not."""
    from xtalflow.domain.crystal_score import self_check

    keys = tuple(f"i{index}" for index in range(100))
    scores = {key: _score(key, 1.0 - index / 100) for index, key in enumerate(keys)}

    for marks in ([], ["i1"], ["i1", "i2"]):
        check = self_check(keys, scores, marks)
        assert check.state == "unverified"
        assert check.sentence() == "Crystal first · not yet checked against your marks"


def test_images_nobody_could_score_are_left_out_of_the_count() -> None:
    """They sort to the front by rule, so counting them would drag every rank down."""
    from xtalflow.domain.crystal_score import self_check

    keys = tuple(f"i{index}" for index in range(100))
    scores = {
        key: _score(key, 1.0 - index / 100)
        for index, key in enumerate(keys)
        if index % 2  # half the plate unreadable, as a flaky share would leave it
    }

    check = self_check(keys, scores, ["i1", "i3", "i5"])

    assert check.total == 50, "the denominator is what was scored"
    assert check.ranks == (1, 2, 3)
    assert check.state == "pass"


def test_many_marks_are_given_as_a_range_and_a_median() -> None:
    from xtalflow.domain.crystal_score import self_check

    keys = tuple(f"i{index}" for index in range(288))
    scores = {key: _score(key, 1.0 - index / 288) for index, key in enumerate(keys)}

    check = self_check(keys, scores, [f"i{index}" for index in range(2, 61, 3)])

    assert len(check.ranks) == 20
    assert "rank 3–60 of 288, median" in check.sentence()
