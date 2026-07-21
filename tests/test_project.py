import pytest

from xtalflow.domain import Project


def test_project_keeps_ordered_pinned_image_sets() -> None:
    project = Project.create("  FBDD Round 1  ")
    first = project.add_image_set("1070", 5947, "profileID_1", "first")
    second = project.add_image_set("1100", 6088, "profileID_1", "second")

    assert project.name == "FBDD Round 1"
    assert project.active_image_sets == (first, second)
    assert project.active_image_set_id == second.id
    assert first.source_key == ("1070", 5947, "profileID_1")


def test_same_physical_image_set_cannot_be_added_twice() -> None:
    project = Project.create("FBDD")
    project.add_image_set("1070", 5947, "profileID_1", "first")

    with pytest.raises(ValueError, match="already"):
        project.add_image_set("1070", 5947, "profileID_1", "first")


def test_image_sets_can_be_reordered_archived_and_restored() -> None:
    project = Project.create("FBDD")
    first = project.add_image_set("1070", 5947, "profileID_1", "first")
    second = project.add_image_set("1100", 6088, "profileID_1", "second")

    project.move_image_set(second.id, -1)
    assert project.active_image_sets == (second, first)
    project.archive_image_set(second.id)
    assert project.active_image_sets == (first,)
    project.restore_image_set(second.id)
    assert project.active_image_sets == (first, second)
