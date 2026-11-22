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
            "Worksheets r1 · not on SHIFTER 2", now, now, "attention",
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
        "Worksheets r1 · not on SHIFTER 2\n\nCopy them to the instrument folders."
    )
    page.close()
    app.processEvents()


def test_a_long_stage_story_is_broken_into_readable_lines():
    """Qt lays a plain tooltip out on one line however long it is."""
    from xtalflow.ui.home_page import TOOLTIP_WIDTH, wrapped

    app = QApplication.instance() or QApplication([])
    page = HomePage()
    now = datetime.now(timezone.utc)
    detail = (
        "The beamline harvested all 36 crystals of r2 into pucks 5C-0029, "
        "5C-0030 and 5C-0031. Nothing after this is recorded in MxLive."
        "\n\nStarted 2026-10-03 18:12."
        "\nWorksheets r2 saved 2026-10-05 10:30 · ECHO 650, SHIFTER 1, SHIFTER 2."
    )
    page.show_recent_work((RecentExperiment(
        "w", "Demo", "a", "Harvest", PlanType.RAW_CRYSTAL, "Harvested r2",
        now, now, "ok", detail,
    ),))

    lines = page.recent_table.item(0, 2).toolTip().splitlines()
    assert max(len(line) for line in lines) <= TOOLTIP_WIDTH
    assert len(lines) > 6
    # The paragraphs stay apart, and a puck keeps its name on one line.
    assert "" in lines
    assert all("5C-" not in line or "5C-00" in line for line in lines)
    # Nothing is lost in the breaking.
    assert " ".join(wrapped(detail).split()) == " ".join(detail.split())
    page.close()
    app.processEvents()


def _lamp_is_whole(button) -> bool:
    """Whether the name this lamp shows is the whole of what it was given.

    A name too wide for its column is not shortened by Qt - it is cut off
    where the paint runs out, with no ellipsis to say so. "ECHO 650" drawn in
    a column meant for "SH… 0" became "ECHO 6", which reads as a different
    instrument. Shortening is fine and is checked elsewhere; being cut off
    without being shortened is the bug.
    """
    from PyQt5.QtGui import QFontMetrics

    metrics = QFontMetrics(button.font())
    overhead = button.minimumSizeHint().width() - metrics.width(button.text())
    return button.width() >= metrics.width(button.text()) + overhead


def _lamp_rows(sidebar):
    """What the panel drew, row by row, as a reader would see it."""
    grid = sidebar.shares_grid
    rows: dict[int, list] = {}
    for index in range(grid.count()):
        row, column, _, _ = grid.getItemPosition(index)
        rows.setdefault(row, []).append((column, grid.itemAt(index).widget().text()))
    return [
        [text for _, text in sorted(rows[row])] for row in sorted(rows)
    ]


