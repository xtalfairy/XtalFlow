"""Detect whether an instrument folder is backed by a mounted network share.

An unmounted mountpoint is still an ordinary empty directory, so writing to it
would succeed locally while the instrument never receives the worksheet.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


NETWORK_FILESYSTEMS = frozenset({"cifs", "smb3", "smbfs", "nfs", "nfs4"})
LINUX_MOUNT_TABLE = Path("/proc/self/mounts")


def is_network_mount(path: Path, mount_table: str | None = None) -> bool:
    real_path = os.path.realpath(path)
    if mount_table is None:
        mount_table = _system_mount_table()
        if mount_table is None:
            return False
    filesystem = _filesystem_type(real_path, mount_table)
    return filesystem in NETWORK_FILESYSTEMS


def _filesystem_type(real_path: str, mount_table: str) -> str | None:
    best_mountpoint = ""
    best_type = None
    for line in mount_table.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mountpoint = _decode_mount_field(fields[1])
        if _is_within(real_path, mountpoint) and len(mountpoint) >= len(best_mountpoint):
            best_mountpoint = mountpoint
            best_type = fields[2]
    return best_type


def _is_within(path: str, mountpoint: str) -> bool:
    if mountpoint == "/":
        return True
    return path == mountpoint or path.startswith(mountpoint.rstrip("/") + "/")


def _decode_mount_field(value: str) -> str:
    # /proc/self/mounts escapes spaces, tabs, newlines, and backslashes as octal.
    for escaped, character in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escaped, character)
    return value


_BSD_MOUNT_LINE = re.compile(r"^(?P<device>.+) on (?P<mountpoint>.+) \((?P<type>[^,)]+)")


def _system_mount_table() -> str | None:
    try:
        return LINUX_MOUNT_TABLE.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        # macOS and other BSDs: "//user@host/share on /Volumes/share (smbfs, ...)".
        output = subprocess.run(
            ["mount"], capture_output=True, text=True, check=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return bsd_mount_table(output)


def bsd_mount_table(output: str) -> str:
    """Convert BSD ``mount`` output to the /proc/self/mounts field layout."""
    lines = []
    for line in output.splitlines():
        match = _BSD_MOUNT_LINE.match(line)
        if match:
            mountpoint = match["mountpoint"].replace("\\", "\\134").replace(" ", "\\040")
            lines.append(f"device {mountpoint} {match['type']}")
    return "\n".join(lines)
