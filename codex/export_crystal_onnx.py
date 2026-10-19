"""Turn CrystAIze's TorchScript crystal classifier into ONNX, with its facts.

Run once, from a Python that has torch — CrystAIze's own venv does:

    /Users/jjh/Dropbox/12_codebase/CrystAIze/.venv/bin/python \
        codex/export_crystal_onnx.py \
        /Users/jjh/Dropbox/12_codebase/CrystAIze/models/crystal3_best_ts.pt \
        models/

XtalFlow reads the ONNX and never imports torch: the runtime is some 50 MB
rather than 385, and the pinned dependency list stays short.

The ``.pt`` carries 41 bytes of metadata — the class names, in a labels.json
inside the archive — and nothing else: no input size, no preprocessing, no
training date, no accuracy. CrystAIze's own README lists storing that beside
the model as a thing it wants. So the JSON written here records everything
needed to use the model correctly and to say later which model said what,
with the source checksum so the two can always be tied back together.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import torch

# These are not ours to choose: they are what the model was trained with, and
# reading them off the training script is the only way to be sure.
# imgSort/src_0/alone/train_xtal3class_3.py:108 (val transform) is identical to
# CrystAIze/src/models/torchscript_loader.py:118-124 (inference transform).
INPUT_SIZE = 224
RESIZE_SHORT_SIDE = 256
NORMALIZE_MEAN = 0.5
NORMALIZE_STD = 0.5
FALLBACK_CLASSES = ("crystal", "drop", "empty")


def classes_in(checkpoint: Path) -> tuple[str, ...]:
    """The class names the exporter put inside the .pt, in index order."""
    try:
        with zipfile.ZipFile(checkpoint) as archive:
            name = next(
                item for item in archive.namelist() if item.endswith("labels.json")
            )
            with archive.open(name) as handle:
                return tuple(json.load(handle)["classes"])
    except (KeyError, OSError, StopIteration, ValueError):
        return FALLBACK_CLASSES


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    checkpoint = Path(argv[1]).expanduser().resolve()
    out_directory = Path(argv[2]).expanduser().resolve()
    if not checkpoint.is_file():
        print(f"no model at {checkpoint}")
        return 2
    out_directory.mkdir(parents=True, exist_ok=True)
    onnx_path = out_directory / "crystal3.onnx"
    facts_path = out_directory / "crystal3.json"

    classes = classes_in(checkpoint)
    model = torch.jit.load(str(checkpoint), map_location="cpu")
    model.eval()

    example = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE)
    with torch.no_grad():
        logits = model(example)
    if logits.shape[1] != len(classes):
        print(f"model emits {logits.shape[1]} values but {len(classes)} classes are named")
        return 1

    torch.onnx.export(
        model,
        example,
        str(onnx_path),
        input_names=["image"],
        output_names=["logits"],
        # Scoring a plate means batches; the batch dimension has to vary.
        dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
        # The TorchScript exporter, not the dynamo one: this is already a
        # traced graph, and the newer path pulls in onnxscript, which is a
        # dependency to install in somebody else's project for no gain here.
        dynamo=False,
    )

    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    facts = {
        "model_id": f"crystal3-{digest[:12]}",
        "classes": list(classes),
        "input_size": INPUT_SIZE,
        "resize_short_side": RESIZE_SHORT_SIDE,
        "normalize_mean": NORMALIZE_MEAN,
        "normalize_std": NORMALIZE_STD,
        "output": "logits",
        "source_checkpoint": checkpoint.name,
        "source_sha256": digest,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exported_by": "codex/export_crystal_onnx.py",
        "note": (
            "EfficientNet-B0 fine-tuned in-house on hand-labelled SWISS-CI plates. "
            "No held-out accuracy was recorded for this checkpoint."
        ),
    }
    facts_path.write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {onnx_path}  ({onnx_path.stat().st_size / 1e6:.1f} MB)")
    print(f"wrote {facts_path}")
    print(f"model_id {facts['model_id']}  classes {', '.join(classes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
