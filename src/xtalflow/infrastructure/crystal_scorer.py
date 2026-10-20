"""Run the crystal classifier over plate images, through OpenCV's ONNX reader.

The model is CrystAIze's: an EfficientNet-B0 fine-tuned in-house on
hand-labelled SWISS-CI plates, exported to ONNX by codex/export_crystal_onnx.py
along with a JSON of the facts needed to use it. Reading it here rather than
importing torch keeps 385 MB out of this program - the venv beside ours that
does import torch is 5.6 GB against our 468 MB.

It runs through ``cv2.dnn`` rather than ONNX Runtime, which has no wheel this
beamline's CentOS 7 can install: its glibc is 2.17 and onnxruntime's recent
wheels want 2.27 or newer. OpenCV is already a dependency here, so inference
costs nothing new, agrees with the original checkpoint to 1e-6, and takes
about six seconds for a plate - all of it in the background, and less than the
share takes to hand over the images.

Pillow is not interchangeable with OpenCV for the resize. torchvision's
Resize antialiases on a PIL image and cv2's does not, and the difference is
not academic: measured over 72 fixture images it moves the tensor by up to
0.25, the probabilities by up to 0.71, and changes the label on two to six of
them. Getting this subtly wrong does not fail - it quietly makes the model
worse, and nobody would know which plates were walked in a worse order.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from xtalflow.domain import CrystalImage
from xtalflow.domain.crystal_score import CrystalScore

BATCH_SIZE = 16


class CrystalModelUnavailable(RuntimeError):
    """The model cannot be loaded, so nothing can be scored."""


def resized_size(width: int, height: int, short_side: int) -> tuple[int, int]:
    """torchvision's Resize(int): scale the short side, keep the aspect ratio.

    Its own arithmetic, truncating rather than rounding
    (torchvision.transforms.functional._compute_resized_output_size). RockMaker
    images are all 1224x1024, where the division is exact and the difference
    never shows - which is exactly why it would go unnoticed elsewhere.
    """
    short, long = (width, height) if width <= height else (height, width)
    new_short, new_long = short_side, int(short_side * long / short)
    return (new_short, new_long) if width <= height else (new_long, new_short)


class OnnxCrystalScorer:
    """Scores images with the exported classifier, a batch at a time."""

    def __init__(self, model_path: Path | str, facts_path: Path | str | None = None) -> None:
        self.model_path = Path(model_path)
        self.facts_path = (
            Path(facts_path) if facts_path is not None
            else self.model_path.with_suffix(".json")
        )
        self._session = None
        facts = self._read_facts()
        self.model_id: str = facts["model_id"]
        self.classes: tuple[str, ...] = tuple(facts["classes"])
        self.input_size: int = int(facts["input_size"])
        self.resize_short_side: int = int(facts["resize_short_side"])
        self.mean: float = float(facts["normalize_mean"])
        self.std: float = float(facts["normalize_std"])

    def _read_facts(self) -> dict:
        if not self.model_path.is_file():
            raise CrystalModelUnavailable(f"no crystal model at {self.model_path}")
        try:
            facts = json.loads(self.facts_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise CrystalModelUnavailable(
                f"the model's facts cannot be read from {self.facts_path}: {error}"
            ) from error
        except ValueError as error:
            raise CrystalModelUnavailable(
                f"the model's facts in {self.facts_path} are not readable JSON: {error}"
            ) from error
        missing = {
            "model_id", "classes", "input_size", "resize_short_side",
            "normalize_mean", "normalize_std",
        } - set(facts)
        if missing:
            raise CrystalModelUnavailable(
                f"{self.facts_path} does not say {', '.join(sorted(missing))}"
            )
        return facts

    def _loaded(self):
        """The network, read on first use."""
        if self._session is None:
            try:
                import cv2
            except ImportError as error:  # pragma: no cover - an install problem
                raise CrystalModelUnavailable(
                    "OpenCV is not installed, so images cannot be scored"
                ) from error
            try:
                self._session = cv2.dnn.readNetFromONNX(str(self.model_path))
            except Exception as error:  # cv2 raises its own types
                raise CrystalModelUnavailable(
                    f"the crystal model at {self.model_path} could not be read: {error}"
                ) from error
        return self._session

    def prepare(self, path: Path) -> np.ndarray:
        """One image as the model wants it: CHW float32, normalised to [-1, 1].

        Pillow, not OpenCV: see the note at the top of this file. It is
        imported here rather than above so that a machine without it runs the
        program with the feature absent, which is how every other part of this
        answers a missing piece.
        """
        try:
            from PIL import Image
        except ImportError as error:  # pragma: no cover - an install problem
            raise CrystalModelUnavailable(
                "Pillow is not installed, so images cannot be prepared for the model"
            ) from error
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            image = image.resize(
                resized_size(image.width, image.height, self.resize_short_side),
                Image.BILINEAR,
            )
            left = (image.width - self.input_size) // 2
            top = (image.height - self.input_size) // 2
            image = image.crop(
                (left, top, left + self.input_size, top + self.input_size)
            )
            values = np.asarray(image, dtype=np.float32) / 255.0
        return ((values - self.mean) / self.std).transpose(2, 0, 1)

    def score(self, images: Sequence[CrystalImage]) -> tuple[CrystalScore, ...]:
        """Score what can be read; an image that cannot be is simply left out.

        Left out rather than scored as empty: a missing score puts the image
        at the front of the walk, where somebody looks at it themselves.
        """
        session = self._loaded()
        scored: list[CrystalScore] = []
        for start in range(0, len(images), BATCH_SIZE):
            batch = images[start:start + BATCH_SIZE]
            prepared, kept = [], []
            for image in batch:
                try:
                    prepared.append(self.prepare(image.path))
                except (OSError, ValueError):
                    continue
                kept.append(image)
            if not prepared:
                continue
            session.setInput(np.stack(prepared))
            logits = session.forward()
            for image, row in zip(kept, logits):
                scored.append(
                    CrystalScore(
                        image.image_key,
                        self.classes,
                        tuple(float(value) for value in _softmax(row)),
                        self.model_id,
                    )
                )
        return tuple(scored)


def _softmax(logits: np.ndarray) -> np.ndarray:
    """The model emits logits; the order needs probabilities to rank by.

    CrystAIze computes these and then keeps only the argmax, which is why its
    predictions cannot be ranked.
    """
    shifted = logits - logits.max()
    weights = np.exp(shifted)
    return weights / weights.sum()
