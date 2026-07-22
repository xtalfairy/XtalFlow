from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class UserPreferences:
    auto_confirm_confidence_percent: int = 90

    def __post_init__(self) -> None:
        if not 0 <= self.auto_confirm_confidence_percent <= 100:
            raise ValueError("auto-confirm confidence must be between 0 and 100")


def default_preferences_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "xtalflow" / "preferences.json"


class JsonUserPreferencesStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_preferences_path()

    def load(self) -> UserPreferences:
        if not self.path.is_file():
            return UserPreferences()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("preferences root must be an object")
            return UserPreferences(int(data["auto_confirm_confidence_percent"]))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return UserPreferences()

    def save(self, preferences: UserPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"auto_confirm_confidence_percent":
                 preferences.auto_confirm_confidence_percent},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
