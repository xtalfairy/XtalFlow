"""Landing-page actions remain independent of experiment persistence."""

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QToolTip

from xtalflow.domain import PlanType
from xtalflow.ui.home_page import HomePage, RecentExperiment, WorkspaceEntry
from xtalflow.ui.workspace_sidebar import WorkspaceSidebar


def test_home_creation_and_empty_state():
    app = QApplication.instance() or QApplication([])
    page = HomePage()
    sidebar = WorkspaceSidebar()
    requested = []
    sidebar.start_requested.connect(requested.append)
    assert page.recent_table.isHidden()
    assert not page.recent_empty_label.isHidden()
    assert not page.resume_button.isEnabled()
    for kind, button in sidebar.start_buttons.items():
        button.click()
        assert requested[-1] == kind
    page.close()
    sidebar.close()
    app.processEvents()


def test_home_keyboard_resume_and_clear():
    app = QApplication.instance() or QApplication([])
    page = HomePage()
    page.show_recent_work((RecentExperiment(
        "workspace", "Demo", "plan", "Example", PlanType.RAW_CRYSTAL,
        "Draft", datetime.now(timezone.utc),
    ),))
    page.show()
    app.processEvents()
    resumed = []
    page.resume_requested.connect(lambda *identity: resumed.append(identity))
    page.recent_table.setCurrentCell(0, 0)
    page.recent_table.setFocus()
    assert page.resume_button.isEnabled()
    QTest.keyClick(page.recent_table, Qt.Key_Return)
    assert resumed == [("workspace", "plan")]
    page.show_recent_work(())
    assert not page.resume_button.isEnabled()
    assert page.recent_count.text() == "0 experiments"
    page.close()


def test_home_delete_button_and_key_request_deletion():
    app = QApplication.instance() or QApplication([])
    page = HomePage()
    page.show_recent_work((RecentExperiment(
        "workspace", "Demo", "plan", "Example", PlanType.RAW_CRYSTAL,
        "Draft", datetime.now(timezone.utc),
    ),))
    page.show()
    app.processEvents()
    deleted = []
    page.delete_requested.connect(lambda *identity: deleted.append(identity))
    assert not page.delete_button.isEnabled()
    page.recent_table.selectRow(0)
    assert page.delete_button.isEnabled()
    page.delete_button.click()
    QTest.keyClick(page.recent_table, Qt.Key_Delete)
    assert deleted == [("workspace", "plan"), ("workspace", "plan")]
    page.close()


def test_experiment_list_shows_when_each_one_started_and_how_far_it_came():
    app = QApplication.instance() or QApplication([])
    page = HomePage()
    started = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
    edited = datetime(2026, 10, 9, 14, 5, tzinfo=timezone.utc)
    page.show_recent_work((RecentExperiment(
        "workspace", "Demo", "plan", "Example", PlanType.RAW_CRYSTAL,
        "Sent r1", edited, started, "ok", "The sample list for r1 is on MxLive.",
    ),))

    assert [
        page.recent_table.horizontalHeaderItem(column).text()
        for column in range(page.recent_table.columnCount())
    ] == ["Experiment", "Type", "Stage", "Workspace", "Started", "Edited"]
    assert page.recent_table.item(0, 2).text() == "Sent r1"
    assert page.recent_table.item(0, 4).text() == (
        started.astimezone().strftime("%Y-%m-%d %H:%M")
    )
    assert page.recent_table.item(0, 5).text() == (
        edited.astimezone().strftime("%Y-%m-%d %H:%M")
    )
    # The workspace column is the one that hides; the dates stay put.
    page.show_workspaces((WorkspaceEntry("workspace", "Demo"),), "workspace")
    assert page.recent_table.isColumnHidden(3)
    assert not page.recent_table.isColumnHidden(4)
    page.close()
    app.processEvents()


def test_the_stage_carries_a_mark_and_tells_its_story_on_click():
    """Shape and colour, never colour alone, and the detail one click away."""
    app = QApplication.instance() or QApplication([])
    page = HomePage()
    now = datetime.now(timezone.utc)
    page.show_recent_work((
        RecentExperiment(
            "w", "Demo", "a", "Done", PlanType.RAW_CRYSTAL, "Sent r1", now, now,
            "ok", "The sample list for r1 is on MxLive.",
        ),
        RecentExperiment(
            "w", "Demo", "b", "Look", PlanType.RAW_CRYSTAL,
            "Worksheets r1 · elsewhere", now, now, "attention",
            "Copy them to the instrument folders.",
        ),
        RecentExperiment(
            "w", "Demo", "c", "New", PlanType.RAW_CRYSTAL, "Draft · Setup", now, now,
        ),
    ))
    page.show()
    app.processEvents()

    assert all(
        not page.recent_table.item(row, 2).icon().isNull() for row in range(3)
    )
    assert page.recent_table.item(0, 2).toolTip() == (
        "Sent r1\n\nThe sample list for r1 is on MxLive."
    )
    # A stage without a story of its own keeps the plain tooltip.
    assert page.recent_table.item(2, 2).toolTip() == "Draft · Setup"

    page.recent_table.cellClicked.emit(1, 2)
    app.processEvents()
    assert QToolTip.text() == (
        "Worksheets r1 · elsewhere\n\nCopy them to the instrument folders."
    )
    page.close()
    app.processEvents()
