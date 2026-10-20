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
# means one of the three sub-drops of a well. The training set shows what it
# actually covers - drop_normal, drop_precipitated and drop_clear - so it is
# every drop without a crystal in it, and "clear" would have been a narrower
# claim than the model makes.
DISPLAY_NAMES = {"crystal": "crystal", "drop": "no crystal", "empty": "empty"}


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


WELL_ORDER = "well"
CRYSTAL_FIRST = "crystal"
# Below this many marks there is nothing to conclude from; a plate with one
# marked drop proves neither that the order works nor that it does not.
ENOUGH_MARKS = 3
# The order is doing its job if the marks sit in the first quarter of the walk.
GOOD_ENOUGH = 0.25


@dataclass(frozen=True)
class SelfCheck:
    """Whether the order put what the user marked near the front.

    This is the only measurement of this model there has ever been: the
    checkpoint shipped with no held-out accuracy of any kind.

    It is only meaningful for marks placed while walking in well order. Marks
    placed while walking crystal first are at the front by construction - the
    user was shown them first - so counting those would make the check pass
    with nothing in it.
    """

    ranks: tuple[int, ...]
    total: int

    @property
    def state(self) -> str:
        if len(self.ranks) < ENOUGH_MARKS or not self.total:
            return "unverified"
        middle = sorted(self.ranks)[len(self.ranks) // 2]
        return "pass" if middle <= self.total * GOOD_ENOUGH else "fail"

    def sentence(self) -> str:
        """What the window says. A measurement, not a verdict."""
        if self.state == "unverified":
            return "Crystal first · not yet checked against your marks"
        drops = f"your {len(self.ranks)} marked drop" + ("" if len(self.ranks) == 1 else "s")
        ordered = sorted(self.ranks)
        if len(ordered) > 8:
            middle = ordered[len(ordered) // 2]
            where = (
                f"rank {ordered[0]}–{ordered[-1]} of {self.total}, median {middle}"
            )
        else:
            listed = ", ".join(str(rank) for rank in ordered[:-1])
            where = (
                f"rank {listed} and {ordered[-1]} of {self.total}"
                if len(ordered) > 1 else f"rank {ordered[0]} of {self.total}"
            )
        if self.state == "pass":
            return f"◆ Crystal first · {drops} {where}"
        return f"△ Crystal first not used on this plate · {drops} {where}"


def self_check(
    image_keys: Sequence[str],
    scores: Mapping[str, CrystalScore],
    marked_in_well_order: Sequence[str],
) -> SelfCheck:
    """Rank the user's own marks against every scored image on the plate.

    Unscored images are left out of the count. They sort to the front by rule,
    so including them would drag every rank down on a plate whose share was
    flaky and make a working order look broken.
    """
    order = [key for key in crystal_first_order(image_keys, scores)]
    scored_keys = [
        image_keys[index] for index in order if image_keys[index] in scores
    ]
    place = {key: rank for rank, key in enumerate(scored_keys, start=1)}
    # Sorted: a rank is a place in the walk, so the order they are read in is
    # the walk's, not the order the plate happens to store its images in.
    ranks = tuple(sorted(place[key] for key in marked_in_well_order if key in place))
    return SelfCheck(ranks, len(scored_keys))
