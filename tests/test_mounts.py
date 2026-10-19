from pathlib import Path

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
