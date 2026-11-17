"""Detect whether an instrument folder is backed by a mounted network share.

An unmounted mountpoint is still an ordinary empty directory, so writing to it
would succeed locally while the instrument never receives the worksheet.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
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


IMAGE_SHARE = "rmserver"
IMAGE_SHARE_LABEL = "RMServer"


@dataclass(frozen=True)
class ShareState:
    """One share as it looks from this machine, right now.

    Mounted or not: there is nothing in between to act on.  A folder that is
    there but is not a mounted share is no more usable than a folder that is
    missing - worse, in fact, because writing into it succeeds locally while
    the instrument never sees the file.  Why it is not mounted belongs in the
    detail, where someone fixing it will look, not in the mark.
    """

    key: str
    label: str
    path: Path
    mounted: bool
    detail: str
    # Whether the folder is there at all, apart from whether it is a share.
    # A folder that is missing is unusable everywhere; one that is merely
    # local is unusable only where a mounted share is required, and the two
    # cannot be told apart from ``mounted`` alone.
    exists: bool = True

    @property
    def state(self) -> str:
        """"ok" or "error", as the theme names its marks."""
        return "ok" if self.mounted else "error"


def share_state(key: str, label: str, path: Path, mount_table=None) -> ShareState:
    """Look at one share. Slow enough on a dropped mount to belong off the GUI thread.

    ``mount_table`` is a holder for the system's mount list, shared by a whole
    reading so that finding it - a subprocess on macOS - is paid once, and
    only by the first share that gets as far as needing it.
    """
    try:
        if not path.is_dir():
            return ShareState(
                key, label, path, False, "the folder is not there", exists=False
            )
        if mount_table is None:
            mounted = is_network_mount(path)
        else:
            # A table that could not be read is an answer, not a missing one.
            # Passing None on would read as "not supplied" and send every
            # share off to run ``mount`` again - "once per reading" failing
            # in exactly the case it was meant to hold for.
            text = mount_table.text()
            mounted = text is not None and is_network_mount(path, text)
    except OSError as error:
        return ShareState(key, label, path, False, str(error), exists=False)
    return ShareState(
        key, label, path, mounted,
        "mounted" if mounted else "a local folder, not a mounted share",
    )


NOT_ANSWERING = "not answering"
LOOK_DEADLINE_SECONDS = 5.0


def _shares_wanted(settings):
    wanted = [(IMAGE_SHARE, IMAGE_SHARE_LABEL, settings.rmserver_root)]
    wanted.extend(
        (item.instrument, item.label, item.output_directory)
        for item in settings.instruments
    )
    return wanted


def share_states(settings) -> tuple[ShareState, ...]:
    """Every share this installation depends on, one after another.

    Kept for callers outside the window, where waiting is what is wanted.
    The window uses :class:`ShareReader`, which will not wait for a share
    that has stopped answering.
    """
    table = _MountTable()
    return tuple(
        share_state(key, label, path, table)
        for key, label, path in _shares_wanted(settings)
    )


class ShareReader:
    """Looks at every share at once and never waits longer than a deadline.

    One thread each, because in a row a dropped mount's minute is paid again
    by every share behind it: one instrument away left the whole panel - the
    image share included - several minutes out of date.

    The deadline is how long the reading waits, not how long a look runs. A
    thread stuck inside ``is_dir`` on a hard mount cannot be stopped and may
    never return, so a share whose look is still out is not looked at again.
    That, not the deadline, is what keeps one dead share from piling threads
    up; and it is why a reading can always finish, where waiting for every
    share meant one that never answered stopped the readings for good.

    A share that has not answered is reported as not answering rather than
    with the answer it gave last. Not knowing has to read as "away": the
    screen then holds a worksheet back and the save, which asks the real
    folder at the moment it writes, has the last word.
    """

    def __init__(self, deadline_seconds: float = LOOK_DEADLINE_SECONDS) -> None:
        self._deadline = deadline_seconds
        self._lock = threading.Lock()
        self._looking: dict[str, threading.Thread] = {}
        self._answers: dict[str, ShareState] = {}

    def read(self, settings) -> tuple[ShareState, ...]:
        """Look at every share, waiting at most the deadline for the lot."""
        wanted = _shares_wanted(settings)
        table = _MountTable()
        started = []
        for key, label, path in wanted:
            looking = self._looking.get(key)
            if looking is not None and looking.is_alive():
                continue
            with self._lock:
                self._answers.pop(key, None)
            thread = threading.Thread(
                target=self._look, args=(key, label, path, table),
                name=f"xtalflow-share-{key}", daemon=True,
            )
            self._looking[key] = thread
            started.append(thread)
            thread.start()
        deadline = time.monotonic() + self._deadline
        for thread in started:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            return tuple(
                self._answers.get(key)
                or ShareState(key, label, path, False, NOT_ANSWERING, exists=False)
                for key, label, path in wanted
            )

    def _look(self, key: str, label: str, path: Path, table) -> None:
        state = share_state(key, label, path, table)
        with self._lock:
            self._answers[key] = state


class _MountTable:
    """The system's mount list, found once and only if something asks for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._text: str | None = None
        self._read = False

    def text(self) -> str | None:
        with self._lock:
            if not self._read:
                self._text = _system_mount_table()
                self._read = True
            return self._text
