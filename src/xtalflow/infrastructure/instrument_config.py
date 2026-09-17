"""Read the instruments that receive worksheets from the site TOML file."""

from __future__ import annotations

import sys
from pathlib import Path

from xtalflow.domain.instruments import WorksheetKind
from xtalflow.settings import InstrumentDestination

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.9/3.10 servers
    import tomli as tomllib


class InstrumentConfigurationError(ValueError):
    """The site instrument configuration is invalid."""


def load_instrument_destinations(
    config_path: Path | None,
) -> tuple[InstrumentDestination, ...] | None:
    """Return configured instruments, or None when the file configures none.

    Each ``[[instruments]]`` table needs ``id``, ``worksheet`` ("echo" or
    "shifter"), and ``directory``; ``label`` is optional.
    """
    if config_path is None or not config_path.is_file():
        return None
    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise InstrumentConfigurationError(
            f"cannot read instrument configuration {config_path}: {error}"
        ) from error
    entries = document.get("instruments")
    if entries is None:
        return None
    if not isinstance(entries, list) or not entries:
        raise InstrumentConfigurationError(
            "instruments must be a non-empty array of [[instruments]] tables"
        )
    destinations = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise InstrumentConfigurationError(f"instruments[{index}] must be a table")
        missing = [key for key in ("id", "worksheet", "directory") if not entry.get(key)]
        if missing:
            raise InstrumentConfigurationError(
                f"instruments[{index}] is missing {', '.join(missing)}"
            )
        try:
            destinations.append(
                InstrumentDestination(
                    str(entry["id"]).strip(),
                    WorksheetKind(str(entry["worksheet"]).strip().lower()),
                    Path(str(entry["directory"])).expanduser(),
                    str(entry.get("label") or "").strip(),
                )
            )
        except ValueError as error:
            raise InstrumentConfigurationError(
                f"instruments[{index}] is invalid: {error}"
            ) from error
    ids = [item.instrument for item in destinations]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise InstrumentConfigurationError(
            f"instrument ids must be unique: {', '.join(duplicates)}"
        )
    return tuple(destinations)
