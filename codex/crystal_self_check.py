"""Measure the crystal classifier against positions people already placed.

The checkpoint CrystAIze ships has no held-out accuracy recorded anywhere, so
this is the first measurement of it. It asks one question per plate: if the
review had been ordered by crystal score, how early would the drops somebody
actually marked have come up?

Read-only. It opens the review database, reads the image share, scores
images, prints, and changes nothing.

    CRYSTAIZE=/Users/jjh/Dropbox/12_codebase/CrystAIze
    python codex/crystal_self_check.py models/crystal3.onnx
    python codex/crystal_self_check.py models/crystal3.onnx \
        --reviews /path/reviews.sqlite3 --root /smbmount/rmserver/.../WellImages

Only plates marked **before** the crystal ordering existed are an honest
sample: once somebody walks crystal first they are shown the top-ranked
images, so their marks rank first by construction. Every plate marked until
now qualifies, and after that the window keeps track itself.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xtalflow.domain.crystal_score import self_check
from xtalflow.infrastructure.crystal_scorer import OnnxCrystalScorer
from xtalflow.infrastructure.rmserver import RockMakerImageRepository


def default_reviews() -> Path | None:
    try:
        from PyQt5.QtCore import QStandardPaths
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        return None
    application = QApplication.instance() or QApplication([])
    application.setOrganizationName("XtalFlow")
    root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    return Path(root) / "reviews.sqlite3" if root else None


def marked_image_sets(database: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection.execute(
        """
        SELECT s.image_set_id, s.plate_code, s.batch_id, s.profile,
               COUNT(DISTINCT t.image_key) AS marked
        FROM project_image_set AS s
        JOIN image_set_target_point AS t ON t.image_set_id = s.image_set_id
        WHERE s.archived_at IS NULL
        GROUP BY s.image_set_id
        HAVING marked > 0
        ORDER BY marked DESC
        """
    ).fetchall()


def marked_keys(database: Path, image_set_id: str) -> set[str]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    return {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT image_key FROM image_set_target_point "
            "WHERE image_set_id = ?",
            (image_set_id,),
        )
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="crystal3.onnx")
    parser.add_argument("--reviews", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=None, help="WellImages root")
    parser.add_argument("--plates", type=int, default=0, help="stop after this many")
    arguments = parser.parse_args(argv[1:])

    database = arguments.reviews or default_reviews()
    if database is None or not database.is_file():
        print(f"no review database at {database}")
        return 2
    root = arguments.root or Path(
        "/smbmount/rmserver/RockMakerStorage/WellImages"
    )
    if not root.is_dir():
        print(f"no image root at {root}; give --root")
        return 2

    scorer = OnnxCrystalScorer(arguments.model)
    repository = RockMakerImageRepository(root)
    print(f"model:   {scorer.model_id}")
    print(f"reviews: {database}")
    print(f"images:  {root}\n")

    sets = marked_image_sets(database)
    if not sets:
        print("no image set in this database has a position on it")
        return 0
    if arguments.plates:
        sets = sets[: arguments.plates]

    passed = failed = unverified = 0
    for row in sets:
        label = f"plate {row['plate_code']} batch {row['batch_id']}"
        try:
            plate = repository.load_plate_batch(
                row["plate_code"], row["batch_id"], row["profile"]
            )
        except Exception as error:  # a plate whose images have gone
            print(f"{label}: images unavailable · {error}")
            continue
        scores = {
            score.image_key: score for score in scorer.score(plate.images)
        }
        keys = [image.image_key for image in plate.images]
        check = self_check(keys, scores, sorted(marked_keys(database, row["image_set_id"])))
        print(f"{label} · {len(plate.images)} images")
        print(f"   {check.sentence()}")
        if check.state == "pass":
            passed += 1
        elif check.state == "fail":
            failed += 1
        else:
            unverified += 1

    print(
        f"\n{passed} plate(s) where the marks ranked high, {failed} where they "
        f"did not, {unverified} with too few marks to say."
    )
    if failed > passed:
        print(
            "\nThe order is not earning its place on this site's plates. Ship "
            "1.0 with Crystal first unticked by default, or not at all."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
