import sqlite3
from pathlib import Path

import pytest

from xtalflow.domain.crystal_score import CrystalScore
from xtalflow.infrastructure.crystal_score_store import (
    MemoryCrystalScoreStore,
    SQLiteCrystalScoreStore,
    crystal_score_database,
)
from xtalflow.infrastructure.review_migrations import LATEST_SCHEMA_VERSION

CLASSES = ("crystal", "drop", "empty")
MODEL = "crystal3-4c99f441417c"


def _score(key: str, crystal: float, model: str = MODEL) -> CrystalScore:
    rest = (1.0 - crystal) / 2
    return CrystalScore(key, CLASSES, (crystal, rest, rest), model)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        return MemoryCrystalScoreStore()
    return SQLiteCrystalScoreStore(tmp_path / "crystal_scores.sqlite3")


def test_scores_are_written_once_and_read_back_by_image_set(store) -> None:
    store.save("set-1", [_score("a", 0.9), _score("b", 0.1)])
    store.save("set-2", [_score("a", 0.2)])

    first = store.scores_for("set-1", MODEL)
    assert sorted(first) == ["a", "b"]
    assert first["a"].crystal_probability == pytest.approx(0.9, abs=1e-6)
    assert first["a"].label == "crystal"
    # The same image key in another image set is another image.
    assert store.scores_for("set-2", MODEL)["a"].crystal_probability == pytest.approx(
        0.2, abs=1e-6
    )
    assert store.scores_for("set-3", MODEL) == {}


def test_another_model_s_answers_are_not_mixed_in(store) -> None:
    """A retrained model answers differently; a walk ordered by both is ordered by neither.

    One row per image, so rescoring with a new model takes an image over
    rather than sitting alongside. Part way through a model change the old
    answers shrink and the new ones grow, and whatever neither has reached is
    simply unscored - which sends it to the front of the walk, where a person
    looks at it. There is no state in which two models' answers are mixed.
    """
    store.save("set-1", [_score("a", 0.9), _score("b", 0.8)])
    store.save("set-1", [_score("a", 0.1, model="crystal3-later")])

    # "a" belongs to the new model now; "b" is still the old model's and only
    # the old model's.
    assert set(store.scores_for("set-1", MODEL)) == {"b"}
    assert set(store.scores_for("set-1", "crystal3-later")) == {"a"}

    assert store.forget_other_models("set-1", "crystal3-later") == 1
    assert store.scores_for("set-1", MODEL) == {}
    assert set(store.scores_for("set-1", "crystal3-later")) == {"a"}


def test_scoring_the_same_image_again_replaces_it_rather_than_doubling(store) -> None:
    store.save("set-1", [_score("a", 0.4)])
    store.save("set-1", [_score("a", 0.7)])

    scores = store.scores_for("set-1", MODEL)
    assert len(scores) == 1
    assert scores["a"].crystal_probability == pytest.approx(0.7, abs=1e-6)


def test_the_file_survives_being_closed_and_opened(tmp_path: Path) -> None:
    path = tmp_path / "crystal_scores.sqlite3"
    store = SQLiteCrystalScoreStore(path)
    store.save("set-1", [_score("a", 0.9)])
    store.close()

    reopened = SQLiteCrystalScoreStore(path)
    assert reopened.scores_for("set-1", MODEL)["a"].label == "crystal"
    reopened.close()


def test_the_score_file_sits_beside_the_review_database_and_not_inside_it(
    tmp_path: Path,
) -> None:
    """This is what lets an older XtalFlow keep opening the same plates.

    The review database path comes from the application name rather than the
    version, so every installed version shares it. Adding to its schema would
    make the older one refuse a database the newer one had opened.
    """
    review = tmp_path / "somewhere" / "reviews.sqlite3"
    scores = crystal_score_database(review, "crystal_scores.sqlite3")

    assert scores == review.parent / "crystal_scores.sqlite3"
    assert scores != review

    store = SQLiteCrystalScoreStore(scores)
    store.save("set-1", [_score("a", 0.9)])
    store.close()

    # Nothing of ours is in the review database, and its schema is untouched.
    assert LATEST_SCHEMA_VERSION == 20, (
        "the crystal feature must not change the review schema: an older "
        "XtalFlow would refuse a database this one had opened"
    )
    connection = sqlite3.connect(scores)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    connection.close()
    assert tables == {"image_score", "mark_ordering"}


def test_the_store_can_be_used_from_a_worker_thread(tmp_path: Path) -> None:
    """Scoring runs off the window's thread, and SQLite refuses a shared connection.

    The window reads stored scores to show them at once while a worker writes
    the rest, so both threads touch this at the same moment.
    """
    import threading

    store = SQLiteCrystalScoreStore(tmp_path / "crystal_scores.sqlite3")
    failures: list[Exception] = []

    def work(start: int) -> None:
        try:
            for index in range(start, start + 20):
                store.save("set-1", [_score(f"image-{index}", 0.5)])
                store.scores_for("set-1", MODEL)
        except Exception as error:  # noqa: BLE001 - the point is to see any
            failures.append(error)

    threads = [threading.Thread(target=work, args=(start,)) for start in (0, 100)]
    for thread in threads:
        thread.start()
    work(200)
    for thread in threads:
        thread.join()

    assert not failures, failures
    assert len(store.scores_for("set-1", MODEL)) == 60


def test_the_order_a_mark_was_placed_in_is_remembered_once(store) -> None:
    """Otherwise the self-check marks its own homework.

    Walking crystal first puts the user on the top-ranked images, so their
    marks rank first by construction and the check passes with nothing in it.
    """
    from xtalflow.domain.crystal_score import CRYSTAL_FIRST, WELL_ORDER

    store.remember_mark_ordering("set-1", "a", WELL_ORDER)
    store.remember_mark_ordering("set-1", "b", CRYSTAL_FIRST)
    # The first one wins: it is the walk that led the user to the image.
    store.remember_mark_ordering("set-1", "a", CRYSTAL_FIRST)
    store.remember_mark_ordering("set-2", "a", CRYSTAL_FIRST)

    assert store.marked_in_crystal_order("set-1") == {"b"}
    assert store.marked_in_crystal_order("set-2") == {"a"}
    # An image with no row at all was marked before any of this existed,
    # which is well order - and those are the only honest sample there is.
    assert "c" not in store.marked_in_crystal_order("set-1")
