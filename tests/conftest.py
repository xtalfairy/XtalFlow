import getpass
import os

# Decide the installation before anything imports xtalflow: settings read the
# environment once, when the module is first imported. The operating server
# exports XTALFLOW_SETTINGS=operating for every login, so a run there would
# otherwise point the suite at the real image share and write worksheets into
# the instrument folders, which marks wells on the shared source plates.
os.environ.pop("XTALFLOW_SETTINGS", None)

import pytest


@pytest.fixture(autouse=True)
def _keep_the_error_hook_to_one_test():
    """Put ``sys.excepthook`` back after every test.

    ``viewer.main`` installs the window's own handler there and nothing takes
    it off again, which is right for the program and wrong for a suite: once
    one test has called ``main``, every later test runs under the handler of
    a window that has been closed. An exception inside a Qt slot does not
    return to its caller - PyQt5 would end the process, which is loud - but
    that leaked handler instead opened a modal error box, with no one to
    close it, and the run stopped where it stood. It took a sampled stack of
    a wedged process to find out why, because a hung run says nothing at all.

    Restoring it does not make slot exceptions harmless. It makes them loud
    again, which is the point.
    """
    import sys

    original = sys.excepthook
    try:
        yield
    finally:
        sys.excepthook = original


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
