"""The ONNX port must answer exactly what CrystAIze's TorchScript model does.

tests/fixtures/crystal_golden.json records what the original .pt said about 72
fixture images, so this can be checked on a machine with no torch installed -
which is every machine XtalFlow runs on.

Getting the preprocessing subtly wrong would not fail anything; it would
quietly make the model worse, and nobody would know which plates had been
walked in a worse order. That is what these tests exist to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xtalflow.infrastructure.crystal_scorer import (
    BATCH_SIZE,
    CrystalModelUnavailable,
    OnnxCrystalScorer,
    resized_size,
)
from xtalflow.infrastructure.rmserver import RockMakerImageRepository

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "rmserver"
GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "crystal_golden.json"
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "crystal3.onnx"

requires_fixture = pytest.mark.skipif(
    not FIXTURE_ROOT.is_dir(), reason="local RMServer fixture is not available"
)
requires_model = pytest.mark.skipif(
    not MODEL_PATH.is_file(),
    reason=(
        "no models/crystal3.onnx; make it with codex/export_crystal_onnx.py. "
        "The weights are copied in by hand and are not a repository asset."
    ),
)


def test_the_short_side_is_scaled_the_way_torchvision_scales_it() -> None:
    """Its own arithmetic truncates; rounding would differ by a pixel."""
    # RockMaker's own shape: 1224x1024 divides exactly, which is why a wrong
    # rule here would never show up on real images.
    assert resized_size(1224, 1024, 256) == (306, 256)
    assert resized_size(1024, 1224, 256) == (256, 306)
    # int(256 * 101 / 100) == 258, where rounding would give 259.
    assert resized_size(100, 101, 256) == (256, 258)
    assert resized_size(101, 100, 256) == (258, 256)
    # A square image keeps its shape.
    assert resized_size(500, 500, 256) == (256, 256)


def test_a_missing_model_says_so_rather_than_failing_later(tmp_path: Path) -> None:
    with pytest.raises(CrystalModelUnavailable, match="no crystal model"):
        OnnxCrystalScorer(tmp_path / "absent.onnx")

    (tmp_path / "model.onnx").write_bytes(b"not really a model")
    with pytest.raises(CrystalModelUnavailable, match="facts cannot be read"):
        OnnxCrystalScorer(tmp_path / "model.onnx")

    (tmp_path / "model.json").write_text('{"model_id": "x", "classes": ["a"]}')
    with pytest.raises(CrystalModelUnavailable, match="does not say"):
        OnnxCrystalScorer(tmp_path / "model.onnx")


@requires_model
@requires_fixture
def test_the_onnx_port_says_what_the_original_model_said() -> None:
    golden = json.loads(GOLDEN_PATH.read_text())
    scorer = OnnxCrystalScorer(MODEL_PATH)

    assert scorer.classes == tuple(golden["classes"])
    assert scorer.model_id.endswith(golden["source_sha256"][:12])

    class _Image:
        """Just enough of a CrystalImage for the scorer: a path and a key."""

        def __init__(self, path: Path, key: str) -> None:
            self.path = path
            self.image_key = key

    images = [
        _Image(FIXTURE_ROOT / row["path"], row["path"])
        for row in golden["predictions"]
    ]
    scores = {score.image_key: score for score in scorer.score(images)}

    assert len(scores) == len(golden["predictions"])
    worst = 0.0
    for row in golden["predictions"]:
        score = scores[row["path"]]
        assert score.label == row["label"], row["path"]
        for ours, theirs in zip(score.probabilities, row["probabilities"]):
            worst = max(worst, abs(ours - theirs))
    # Float32 accumulating in a different order, and nothing else.
    assert worst < 1e-4, f"probabilities drifted by {worst}"


@requires_model
@requires_fixture
def test_an_image_that_cannot_be_read_is_left_unscored_not_guessed_at(
    tmp_path: Path,
) -> None:
    """No score sends the image to the front of the walk, for a person to judge."""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"this is not a JPEG")
    repository = RockMakerImageRepository(FIXTURE_ROOT)
    plate = repository.load_plate("2070")
    scorer = OnnxCrystalScorer(MODEL_PATH)

    class _Image:
        def __init__(self, path: Path, key: str) -> None:
            self.path = path
            self.image_key = key

    # One unreadable image in the middle of a batch must not lose the rest.
    images = list(plate.images[:BATCH_SIZE - 1]) + [_Image(broken, "broken")]
    scores = scorer.score(images)

    assert len(scores) == BATCH_SIZE - 1
    assert "broken" not in {score.image_key for score in scores}


@requires_model
@requires_fixture
def test_every_score_names_the_model_that_made_it() -> None:
    """A model retrained later answers differently; the rows must say which."""
    repository = RockMakerImageRepository(FIXTURE_ROOT)
    plate = repository.load_plate("2070")
    scorer = OnnxCrystalScorer(MODEL_PATH)

    scores = scorer.score(plate.images[:4])

    assert {score.model_id for score in scores} == {scorer.model_id}
    assert all(
        abs(sum(score.probabilities) - 1.0) < 1e-5 for score in scores
    ), "probabilities must be a distribution, not logits"


def test_a_missing_pillow_turns_the_feature_off_rather_than_the_program(
    monkeypatch, tmp_path: Path
) -> None:
    """The beamline could not install onnxruntime at all; assume the same of any of these.

    This module has to import without Pillow, so a machine lacking it runs
    XtalFlow with the crystal feature absent - which is what every other
    missing piece does here - rather than failing to start. A module-level
    `from PIL import ...` would take the whole program down with it, and the
    import that matters is in the one function that needs it.
    """
    import builtins

    source = Path(OnnxCrystalScorer.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parent.parent / "src" / source).read_text()
    imports = [
        line for line in text.splitlines()
        if line.startswith(("import ", "from ")) and "PIL" in line
    ]
    assert not imports, f"Pillow must not be imported at module level: {imports}"

    real_import = builtins.__import__

    def without_pillow(name, *args, **kwargs):
        if name.startswith("PIL"):
            raise ImportError("no module named PIL")
        return real_import(name, *args, **kwargs)

    scorer = OnnxCrystalScorer.__new__(OnnxCrystalScorer)
    scorer.resize_short_side, scorer.input_size = 256, 224
    scorer.mean = scorer.std = 0.5
    monkeypatch.setattr(builtins, "__import__", without_pillow)
    with pytest.raises(CrystalModelUnavailable, match="Pillow is not installed"):
        scorer.prepare(tmp_path / "anything.jpg")
