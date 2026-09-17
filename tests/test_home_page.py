"""Landing-page actions remain independent of experiment persistence."""

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from xtalflow.domain import PlanType
from xtalflow.ui.home_page import HomePage, RecentExperiment
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
