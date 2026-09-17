"""Lab-approved example images shown while selecting wells."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


@dataclass(frozen=True)
class Example:
    image_path: Path
    caption: str


def load_examples(directory: Path | None) -> tuple[Example, ...]:
    """Images in name order, each captioned by a text file with the same name.

    ``good-crystal.png`` is captioned by ``good-crystal.txt``; without one the
    file name is used. A missing or unreadable folder has no examples.
    """
    if directory is None:
        return ()
    try:
        images = sorted(
            (
                path for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return ()
    examples = []
    for image in images:
        try:
            caption = image.with_suffix(".txt").read_text(encoding="utf-8").strip()
        except OSError:
            caption = ""
        examples.append(Example(image, caption or image.stem.replace("-", " ")))
    return tuple(examples)
