"""XtalFlow's new implementation."""

from pathlib import Path

__version__ = "1.1.0"

# The version belongs in the title bar. Several versions are installed beside
# one another under /usr/local/fbdd_apps/XtalFlow/, sharing one config file,
# so "which one is this?" has no other answer from a running window or from a
# screenshot somebody attaches to a question.
APP_TITLE = f"XtalFlow {__version__}"


def _installed_build() -> str:
    """The commit this copy was installed at, short, or "" if there is none.

    The version alone stopped answering the question the comment above says
    it is for. The install on the beamline is a git checkout updated in
    place, so one version number covers every commit it is ever moved to:
    1.1.0 was both ebab939 and 1bc73a1, which differ in whether a long
    harvest comment is held back, whether an experiment ID already taken in
    another case counts as taken, and whether an ID that cannot be a file
    name is refused. Telling those apart took ``git log -1`` in the install
    directory - which a screenshot cannot do, and which is what somebody
    attaches to a question.

    Read from the files rather than by running git: the launcher's PATH is
    not ours to assume, and a subprocess at startup on a network home is a
    wait with nothing to show for it. Two small reads, and every failure is
    the same answer - no build, so the title is the version alone, which is
    what an install from a tarball should say.

    It names the commit, not the working tree. A checkout with edits on top
    still reports the commit under them, because finding out otherwise costs
    a full status; on the beamline the tree is clean and this is the whole
    answer.
    """
    try:
        root = Path(__file__).resolve().parent.parent.parent
        git = root / ".git"
        if git.is_file():
            # A worktree or a submodule: the file names the real directory,
            # and may name it relative to this one.
            named = git.read_text(encoding="utf-8").strip()
            if not named.startswith("gitdir:"):
                return ""
            git = Path(named.split(":", 1)[1].strip())
            if not git.is_absolute():
                git = root / git
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            # Detached, so HEAD is the commit itself.
            return head[:7]
        ref = head.split(":", 1)[1].strip()
        loose = git / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()[:7]
        # A fresh clone keeps its refs packed until something writes one.
        for row in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if row.endswith(f" {ref}"):
                return row.split(" ", 1)[0][:7]
        return ""
    except (OSError, ValueError, IndexError):
        return ""


INSTALLED_BUILD = _installed_build()
"""Read once, at import: the answer cannot change while the window is open."""

APP_WINDOW_TITLE = (
    f"{APP_TITLE} · {INSTALLED_BUILD}" if INSTALLED_BUILD else APP_TITLE
)
"""What a window says it is. ``APP_TITLE`` stays the version on its own,
because that number has a second job: it is the name of the installed folder
somebody types, and the test that keeps the two the same depends on it."""
