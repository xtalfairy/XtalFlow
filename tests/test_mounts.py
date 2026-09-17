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
