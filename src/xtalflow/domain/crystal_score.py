"""What the crystal classifier said about one image, and the order it implies.

The score only ever decides the order images are walked in. It never removes
one: a drop passed over because the model was wrong can cost the only
diffracting crystal on a plate that took weeks to grow, while a drop looked at
for nothing costs three seconds.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CRYSTAL = "crystal"
# The model's second class is called "drop", which in this program already
# means one of the three sub-drops of a well. On screen it is "clear".
DISPLAY_NAMES = {"crystal": "crystal", "drop": "clear", "empty": "empty"}


@dataclass(frozen=True)
class CrystalScore:
    """One image's class probabilities, and which model produced them."""

    image_key: str
    classes: tuple[str, ...]
    probabilities: tuple[float, ...]
    model_id: str

    def __post_init__(self) -> None:
        if not self.image_key:
            raise ValueError("a score needs the image it belongs to")
        if not self.classes or len(self.classes) != len(self.probabilities):
            raise ValueError("every class needs a probability and the other way round")
        if any(not 0.0 <= value <= 1.0 for value in self.probabilities):
            raise ValueError("probabilities must lie between 0 and 1")
        if not self.model_id:
            raise ValueError("a score must say which model made it")

    @property
    def label(self) -> str:
        """The class the model chose."""
        best = max(range(len(self.classes)), key=lambda index: self.probabilities[index])
        return self.classes[best]

    @property
    def display_label(self) -> str:
        return DISPLAY_NAMES.get(self.label, self.label)

    @property
    def crystal_probability(self) -> float:
        """How strongly this image is a crystal, which is what the order uses."""
        if CRYSTAL not in self.classes:
            return 0.0
        return self.probabilities[self.classes.index(CRYSTAL)]


def crystal_first_order(
    image_keys: Sequence[str], scores: Mapping[str, CrystalScore]
) -> tuple[int, ...]:
    """Positions of ``image_keys``, most likely crystal first.

    An image with no score comes first, not last. Being unscored means the
    model could not look at it — an unreadable file, a share that dropped part
    way through — and the answer to that is a person looking, not a place at
    the back of a queue of 288.

    Images that score alike keep the order they came in, so a tie reads as
    well order rather than as noise.
    """
    def rank(position: int) -> tuple[int, float, int]:
        score = scores.get(image_keys[position])
        if score is None:
            return (0, 0.0, position)
        return (1, -score.crystal_probability, position)

    return tuple(sorted(range(len(image_keys)), key=rank))
