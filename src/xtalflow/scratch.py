"""Scratch folders that belong to the account using them.

Many researchers share the beamline machine, and they share its temporary
directory. A folder below one they share is created by whoever gets there
first, and that account's umask then decides whether anybody else can write
at all - so every account gets a folder of its own, named after it, and the
folder is checked before anything is written into it.
"""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path


def private_directory(name: str, user: str | None = None) -> Path:
    """Make, and return, a folder of this account's own.

    The account's name comes first in the path, so there is no shared parent
    for another account's umask to own.
    """
    account = user or getpass.getuser()
    root = Path(tempfile.gettempdir()) / f"{name}-{account}"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    refuse_a_folder_that_is_not_ours(root)
    return root


def refuse_a_folder_that_is_not_ours(root: Path) -> None:
    """Never read from or write into a folder someone else can write to.

    A shared temporary directory is writable by everyone, so a folder there
    may have been made by another account, or left world-writable, before
    this one asked for it.
    """
    current_user = getattr(os, "geteuid", None)
    if current_user is None:
        return
    info = root.stat()
    if info.st_uid != current_user():
        raise PermissionError(f"this folder belongs to another account: {root}")
    if info.st_mode & 0o022:
        raise PermissionError(f"this folder is writable by others: {root}")