def test_the_lamps_group_by_kind_and_keep_the_stations_on_one_row():
    """Five lamps in a 250px panel, which two fixed columns could not hold.

    Poured into two columns in arrival order, three harvesting stations broke
    across two rows and the third sat alone beside an empty cell - invisible
    while there were two stations, and the first thing a person said about
    the panel when there were three.

    So the kind decides the row. The stations do not fit a 250px row at their
    full names, so the names are shortened from the middle, which keeps the
    number: it is the only part that says which station this is. The full
    name stays in the tooltip, and nothing is shortened where there is room.
    """
    from xtalflow.infrastructure.mounts import ShareState
    from pathlib import Path

    app = QApplication.instance() or QApplication([])
    states = tuple(
        ShareState(key, label, Path("/smbmount") / key, True, "mounted", group=group)
        for key, label, group in (
            ("rmserver", "RMServer", "image"),
            ("echo650", "ECHO 650", "echo"),
            ("shifter0", "SHIFTER 0", "shifter"),
            ("shifter1", "SHIFTER 1", "shifter"),
            ("shifter2", "SHIFTER 2", "shifter"),
        )
    )

    sidebar = WorkspaceSidebar()
    assert sidebar.width() == 250, "the panel this had to fit"
    sidebar.show_share_states(states)
    sidebar.show()
    app.processEvents()
    rows = _lamp_rows(sidebar)

    # Every lamp shows the whole of the name it was given. The rows are
    # different lengths, so the grid needs a common denominator for them:
    # without it the two on the first row sat in two of three equal columns,
    # a third narrower than the width their names had been fitted to, and
    # were silently cut off.
    assert all(
        _lamp_is_whole(button) for button in sidebar.share_buttons.values()
    ), {
        key: (button.text(), button.width())
        for key, button in sidebar.share_buttons.items()
        if not _lamp_is_whole(button)
    }

    assert len(rows) == 2, "the image share rides with the ECHO, as it always did"
    assert rows[0] == ["RMServer", "ECHO 650"]
    assert len(rows[1]) == 3, "the three stations read as one set"
    # Shortened, and shortened in the middle: every one still ends in its own
    # number, and none of them is merely "SHIF…".
    assert [text[-1] for text in rows[1]] == ["0", "1", "2"]
    assert all(text.startswith("S") and "…" in text for text in rows[1])
    assert sidebar.share_buttons["shifter0"].toolTip().startswith("SHIFTER 0 · ")
    assert (
        sidebar.share_buttons["shifter0"].accessibleName() == "SHIFTER 0: mounted"
    )

    # Given room, nothing is shortened - which is every panel drawn before a
    # third station arrived.
    wider = WorkspaceSidebar()
    wider.setFixedWidth(320)
    wider.show_share_states(states)
    wider.show()
    app.processEvents()
    assert all(_lamp_is_whole(button) for button in wider.share_buttons.values())
    assert _lamp_rows(wider) == [
        ["RMServer", "ECHO 650"], ["SHIFTER 0", "SHIFTER 1", "SHIFTER 2"]
    ]

    # A fourth station follows the same rule rather than a written-down
    # count, and the rule prefers two rows of two over three and a stray one
    # - which was the complaint about the old layout. With the room that
    # gives them, their names are not shortened at all.
    with_fourth = states + (
        ShareState(
            "shifter3", "SHIFTER 3", Path("/smbmount/shifter3"), True, "mounted",
            group="shifter",
        ),
    )
    panel = WorkspaceSidebar()
    panel.show_share_states(with_fourth)
    assert _lamp_rows(panel) == [
        ["RMServer", "ECHO 650"],
        ["SHIFTER 0", "SHIFTER 1"],
        ["SHIFTER 2", "SHIFTER 3"],
    ]

    # The order the running program uses: the window is shown, and the first
    # reading arrives later from the thread that looks at the shares. The
    # buttons are made at that moment, and a button that has just been made
    # reports the width it was born with - wider than the one it will get,
    # because a nested layout does not give its children their geometry when
    # it is activated. Fitting a name to that width drew it cut off, and the
    # part lost was the number that says which station it is. Nothing
    # corrected it for a minute, which is how long the next reading takes.
    #
    # Checked before the processEvents that would hide it, because this is
    # what the first painted frame holds.
    shown_first = WorkspaceSidebar()
    shown_first.show()
    app.processEvents()
    shown_first.show_share_states(states)
    assert all(
        _lamp_is_whole(button) for button in shown_first.share_buttons.values()
    ), {
        key: (button.text(), button.width(), button.minimumSizeHint().width())
        for key, button in shown_first.share_buttons.items()
        if not _lamp_is_whole(button)
    }
    assert [
        button.text()[-1] for key, button in shown_first.share_buttons.items()
        if key.startswith("shifter")
    ] == ["0", "1", "2"], "the number is the part that must survive"

    # This case passes here whether or not the arithmetic that makes it safe
    # is present, because the thing that breaks it - a just-made button
    # reporting the width it was born with - does not happen on this machine.
    # It happens on the beamline's, where this order drew three stations with
    # no numbers for the first minute after startup. So this records the
    # order the program really uses; the proof is a render over there.

    # However they are split, one kind is shortened the same way throughout:
    # a name spelled out on one line and cut on the next reads as a mistake.
    panel = WorkspaceSidebar()
    panel.show_share_states(
        states
        + tuple(
            ShareState(
                f"shifter{n}", f"SHIFTER {n}", Path(f"/smbmount/shifter{n}"),
                True, "mounted", group="shifter",
            )
            for n in (3, 4)
        )
    )
    stations = [text for row in _lamp_rows(panel)[1:] for text in row]
    assert [text[-1] for text in stations] == ["0", "1", "2", "3", "4"]
    assert len({text[:-1] for text in stations}) == 1, "shortened the same way"
