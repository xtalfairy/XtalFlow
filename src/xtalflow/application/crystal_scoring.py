"""Score an image set's images once, and keep what was already scored.

Scoring a plate costs about thirty seconds, almost all of it reading 288
images of 1.2 megapixels off a network share. Images never change, so this is
paid once per image set: opening the same plate again is immediate.

``score_missing`` runs off the window's thread. It touches the score file and
the image share and nothing else, and it never asks the user anything: a share
that drops part way through leaves images unscored, which puts them at the
front of the walk rather than stopping it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from xtalflow.domain import CrystalImage
from xtalflow.domain.crystal_score import CrystalScore


class CrystalScorerPort(Protocol):
    model_id: str

    def score(self, images: Sequence[CrystalImage]) -> tuple[CrystalScore, ...]: ...


class CrystalScoreStorePort(Protocol):
    def scores_for(self, image_set_id: str, model_id: str) -> dict[str, CrystalScore]: ...

    def save(self, image_set_id: str, scores: "Sequence[CrystalScore]") -> int: ...


@dataclass(frozen=True)
class ScoringProgress:
    """How far scoring has got, for the one line that says so on screen."""

    done: int
    total: int
    failed: int = 0

    @property
    def finished(self) -> bool:
        return self.done >= self.total

    def __str__(self) -> str:
        return f"scoring {self.done} of {self.total}"


BATCH = 16


class CrystalScoringService:
    """Fills in the scores an image set is missing, a batch at a time."""

    def __init__(self, scorer: CrystalScorerPort, store: CrystalScoreStorePort) -> None:
        self.scorer = scorer
        self.store = store

    def stored(self, image_set_id: str) -> dict[str, CrystalScore]:
        return self.store.scores_for(image_set_id, self.scorer.model_id)

    def score_missing(
        self,
        image_set_id: str,
        images: Sequence[CrystalImage],
        progress: Callable[[ScoringProgress], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, CrystalScore]:
        """Score what has no score yet; return every score for the image set.

        ``should_stop`` is asked between batches so closing a window or moving
        to another plate does not wait for 288 images. What was scored before
        stopping is kept: it is correct, just incomplete.
        """
        scores = self.stored(image_set_id)
        missing = [image for image in images if image.image_key not in scores]
        if not missing:
            if progress is not None:
                progress(ScoringProgress(len(images), len(images)))
            return scores

        failed = 0
        for start in range(0, len(missing), BATCH):
            if should_stop is not None and should_stop():
                break
            batch = missing[start:start + BATCH]
            produced = self.scorer.score(batch)
            # An image the scorer could not read comes back missing, not
            # guessed at. Counted so the window can say so.
            failed += len(batch) - len(produced)
            if produced:
                self.store.save(image_set_id, produced)
                scores.update({score.image_key: score for score in produced})
            if progress is not None:
                progress(ScoringProgress(len(scores) + failed, len(images), failed))
        return scores
