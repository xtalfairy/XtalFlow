import getpass
import os
import sys

# Decide the installation before anything imports xtalflow: settings read the
# environment once, when the module is first imported. The operating server
# exports XTALFLOW_SETTINGS=operating for every login, so a run there would
# otherwise point the suite at the real image share and write worksheets into
# the instrument folders, which marks wells on the shared source plates.
os.environ.pop("XTALFLOW_SETTINGS", None)

import pytest


def pytest_configure(config) -> None:
    """Refuse to run against any folder outside the repository."""
    from xtalflow.settings import DEFAULT_SETTINGS, PROJECT_ROOT

    outside = [
        path
        for path in (
            DEFAULT_SETTINGS.rmserver_root,
            # None asks for a folder of the running account's own, which is
            # never a folder of this repository's.
            DEFAULT_SETTINGS.worksheet_staging_directory or PROJECT_ROOT / "x",
            *(item.output_directory for item in DEFAULT_SETTINGS.instruments),
        )
        if PROJECT_ROOT not in path.parents
    ]
    if outside:
        raise pytest.UsageError(
            "the tests would read and write outside the repository: "
            + " · ".join(str(path) for path in outside)
        )


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path_factory, monkeypatch):
    """Keep viewer tests from reading or writing the developer's own files.

    XDG_STATE_HOME as well as XDG_CONFIG_HOME: main() opens the operations
    log, and the suite calls main() many times, so a run wrote the
    developer's real ~/.local/state and left the log of a test run where
    somebody would later read it as the log of a real session.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))


@pytest.fixture(autouse=True)
def fixed_local_user(monkeypatch):
    """Answer the same account name whoever runs the suite.

    The login name decides the MxLive identity and the folder worksheets are
    written to, and root is deliberately refused an upload until it is mapped
    to a real account, so a run as root would otherwise fail where a run as a
    researcher passes.
    """
    monkeypatch.setattr(getpass, "getuser", lambda: "tester")


@pytest.fixture(autouse=True)
def no_window_outlives_its_test():
    """Delete every window a test leaves behind, here, on the main thread.

    ``window.close()`` only hides a window. What a viewer test leaves is a
    live window, owned by Python, with timers still running - remaining
    times from tens of milliseconds to twelve seconds. It becomes
    collectable a second or two later, once its short timers have fired and
    some later test has pumped events twice.

    By then the collector may reach it from a worker thread: this program
    reads the image share and scores crystals off the interface thread, and
    a full run sees five to twelve collections there, some of generation one
    with thousands of objects. PyQt does not delete a C++ window from a
    thread that does not own it, so what is left is a window still alive,
    its timers still running, and its Python side cleared. The next timeout
    then runs a slot whose closure has been emptied, and the interpreter
    dereferences NULL: a segmentation fault inside another test's
    processEvents, blamed on whichever test happened to be running.

    That is what made the suite fail about one run in five while every test
    in it was correct - and an intermittently lying suite is worse than a
    failing one, because it is the only thing that says whether this program
    may write to a beamline database.

    Deleting here removes the condition rather than the symptom: there is
    nothing left to collect, on any thread. Each window is found before any
    is deleted, because deleting one destroys the menus and popups it owns
    and asking a destroyed widget for its parent is itself fatal.
    """
    yield
    widgets = sys.modules.get("PyQt5.QtWidgets")
    if widgets is None or widgets.QApplication.instance() is None:
        return
    from PyQt5 import sip

    left_behind = [
        widget
        for widget in widgets.QApplication.topLevelWidgets()
        if widget.parent() is None
    ]
    for widget in left_behind:
        if not sip.isdeleted(widget):
            sip.delete(widget)
