from dataclasses import dataclass
from pathlib import Path

from xtalflow.application.crystal_scoring import CrystalScoringService, ScoringProgress
from xtalflow.domain.crystal_score import CrystalScore
from xtalflow.infrastructure.crystal_score_store import MemoryCrystalScoreStore

CLASSES = ("crystal", "drop", "empty")
MODEL = "crystal3-test"


@dataclass
class _Image:
    image_key: str
    path: Path = Path("unused")


class _Scorer:
    """Scores everything, except the keys it is told it cannot read."""

    model_id = MODEL

    def __init__(self, unreadable: set[str] | None = None) -> None:
        self.unreadable = unreadable or set()
        self.seen: list[str] = []
        self.batches = 0

    def score(self, images):
        self.batches += 1
        produced = []
        for image in images:
            self.seen.append(image.image_key)
            if image.image_key in self.unreadable:
                continue
            value = (len(image.image_key) % 10) / 10
            produced.append(
                CrystalScore(
                    image.image_key, CLASSES, (value, 1 - value, 0.0), MODEL
                )
            )
        return tuple(produced)


def _images(count: int) -> list[_Image]:
    return [_Image(f"image-{index:03d}") for index in range(count)]


def test_an_image_set_is_scored_once_and_not_again() -> None:
    images = _images(20)
    scorer = _Scorer()
    service = CrystalScoringService(scorer, MemoryCrystalScoreStore())

    first = service.score_missing("set-1", images)
    assert len(first) == 20
    assert len(scorer.seen) == 20

    again = service.score_missing("set-1", images)
    assert len(again) == 20
    assert len(scorer.seen) == 20, "already scored images must not be read again"


def test_only_the_new_images_of_a_second_batch_are_scored() -> None:
    """A plate imaged again adds images; the ones already scored are kept."""
    scorer = _Scorer()
    service = CrystalScoringService(scorer, MemoryCrystalScoreStore())
    service.score_missing("set-1", _images(10))

    service.score_missing("set-1", _images(16))

    assert len(scorer.seen) == 16
    assert scorer.seen[10:] == [f"image-{index:03d}" for index in range(10, 16)]


def test_an_unreadable_image_is_left_out_and_counted() -> None:
    """Missing means a person looks at it; it is never guessed at."""
    images = _images(8)
    scorer = _Scorer(unreadable={"image-003", "image-005"})
    service = CrystalScoringService(scorer, MemoryCrystalScoreStore())
    seen: list[ScoringProgress] = []

    scores = service.score_missing("set-1", images, progress=seen.append)

    assert set(scores) == {
        image.image_key for image in images
    } - {"image-003", "image-005"}
    assert seen[-1].failed == 2
    assert seen[-1].finished


def test_scoring_stops_when_asked_and_keeps_what_it_had() -> None:
    """Closing a window or opening another plate must not wait for 288 images."""
    images = _images(80)
    scorer = _Scorer()
    service = CrystalScoringService(scorer, MemoryCrystalScoreStore())

    scores = service.score_missing(
        "set-1", images, should_stop=lambda: scorer.batches >= 2
    )

    assert 0 < len(scores) < 80
    assert scorer.batches == 2
    # What it managed is kept, and a later pass carries on from there.
    carried = service.score_missing("set-1", images)
    assert len(carried) == 80


def test_progress_counts_towards_the_whole_image_set_not_the_remainder() -> None:
    """The line on screen says 120 of 288, so it must mean the plate."""
    images = _images(40)
    scorer = _Scorer()
    service = CrystalScoringService(scorer, MemoryCrystalScoreStore())
    service.score_missing("set-1", images[:24])
    seen: list[ScoringProgress] = []

    service.score_missing("set-1", images, progress=seen.append)

    assert [step.total for step in seen] == [40] * len(seen)
    assert seen[-1].done == 40
    assert str(seen[-1]) == "scoring 40 of 40"
