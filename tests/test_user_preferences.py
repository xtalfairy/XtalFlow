from dataclasses import replace
from pathlib import Path

from xtalflow.infrastructure.user_preferences import (
    JsonUserPreferencesStore,
    UserPreferences,
)


def test_auto_confirm_confidence_round_trips_in_user_file(tmp_path: Path) -> None:
    path = tmp_path / ".config" / "xtalflow" / "preferences.json"
    store = JsonUserPreferencesStore(path)

    assert store.load() == UserPreferences()
    store.save(UserPreferences(94, 5))

    assert store.load() == UserPreferences(94, 5)
    assert '"auto_confirm_confidence_percent": 94' in path.read_text("utf-8")
    assert '"auto_advance_target_count": 5' in path.read_text("utf-8")


def test_invalid_preferences_fall_back_to_safe_default(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text('{"auto_confirm_confidence_percent": 120}', encoding="utf-8")

    assert JsonUserPreferencesStore(path).load() == UserPreferences()


def test_the_default_confidence_accepts_the_wells_a_person_would() -> None:
    """Measured, not chosen: see the note beside the default.

    At 90 the fixture left 214 images of 1728 to confirm by hand, and all
    of the low-scoring ones that were looked at had the outline in the right
    place. A number that sends somebody clicking through two dozen correct
    boundaries on every plate is not protecting them from anything.
    """
    assert UserPreferences().auto_confirm_confidence_percent == 80


def test_a_number_already_chosen_is_not_moved_to_the_new_default(tmp_path: Path) -> None:
    """Lowering the default does not reach into files that already have a number.

    90 was the default before it was measured, so most stored 90s were never
    chosen - but some were, and nothing here can tell the two apart. The
    researchers who want 80 move the field once; nobody is moved by us.
    """
    path = tmp_path / "preferences.json"
    store = JsonUserPreferencesStore(path)
    store.save(UserPreferences(90))

    assert store.load().auto_confirm_confidence_percent == 90
    # Saved again, for any other reason: still 90.
    store.save(replace(store.load(), review_hint_shown=True))
    assert store.load().auto_confirm_confidence_percent == 90
