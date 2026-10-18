import getpass
import os

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
    """Keep viewer tests from reading or writing the developer's preferences."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))


@pytest.fixture(autouse=True)
def fixed_local_user(monkeypatch):
    """Answer the same account name whoever runs the suite.

    The login name decides the MxLive identity and the folder worksheets are
    written to, and root is deliberately refused an upload until it is mapped
    to a real account, so a run as root would otherwise fail where a run as a
    researcher passes.
    """
    monkeypatch.setattr(getpass, "getuser", lambda: "tester")
