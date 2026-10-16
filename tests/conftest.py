import getpass

import pytest


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
