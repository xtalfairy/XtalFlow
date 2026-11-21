"""Which copy of this program is running, answered in one place.

There were three answers to that question and they disagreed. The title said
the version, which covered every commit the beamline's checkout was ever
moved to - 1.1.0 was both ebab939 and 1bc73a1, which differ in what they
refuse. ``operations_log.commit_hash`` read the commit twelve characters long
and said "unknown" on failure, every time it was called. A second reader,
added the same day without noticing the first, read it seven characters long
and said "" on failure, once at import. The two were never compared, and
nothing tested either.

Reading it at import rather than per call is the part that matters. The
install is a checkout the server fast-forwards, so the files on disk can
change while this process runs: a diagnostics report written after a
fast-forward named the new commit while the process was still running the old
code. What is loaded was decided at import, so that is when to ask.

Read from the files, never by running git. A subprocess at startup is a
freeze waiting to happen where git is slow or missing, and this has to work
before anything else does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["Build", "BUILD"]

NONE = "none"
"""No ``.git``: an install from a tarball, which has no commit to name."""
READ = "read"
"""A checkout, and its commit was read."""
UNREADABLE = "unreadable"
"""A checkout whose commit could not be read, which is worth saying.

Silence would make a broken read look like a tarball. The title says ``?``
rather than nothing, and the reason goes in the startup log - the failure is
kept on the value here because it happens at import, long before the log is
configured.
"""


@dataclass(frozen=True)
class Build:
    commit: str = ""
    state: str = NONE
    detail: str = ""
    root: str = ""

    @property
    def short(self) -> str:
        """Seven characters, which is what a person reads off a title bar."""
        return self.commit[:7]

    @property
    def title_suffix(self) -> str:
        if self.state == READ:
            return f" · {self.short}"
        if self.state == UNREADABLE:
            return " · ?"
        return ""

    @property
    def for_log(self) -> str:
        """Twelve characters, or the word for why there are none."""
        return self.commit[:12] if self.state == READ else self.state


def _ref_in(directory: Path, reference: str) -> str:
    loose = directory / reference
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip()
    packed = directory / "packed-refs"
    if packed.is_file():
        for row in packed.read_text(encoding="utf-8").splitlines():
            if row.endswith(f" {reference}"):
                return row.split(" ", 1)[0]
    return ""


def read_build(root: Path) -> Build:
    """What ``root`` is, from its own files.

    Four layouts, and this repository is only ever one of them. A plain
    checkout keeps a ``.git`` directory. A fresh clone - which is what the
    beamline makes to test a commit - keeps its refs packed until something
    writes one. A checkout of a commit rather than a branch has the commit in
    ``HEAD``. A worktree or submodule keeps ``.git`` as a file naming the real
    directory, possibly relative; and a worktree's own directory holds only
    its ``HEAD``, with the branch refs in the common directory that
    ``commondir`` names. Reading the ref beside ``HEAD`` and finding nothing
    there was the bug: every real worktree answered "".
    """
    where = str(root)
    git = root / ".git"
    try:
        if git.is_file():
            named = git.read_text(encoding="utf-8").strip()
            if not named.startswith("gitdir:"):
                return Build(state=UNREADABLE, detail=".git names no gitdir", root=where)
            git = Path(named.split(":", 1)[1].strip())
            if not git.is_absolute():
                git = root / git
        if not (git / "HEAD").is_file():
            return Build(state=NONE, root=where)
        common = git
        marker = git / "commondir"
        if marker.is_file():
            named = marker.read_text(encoding="utf-8").strip()
            shared = Path(named)
            common = shared if shared.is_absolute() else git / shared
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            if head:
                return Build(commit=head, state=READ, root=where)
            return Build(state=UNREADABLE, detail="HEAD is empty", root=where)
        reference = head.split(":", 1)[1].strip()
        # Beside HEAD first, because a worktree may hold its own ref there,
        # then the common directory, where a worktree's branches live.
        for directory in (git, common):
            found = _ref_in(directory, reference)
            if found:
                return Build(commit=found, state=READ, root=where)
        return Build(
            state=UNREADABLE, detail=f"no ref for {reference}", root=where
        )
    except (OSError, ValueError, IndexError) as error:
        return Build(
            state=UNREADABLE, detail=type(error).__name__, root=where
        )


BUILD = read_build(Path(__file__).resolve().parents[2])
"""Read once. What is loaded was decided at import, so that is when to ask."""
