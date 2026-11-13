from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class UserPreferences:
    # Measured over the 1728 fixture images: 90 left 214 of them (12.4%) to
    # be confirmed by hand, 18 to 38 on every plate walked, and every one of
    # the 36 looked at had the outline in the right place. The score is low
    # where the well wall cuts the lens circle in a straight line and there
    # is no gradient to find, not where the circle is wrong. 80 leaves 20.
    # Somebody who has already chosen a number keeps it: this is the number
    # for somebody who has not.
    auto_confirm_confidence_percent: int = 80
    auto_advance_target_count: int | None = None
    review_hint_shown: bool = False
    workspace_panel_visible: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.auto_confirm_confidence_percent <= 100:
            raise ValueError("auto-confirm confidence must be between 0 and 100")
        if (
            self.auto_advance_target_count is not None
            and self.auto_advance_target_count < 1
        ):
            raise ValueError("auto-advance target count must be positive")


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
            count = data.get("auto_advance_target_count")
            return UserPreferences(
                int(data["auto_confirm_confidence_percent"]),
                int(count) if count is not None else None,
                bool(data.get("review_hint_shown", False)),
                bool(data.get("workspace_panel_visible", True)),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return UserPreferences()

    def save(self, preferences: UserPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "auto_confirm_confidence_percent":
                        preferences.auto_confirm_confidence_percent,
                    "auto_advance_target_count":
                        preferences.auto_advance_target_count,
                    "review_hint_shown": preferences.review_hint_shown,
                    "workspace_panel_visible": preferences.workspace_panel_visible,
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
