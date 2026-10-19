"""Read the review database and say, well by well, why a well needs attention.

Read-only: it opens the database, prints, and changes nothing.  The window's
"Needs attention" list and the boundary status line above the image read the
same rows, so when the two disagree this says which rows each one found.

    python codex/well_status.py                     # the window's own database
    python codex/well_status.py /path/reviews.sqlite3

A well needs attention for one of three reasons, and they are not the same:

  no boundary        there is no calibration row at all; confirming cannot help,
                     only "Set 3 points" or "Detect again" can
  unconfirmed        a calibration row with confirmed = 0
  outside the well   a confirmed boundary with a position beyond it; confirming
                     cannot help either, the boundary or the position must move

The column that catches people out is "image set".  One plate imaged twice has
one image set per batch, and a boundary confirmed on one batch says nothing
about the other - on screen it is green, while the well the experiment holds
sits in another image set, still unconfirmed.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def default_database() -> Path | None:
    try:
        from PyQt5.QtCore import QStandardPaths
    except ImportError:
        return None
    root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    return Path(root) / "reviews.sqlite3" if root else None


def issue(row: sqlite3.Row) -> str:
    if row["center_x_px"] is None:
        return "no boundary"
    if not row["confirmed"]:
        return "unconfirmed"
    distance = (
        ((row["x_px"] - row["center_x_px"]) / row["radius_x_px"]) ** 2
        + ((row["y_px"] - row["center_y_px"]) / row["radius_y_px"]) ** 2
    )
    return "outside the well" if distance > 1 else ""


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else default_database()
    if path is None:
        print("give the database path: python codex/well_status.py <reviews.sqlite3>")
        return 2
    if not path.is_file():
        print(f"no database at {path}")
        return 2
    print(f"database: {path}\n")

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT t.experiment_id, t.image_set_id, t.image_key, t.target_id,
               t.x_px, t.y_px,
               s.plate_code, s.batch_id, s.profile, s.plate_format_id,
               s.plate_format_version, s.archived_at,
               c.center_x_px, c.center_y_px, c.radius_x_px, c.radius_y_px,
               c.confirmed, c.method, c.confidence
        FROM image_set_target_point AS t
        JOIN project_image_set AS s ON s.image_set_id = t.image_set_id
        LEFT JOIN image_set_image_calibration AS c
               ON c.image_set_id = t.image_set_id AND c.image_key = t.image_key
        ORDER BY t.experiment_id, s.plate_code, s.batch_id, t.image_key
        """
    ).fetchall()
    if not rows:
        print("no selected positions in this database")
        return 0

    # One plate with several image sets is the trap, so say it first.
    batches: dict[str, set[int]] = {}
    for row in rows:
        batches.setdefault(row["plate_code"], set()).add(row["batch_id"])
    for plate, seen in sorted(batches.items()):
        if len(seen) > 1:
            print(
                f"!  plate {plate} holds positions in {len(seen)} image sets "
                f"(batches {', '.join(str(batch) for batch in sorted(seen))}). "
                "A boundary confirmed on one batch leaves the other untouched."
            )
    if any(len(seen) > 1 for seen in batches.values()):
        print()

    wells: dict[tuple, list[sqlite3.Row]] = {}
    for row in rows:
        wells.setdefault(
            (row["experiment_id"], row["image_set_id"], row["image_key"]), []
        ).append(row)

    experiment = None
    for (experiment_id, image_set_id, image_key), positions in wells.items():
        if experiment_id != experiment:
            experiment = experiment_id
            print(f"\n=== experiment {experiment_id or '(none)'}")
        first = positions[0]
        issues = [found for found in (issue(row) for row in positions) if found]
        boundary = (
            "none"
            if first["center_x_px"] is None
            else f"{first['method']} {first['confidence']:.0%} "
            f"{'confirmed' if first['confirmed'] else 'UNCONFIRMED'}"
        )
        state = (
            "ready" if not issues
            else " · ".join(sorted(set(issues)))
            + (f" ({len(issues)} of {len(positions)})" if "outside the well" in issues
               else "")
        )
        print(
            f"  {first['plate_code']} batch {first['batch_id']} "
            f"· image set {image_set_id[:8]} · {image_key}"
        )
        print(
            f"      {len(positions)} position(s) · boundary {boundary} "
            f"· format {first['plate_format_id']} v{first['plate_format_version']}"
            + ("  · IMAGE SET ARCHIVED" if first["archived_at"] else "")
        )
        print(f"      -> {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
