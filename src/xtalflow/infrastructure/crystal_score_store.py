"""Keep what the crystal classifier said, in a file of its own.

Beside the review database, not inside it. Two reasons, and they agree:

Everything here is derived. Lose the file and thirty seconds of scoring
rebuilds it; nothing a person typed or clicked lives in it. Derived data
belongs beside the record, not in it.

And the review database is shared by every installed version - the path comes
from the application name, not the version - so a schema past
LATEST_SCHEMA_VERSION makes an older XtalFlow refuse a database a newer one
has opened. Keeping out of it is what lets 0.9 and 1.0 run side by side on the
same plates, and what makes backing out of a version a matter of deleting one
file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from xtalflow.domain.crystal_score import CrystalScore

SCHEMA_VERSION = 1


class CrystalScoreStoreUnavailable(RuntimeError):
    """The score file cannot be opened or written."""


class SQLiteCrystalScoreStore:
    """Scores for one user, keyed by image set and image, with their model."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._open() as connection:
                self._migrate(connection)
        except (OSError, sqlite3.Error) as error:
            raise CrystalScoreStoreUnavailable(
                f"cannot open the crystal score file {self.database_path}: {error}"
            ) from error

    @contextmanager
    def _open(self):
        """A connection for one call, because scoring runs off the window's thread.

        SQLite refuses a connection used from a thread other than the one that
        made it. Rather than passing that rule around - and then having to
        think about the window reading while a worker writes - each call opens
        its own. A plate is a dozen writes, so this costs nothing measurable.
        """
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_score (
                    image_set_id TEXT NOT NULL,
                    image_key TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    classes TEXT NOT NULL,
                    probabilities TEXT NOT NULL,
                    label TEXT NOT NULL,
                    scored_at TEXT NOT NULL,
                    PRIMARY KEY (image_set_id, image_key)
                )
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error:
            connection.rollback()
            raise
        connection.commit()

    def scores_for(self, image_set_id: str, model_id: str) -> dict[str, CrystalScore]:
        """Every score of this image set that the model in use produced.

        Rows from another model are not returned: a retrained model answers
        differently, and a walk ordered by a mixture of two would be ordered
        by neither.
        """
        try:
            with self._open() as connection:
                rows = connection.execute(
                    "SELECT image_key, classes, probabilities, model_id "
                    "FROM image_score WHERE image_set_id = ? AND model_id = ?",
                    (image_set_id, model_id),
                ).fetchall()
        except sqlite3.Error as error:
            raise CrystalScoreStoreUnavailable(
                f"cannot read crystal scores: {error}"
            ) from error
        scores: dict[str, CrystalScore] = {}
        for image_key, classes, probabilities, stored_model in rows:
            try:
                scores[image_key] = CrystalScore(
                    image_key,
                    tuple(classes.split("\t")),
                    tuple(float(value) for value in probabilities.split("\t")),
                    stored_model,
                )
            except ValueError:
                continue  # A row written by something that did not finish.
        return scores

    def save(self, image_set_id: str, scores: Iterable[CrystalScore]) -> int:
        """Store scores, replacing any this model wrote for the same images."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [
            (
                image_set_id,
                score.image_key,
                score.model_id,
                "\t".join(score.classes),
                "\t".join(f"{value:.6f}" for value in score.probabilities),
                score.label,
                now,
            )
            for score in scores
        ]
        if not rows:
            return 0
        try:
            with self._open() as connection, connection:
                connection.executemany(
                    "INSERT OR REPLACE INTO image_score "
                    "(image_set_id, image_key, model_id, classes, probabilities, "
                    "label, scored_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.Error as error:
            raise CrystalScoreStoreUnavailable(
                f"cannot store crystal scores: {error}"
            ) from error
        return len(rows)

    def forget_other_models(self, image_set_id: str, model_id: str) -> int:
        """Drop scores from models no longer in use, once the new ones are in."""
        try:
            with self._open() as connection, connection:
                cursor = connection.execute(
                    "DELETE FROM image_score WHERE image_set_id = ? AND model_id != ?",
                    (image_set_id, model_id),
                )
        except sqlite3.Error as error:
            raise CrystalScoreStoreUnavailable(
                f"cannot tidy crystal scores: {error}"
            ) from error
        return cursor.rowcount

    def close(self) -> None:
        """Nothing is held open, so there is nothing to close."""


class MemoryCrystalScoreStore:
    """A store for tests and for a session that cannot write a file."""

    def __init__(self) -> None:
        self._scores: dict[tuple[str, str], CrystalScore] = {}

    def scores_for(self, image_set_id: str, model_id: str) -> dict[str, CrystalScore]:
        return {
            image_key: score
            for (stored_set, image_key), score in self._scores.items()
            if stored_set == image_set_id and score.model_id == model_id
        }

    def save(self, image_set_id: str, scores: Iterable[CrystalScore]) -> int:
        saved = 0
        for score in scores:
            self._scores[(image_set_id, score.image_key)] = score
            saved += 1
        return saved

    def forget_other_models(self, image_set_id: str, model_id: str) -> int:
        stale = [
            key for key, score in self._scores.items()
            if key[0] == image_set_id and score.model_id != model_id
        ]
        for key in stale:
            del self._scores[key]
        return len(stale)

    def close(self) -> None:
        self._scores.clear()


def crystal_score_database(review_database_path: Path, filename: str) -> Path:
    """The score file's place: beside the review database, never inside it."""
    return Path(review_database_path).expanduser().parent / filename
