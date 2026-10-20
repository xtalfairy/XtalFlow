"""Check the committed ONNX against the TorchScript checkpoint it came from.

The ONNX is in the repository because the beamline cannot build one: its
glibc is 2.17 and neither onnxruntime nor the onnx package ships a wheel that
old. So the file arrives already made, and arriving already made is exactly
the thing that has to be checked rather than trusted.

Run it wherever torch and the original .pt are - CrystAIze's venv has both:

    <CrystAIze venv>/bin/python codex/verify_crystal_onnx.py \
        models/crystal3.onnx \
        /usr/local/fbdd_apps/Applications/rex/CrystAIze/models/crystal3_best_ts.pt \
        /smbmount/rmserver/RockMakerStorage/WellImages/70/plateID_2070

Read-only. It compares the checksum recorded at export time against the
checkpoint in front of it, then runs both over real images and compares what
they say, image by image.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# Deliberately self-contained: this runs in whichever venv has torch, which
# is not the one XtalFlow is installed in and does not have its dependencies.
# The arithmetic below is the same as infrastructure/crystal_scorer.py, and
# the point of the script is to prove they agree anyway.


def resized_size(width: int, height: int, short_side: int) -> tuple[int, int]:
    """torchvision's Resize(int): scale the short side, truncating."""
    short, long = (width, height) if width <= height else (height, width)
    new_short, new_long = short_side, int(short_side * long / short)
    return (new_short, new_long) if width <= height else (new_long, new_short)


def prepare(path: Path, facts: dict) -> np.ndarray:
    from PIL import Image

    size = facts["input_size"]
    with Image.open(path) as opened:
        image = opened.convert("RGB")
        image = image.resize(
            resized_size(image.width, image.height, facts["resize_short_side"]),
            Image.BILINEAR,
        )
        left, top = (image.width - size) // 2, (image.height - size) // 2
        image = image.crop((left, top, left + size, top + size))
        values = np.asarray(image, dtype=np.float32) / 255.0
    return ((values - facts["normalize_mean"]) / facts["normalize_std"]).transpose(2, 0, 1)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    onnx_path, checkpoint, images_root = (Path(value) for value in argv[1:4])
    facts = json.loads(onnx_path.with_suffix(".json").read_text())

    print(f"onnx       {onnx_path}")
    print(f"model_id   {facts['model_id']}")
    print(f"classes    {', '.join(facts['classes'])}")

    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    same = digest == facts.get("source_sha256")
    print(f"\ncheckpoint {checkpoint}")
    print(f"  recorded at export  {facts.get('source_sha256')}")
    print(f"  in front of us      {digest}")
    print("  -> the same checkpoint" if same else "  -> A DIFFERENT CHECKPOINT")
    if not same:
        print("\nStop here: this ONNX was not made from this .pt.")
        return 1

    paths = sorted(images_root.rglob("*_ef.jpg"))[:48]
    if not paths:
        print(f"\nno *_ef.jpg under {images_root}")
        return 2
    print(f"\ncomparing both over {len(paths)} images from {images_root}")

    import torch
    import torchvision.transforms as T
    from PIL import Image

    theirs_transform = T.Compose([
        T.Resize(facts["resize_short_side"]),
        T.CenterCrop(facts["input_size"]),
        T.ToTensor(),
        T.Normalize(mean=(facts["normalize_mean"],) * 3,
                    std=(facts["normalize_std"],) * 3),
    ])
    model = torch.jit.load(str(checkpoint), map_location="cpu")
    model.eval()
    network = cv2.dnn.readNetFromONNX(str(onnx_path))

    worst, disagreed = 0.0, 0
    for start in range(0, len(paths), 16):
        batch = paths[start:start + 16]
        theirs = np.stack([
            theirs_transform(Image.open(path).convert("RGB")).numpy()
            for path in batch
        ])
        ours = np.stack([prepare(path, facts) for path in batch])
        tensor_gap = float(np.abs(ours - theirs).max())
        with torch.no_grad():
            want = torch.softmax(model(torch.from_numpy(theirs)), dim=1).numpy()
        network.setInput(ours)
        got = network.forward()
        got = np.exp(got - got.max(axis=1, keepdims=True))
        got = got / got.sum(axis=1, keepdims=True)
        worst = max(worst, float(np.abs(got - want).max()), tensor_gap)
        disagreed += int((got.argmax(axis=1) != want.argmax(axis=1)).sum())

    print(f"  largest difference  {worst:.3e}")
    print(f"  labels disagree on  {disagreed} of {len(paths)}")
    good = worst < 1e-4 and disagreed == 0
    print("\n  -> the ONNX says what the checkpoint says" if good
          else "\n  -> THEY DISAGREE; do not use this ONNX")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
