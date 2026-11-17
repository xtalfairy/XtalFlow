import threading
import time
from pathlib import Path

from xtalflow.infrastructure import mounts
from xtalflow.infrastructure.mounts import is_network_mount


MOUNT_TABLE = """\
/dev/sda1 / xfs rw 0 0
/dev/sdb1 /data xfs rw 0 0
//echo650/share /smbmount/echo650 cifs rw 0 0
//shifter/share\\040one /smbmount/shifter\\0401 smb3 rw 0 0
"""


def test_only_directories_inside_a_network_mount_are_accepted() -> None:
    assert is_network_mount(Path("/smbmount/echo650"), MOUNT_TABLE)
    assert is_network_mount(Path("/smbmount/echo650/scientist"), MOUNT_TABLE)
    assert is_network_mount(Path("/smbmount/shifter 1"), MOUNT_TABLE)
    assert not is_network_mount(Path("/smbmount/shifter2"), MOUNT_TABLE)
    assert not is_network_mount(Path("/smbmount/echo6500"), MOUNT_TABLE)
    assert not is_network_mount(Path("/data/echo650"), MOUNT_TABLE)


def test_bsd_mount_output_is_understood() -> None:
    from xtalflow.infrastructure.mounts import bsd_mount_table

    table = bsd_mount_table(
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
        "//fbdd@echo650/share on /Volumes/echo 650 (smbfs, nodev, nosuid, mounted by fbdd)\n"
    )

    assert is_network_mount(Path("/Volumes/echo 650/scientist"), table)
    assert not is_network_mount(Path("/Users/fbdd/echo650"), table)


def test_a_share_is_mounted_or_it_is_not(tmp_path, monkeypatch) -> None:
    """Two states: a folder that is there but is not a share is no more usable."""
    from dataclasses import replace

    from xtalflow.infrastructure import mounts
    from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments

    mounted = tmp_path / "echo650"
    local = tmp_path / "shifter1"
    for directory in (mounted, local):
        directory.mkdir()
    monkeypatch.setattr(
        mounts, "is_network_mount", lambda path, table=None: path == mounted
    )
    settings = replace(
        DEFAULT_SETTINGS,
        rmserver_root=mounted,
        instruments=standard_instruments(mounted, local, tmp_path / "gone"),
    )

    states = {state.key: state for state in mounts.share_states(settings)}

    assert [state.key for state in mounts.share_states(settings)] == [
        "rmserver", "echo650", "shifter1", "shifter2"
    ]
    assert states["echo650"].state == "ok"
    assert states["echo650"].detail == "mounted"
    # There, writable, and the instrument would never see the file: not usable,
    # so not a different state from missing. Only the reason differs.
    assert states["shifter1"].state == "error"
    assert "not a mounted share" in states["shifter1"].detail
    assert states["shifter2"].state == "error"
    assert states["shifter2"].detail == "the folder is not there"
    assert states["rmserver"].label == "RMServer"


def test_a_share_that_cannot_be_asked_is_not_called_mounted(tmp_path, monkeypatch) -> None:
    """A dropped mount raises instead of answering; that is not a green light."""
    from xtalflow.infrastructure import mounts

    share = tmp_path / "echo650"
    share.mkdir()

    def refuse(path, table=None):
        raise OSError(112, "Host is down")

    monkeypatch.setattr(mounts, "is_network_mount", refuse)

    state = mounts.share_state("echo650", "ECHO 650", share)

    assert not state.mounted
    assert state.state == "error"
    assert "Host is down" in state.detail


def test_a_mount_table_that_cannot_be_read_is_looked_for_once(monkeypatch) -> None:
    """The one reading must not send every share off to run ``mount`` again.

    A table that comes back empty is an answer. Handed on as None it reads as
    "not supplied", and each share looks for it afresh - the cost this holder
    exists to pay once, paid per share in exactly the case it was built for.
    """
    from dataclasses import replace

    from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments

    looked = []
    monkeypatch.setattr(
        mounts, "_system_mount_table", lambda: looked.append(1) or None
    )
    root = Path(__file__).parent
    settings = replace(
        DEFAULT_SETTINGS,
        rmserver_root=root,
        instruments=standard_instruments(root, root, root),
    )

    states = mounts.share_states(settings)

    assert len(looked) == 1
    assert [state.mounted for state in states] == [False] * len(states)
    # The folders are all there; only the mount answer is missing.
    assert all(state.exists for state in states)


def test_a_share_that_never_answers_does_not_stop_the_others(monkeypatch) -> None:
    """One dead mount must not hold the whole reading, or end the readings.

    A look that is still out is not started again, so a share nothing will
    ever hear from costs one stranded thread, not one per minute - and the
    reading still finishes, which is what lets the next one happen at all.
    """
    from dataclasses import replace

    from xtalflow.settings import DEFAULT_SETTINGS, standard_instruments

    never = Path("/never-answers")
    looks = []
    forever = threading.Event()
    real_is_dir = Path.is_dir

    def hanging_is_dir(self):
        if never == self:
            looks.append(self)
            # A hard mount: this call may never return. Bounded only so
            # that a reading which waits for it fails the clock below
            # instead of hanging the suite.
            forever.wait(30)
            return False
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", hanging_is_dir)
    here = Path(__file__).parent
    settings = replace(
        DEFAULT_SETTINGS,
        rmserver_root=here,
        instruments=standard_instruments(never, here, here),
    )
    reader = mounts.ShareReader(deadline_seconds=0.3)

    try:
        started = time.monotonic()
        first = reader.read(settings)
        # The reading finished without the dead share. This is the guarantee
        # the whole class exists for: a reading that cannot finish stops
        # every later one, and the lamps freeze at values nothing dates.
        assert time.monotonic() - started < 2.0
        # The others answered; the dead one is reported as what it is.
        assert {state.key: state.detail for state in first}["echo650"] == (
            mounts.NOT_ANSWERING
        )
        assert [state.mounted for state in first].count(False) == len(first)
        assert any(state.key == "shifter1" and state.exists for state in first)

        # A second reading still happens, and does not start a second look.
        second = reader.read(settings)
        assert len(looks) == 1
        assert {state.key: state.detail for state in second}["echo650"] == (
            mounts.NOT_ANSWERING
        )
    finally:
        forever.set()
