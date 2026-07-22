from pathlib import Path

from xtalflow.infrastructure.user_preferences import (
    JsonUserPreferencesStore,
    UserPreferences,
)


def test_auto_confirm_confidence_round_trips_in_user_file(tmp_path: Path) -> None:
    path = tmp_path / ".config" / "xtalflow" / "preferences.json"
    store = JsonUserPreferencesStore(path)

    assert store.load() == UserPreferences(90)
    store.save(UserPreferences(94))

    assert store.load() == UserPreferences(94)
    assert '"auto_confirm_confidence_percent": 94' in path.read_text("utf-8")


def test_invalid_preferences_fall_back_to_safe_default(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text('{"auto_confirm_confidence_percent": 120}', encoding="utf-8")

    assert JsonUserPreferencesStore(path).load() == UserPreferences(90)
