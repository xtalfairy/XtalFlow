"""Score CrystAIze's classifier against the labels it was held out from.

No accuracy was ever recorded for this checkpoint. The training script printed
a confusion matrix and nobody kept it, and `outputs/` is empty. This produces
that number, for the test and val splits it never trained on.

Run it with a Python that has torch - CrystAIze's own venv does:

    <CrystAIze venv>/bin/python codex/evaluate_crystal_model.py \
        /usr/local/fbdd_apps/Applications/rex/imgSort/data_split \
        <CrystAIze>/models/crystal3_best_ts.pt

Read-only. It reads images and prints.

Two things it reports that a pooled accuracy would hide:

  per class      a model that over-calls one class scores well overall while
                 being useless for the thing it over-calls
  per plate      the training set has plates that are almost entirely one
                 class, so a model can score well by recognising the plate -
                 its condition, its illumination - rather than its crystals.
                 Accuracy that swings wildly between plates is the sign.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import torchvision.transforms as T
from PIL import Image

CLASSES = ("crystal", "drop", "empty")
PLATE = re.compile(r"plate(\d+)_", re.IGNORECASE)
BATCH = 32


def transform(size: int, short_side: int):
    return T.Compose([
        T.Resize(short_side),
        T.CenterCrop(size),
        T.ToTensor(),
        T.Normalize(mean=(0.5,) * 3, std=(0.5,) * 3),
    ])


def predict(model, paths, prepare, device) -> list[tuple[int, np.ndarray]]:
    out = []
    for start in range(0, len(paths), BATCH):
        batch = paths[start:start + BATCH]
        tensors = []
        for path in batch:
            with Image.open(path) as opened:
                tensors.append(prepare(opened.convert("RGB")).numpy())
        with torch.no_grad():
            logits = model(torch.from_numpy(np.stack(tensors)).to(device))
            probabilities = torch.softmax(logits, dim=1).cpu().numpy()
        out.extend((int(row.argmax()), row) for row in probabilities)
    return out


def show_ranking(truth: list[int], probabilities: list[np.ndarray]) -> None:
    """How well P(crystal) orders the plate, which is what the feature uses.

    Accuracy at the argmax is the wrong question here. A crystal the model
    calls a drop at P(crystal) 0.45 still sorts above a drop at 0.05, and the
    review walks the order rather than the labels. What matters is how much of
    the plate somebody has to look at before they have seen the crystals.
    """
    scores = np.array([row[0] for row in probabilities])
    actual = np.array([1 if value == 0 else 0 for value in truth])
    crystals = int(actual.sum())
    if not crystals or crystals == len(actual):
        print("\n    ranking: every image is the same class here")
        return

    # Mann-Whitney: the chance a crystal outranks a non-crystal. 50% is a coin.
    order = np.argsort(scores)
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    positive = ranks[actual == 1].sum()
    negatives = len(actual) - crystals
    auc = (positive - crystals * (crystals + 1) / 2) / (crystals * negatives)
    print(f"\n    ranking by P(crystal): AUC {auc:.3f}  "
          f"(0.5 is a coin, 1.0 is perfect)")

    print(f"    {'look at the top':>18} {'images':>7} {'crystals found':>16}")
    by_score = np.argsort(-scores)
    for fraction in (0.1, 0.2, 0.3, 0.5):
        take = max(1, int(len(scores) * fraction))
        found = int(actual[by_score[:take]].sum())
        print(f"    {fraction:>17.0%} {take:>7} {found:>9} / {crystals}"
              f" = {found / crystals:>4.0%}")


def show_matrix(truth: list[int], guess: list[int]) -> None:
    counts = defaultdict(int)
    for actual, predicted in zip(truth, guess):
        counts[(actual, predicted)] += 1
    print(f"\n    {'actual / said':>16} " + "".join(f"{name:>10}" for name in CLASSES)
          + f"{'recall':>10}")
    for actual, name in enumerate(CLASSES):
        row = [counts[(actual, predicted)] for predicted in range(len(CLASSES))]
        total = sum(row)
        recall = row[actual] / total if total else 0.0
        print(f"    {name:>16} " + "".join(f"{value:>10}" for value in row)
              + f"{recall:>9.0%}")
    print(f"    {'precision':>16} ", end="")
    for predicted in range(len(CLASSES)):
        column = sum(counts[(actual, predicted)] for actual in range(len(CLASSES)))
        value = counts[(predicted, predicted)] / column if column else 0.0
        print(f"{value:>9.0%} ", end="")
    right = sum(counts[(index, index)] for index in range(len(CLASSES)))
    print(f"\n    overall {right}/{len(truth)} = {right / max(1, len(truth)):.1%}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_split", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--splits", default="test,val")
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument("--short-side", type=int, default=256)
    arguments = parser.parse_args(argv[1:])

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model = torch.jit.load(str(arguments.checkpoint), map_location=device)
    model.eval()
    prepare = transform(arguments.size, arguments.short_side)
    print(f"model   {arguments.checkpoint}")
    print(f"input   Resize({arguments.short_side}) CenterCrop({arguments.size}) on {device}")

    for split in arguments.splits.split(","):
        root = arguments.data_split / split.strip()
        if not root.is_dir():
            print(f"\n== {split}: no such folder {root}")
            continue
        paths, truth = [], []
        for index, name in enumerate(CLASSES):
            for path in sorted((root / name).glob("*.jpg")):
                paths.append(path)
                truth.append(index)
        if not paths:
            print(f"\n== {split}: no images")
            continue
        print(f"\n== {split}: {len(paths)} images")
        results = predict(model, paths, prepare, device)
        guess = [label for label, _ in results]
        show_matrix(truth, guess)
        show_ranking(truth, [row for _, row in results])

        # Per plate, because a plate that is almost all one class can be
        # recognised by its look rather than by what is in its drops.
        by_plate: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for path, actual, (predicted, _) in zip(paths, truth, results):
            found = PLATE.search(path.name)
            by_plate[found.group(1) if found else "?"].append((actual, predicted))
        print(f"\n    {'plate':>8} {'images':>7} {'accuracy':>9} {'said crystal':>13}"
              f" {'really crystal':>15}")
        for plate, rows in sorted(by_plate.items(), key=lambda item: -len(item[1])):
            right = sum(1 for actual, predicted in rows if actual == predicted)
            said = sum(1 for _, predicted in rows if predicted == 0)
            really = sum(1 for actual, _ in rows if actual == 0)
            print(f"    {plate:>8} {len(rows):>7} {right / len(rows):>9.0%}"
                  f" {said / len(rows):>12.0%} {really / len(rows):>14.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
