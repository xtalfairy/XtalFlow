"""A record of what this program did, kept where it can still be read.

BL-5C asked for this after a morning nobody could reconstruct. The program
had told somebody the worksheets were in the instrument folders when they
were not, and there was no way afterwards to find out what the screen had
said or where the files had gone. The audit tables in reviews.sqlite3 hold
what was saved and uploaded, which is the record of the work; they do not
hold what the program believed at the time, and that is what a diagnosis
needs.

Three things decide the shape of this.

It is on local disk. The instrument folders are network shares that drop,
and a log that lives behind a dropped share cannot record the share
dropping. An unmounted mountpoint also accepts writes, so a log written
under one would vanish when the share came back - which is the same trap
the held-worksheet folder is there to avoid.

It never raises. A program that falls over because it could not write a
log line is worse than one with no log. Every write is guarded, and a
logger that cannot open its file degrades to doing nothing rather than to
failing. The one thing that IS said out loud is that logging is off, once,
on the standard error stream, so an operator who looks for the file knows
why it is not there.

It holds no secrets. MxLive is reached with a signed key and the signature
is in the URL; the key file's contents, the signature, and the record
bodies are never written here. What goes in is the endpoint, how many
records, the status, and how long it took.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

LOGGER_NAME = "xtalflow.operations"

# Enough to cover a beamtime of a few days and still be mailable when
# something goes wrong. Five files, so a crash loop cannot push the morning
# that matters out of the window in one afternoon.
MAX_BYTES = 4 * 1024 * 1024
BACKUP_COUNT = 5

# ``~/.local/state`` is the XDG place for state that should outlive a
# reboot and is not a cache. /var/tmp only if the home cannot be written,
# which at BL-5C means the network home is away: logging the failure of the
# share is exactly when this has to keep working.
FALLBACK_ROOT = Path("/var/tmp")


def default_log_directory() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / "XtalFlow" / "logs"


class _Fields(logging.Filter):
    """Put the account, process and host on every line.

    One line of a log is read on its own, out of a mail or a paste, long
    after the file it came from stopped being obvious. Two accounts on one
    server write two files, and two copies of the program in one account
    write the same file.
    """

    def __init__(self) -> None:
        super().__init__()
        self.account = _account()
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.account = self.account
        record.host = self.host
        return True


def _account() -> str:
    for name in ("LOGNAME", "USER", "USERNAME"):
        found = os.environ.get(name)
        if found:
            return found
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # pragma: no cover - no account name anywhere
        return "unknown"


class _LocalTimeFormatter(logging.Formatter):
    """Timestamps with the offset, in the zone the machine is set to.

    The clock on the wall and the offset both: somebody comparing this
    against a harvest file, a beamline log or a mail is in one zone and the
    server may be in another, and a bare local time cannot be lined up
    with anything.
    """

    def formatTime(  # noqa: N802 - logging API
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        moment = datetime.fromtimestamp(record.created).astimezone()
        return moment.strftime(datefmt or "%Y-%m-%d %H:%M:%S%z")


_FORMAT = (
    "%(asctime)s %(levelname)-7s %(account)s@%(host)s pid=%(process)d "
    "%(name)s %(message)s"
)

_configured = False
_lock = threading.Lock()


def configure(directory: Path | None = None) -> Path | None:
    """Start writing the log, and say where, or None if it cannot be.

    Called once at startup. Calling it again does nothing, so a second
    window in the same process does not double every line.
    """
    global _configured
    with _lock:
        if _configured:
            return _current_path()
        _configured = True
        for candidate in _candidates(directory):
            handler = _handler_at(candidate)
            if handler is not None:
                logger = logging.getLogger(LOGGER_NAME)
                logger.setLevel(logging.INFO)
                logger.propagate = False
                logger.addFilter(_Fields())
                logger.addHandler(handler)
                return candidate
        # Said once, where it cannot be swallowed: an operator looking for
        # the file is owed the reason it is not there.
        print(
            "XtalFlow: no operations log could be opened; continuing without one",
            file=sys.stderr,
        )
        return None


def _candidates(directory: Path | None) -> tuple[Path, ...]:
    if directory is not None:
        return (Path(directory),)
    return (
        default_log_directory(),
        FALLBACK_ROOT / f"XtalFlow-logs-{_account()}",
    )


def _handler_at(directory: Path) -> logging.Handler | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / "xtalflow.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )
    except OSError:
        # A read-only home, a full disk, a network home that is away, a
        # folder owned by somebody else. The next candidate, or none.
        return None
    handler.setFormatter(_LocalTimeFormatter(_FORMAT))
    return handler


def _current_path() -> Path | None:
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        filename = getattr(handler, "baseFilename", None)
        if filename:
            return Path(filename)
    return None


def log_directory() -> Path | None:
    """Where the log is being written, for the window to offer to open."""
    path = _current_path()
    return path.parent if path is not None else None


def log_files() -> tuple[Path, ...]:
    """The log and its rotated backups, newest first, for diagnostics."""
    path = _current_path()
    if path is None:
        return ()
    found = [path] + [
        path.with_name(f"{path.name}.{index}")
        for index in range(1, BACKUP_COUNT + 1)
    ]
    return tuple(item for item in found if item.exists())


def event(name: str, /, **fields: Any) -> None:
    """Record one thing that happened, as name and key=value pairs.

    Never raises. A log that can bring the program down is not worth
    having, and this is called from worker threads, from excepthooks and
    from the middle of a save.

    ``name`` is positional-only, because ``name`` is also the most natural
    thing to call a field: ``event("setting", name=..., value=...)`` read
    perfectly and raised TypeError until it was.
    """
    try:
        logging.getLogger(LOGGER_NAME).info("%s %s", name, _rendered(fields))
    except Exception:  # pragma: no cover - logging must never propagate
        pass


def problem(name: str, /, **fields: Any) -> None:
    """Record something that went wrong, at a level a reader can grep for."""
    try:
        logging.getLogger(LOGGER_NAME).error("%s %s", name, _rendered(fields))
    except Exception:  # pragma: no cover
        pass


def exception(name: str, /, **fields: Any) -> None:
    """Record something that went wrong, with the traceback that is current."""
    try:
        logging.getLogger(LOGGER_NAME).exception("%s %s", name, _rendered(fields))
    except Exception:  # pragma: no cover
        pass


def _rendered(fields: dict[str, Any]) -> str:
    """Key=value, quoted only where a reader would otherwise be misled.

    Not JSON: these lines are read by eye in a terminal far more often than
    they are parsed, and grep for one well or one path is the common case.
    """
    parts = []
    for key, value in fields.items():
        text = "-" if value is None else str(value)
        if text == "" or any(character in text for character in " \t\"'"):
            text = '"' + text.replace('"', "'") + '"'
        parts.append(f"{key}={text}")
    return " ".join(parts)


def commit_hash(root: Path | None = None) -> str:
    """Which build this is - the same answer the title bar gives.

    Delegates to ``xtalflow.build``, which reads it from the files at import.
    This used to read it again on every call, twelve characters where the
    title said seven and "unknown" where the title said nothing, and it could
    not read a worktree at all. Worse, reading per call meant a diagnostics
    report written after the server fast-forwarded the install named a commit
    this process was not running.
    """
    from xtalflow.build import BUILD, read_build

    build = read_build(root) if root is not None else BUILD
    return build.for_log


def record_startup(settings: Any, profile: str | None = None) -> None:
    """The build and the settings it is running with, once.

    Half of diagnosing a report is knowing which build and which profile
    the person was on: the development settings create missing instrument
    folders and the operating ones refuse to, and the same symptom means
    different things under each.
    """
    from xtalflow import __version__
    from xtalflow.build import BUILD, UNREADABLE

    event(
        "startup",
        # The version and the folder as well as the commit. Several versions
        # are installed beside one another sharing one config file, so which
        # directory this came from is the most direct answer to "which
        # installation is this" - and the only one that shows a version
        # number that disagrees with the folder holding it.
        version=__version__,
        commit=BUILD.for_log,
        root=BUILD.root,
        profile=profile or os.environ.get("XTALFLOW_SETTINGS") or "development",
        python=f"{sys.version_info.major}.{sys.version_info.minor}",
        log=_current_path(),
    )
    if BUILD.state == UNREADABLE:
        # Said here and not at import, which is before this log exists. A
        # checkout whose commit cannot be read would otherwise look like an
        # install from a tarball, which has no commit to read.
        problem("build-not-read", root=BUILD.root, reason=BUILD.detail)
    for name in (
        "create_missing_instrument_roots",
        "require_network_instrument_mounts",
        "require_network_image_root",
        "rmserver_root",
        "fragment_library_directory",
        "pending_worksheet_directory",
        "mxlive_base_url",
        "mxlive_beamline",
    ):
        if hasattr(settings, name):
            event("setting", name=name, value=getattr(settings, name))
    # The key file by path only. What is in it is a private key.
    key = getattr(settings, "mxlive_key_path", None)
    if key is not None:
        event("setting", name="mxlive_key_path", value=key)
    for instrument in getattr(settings, "instruments", ()):
        event(
            "instrument",
            id=instrument.instrument,
            worksheet=getattr(instrument.worksheet, "value", instrument.worksheet),
            directory=instrument.output_directory,
        )


def install_exception_hooks() -> None:
    """Catch what nothing else caught, on the main thread and on workers.

    A worker thread dying took its traceback with it: the window showed a
    step that never finished and the reason was gone. threading.excepthook
    is the only place that sees it.
    """
    previous = sys.excepthook

    def on_main(kind, value, trace) -> None:
        try:
            logging.getLogger(LOGGER_NAME).error(
                "unhandled-exception thread=main",
                exc_info=(kind, value, trace),
            )
        except Exception:  # pragma: no cover
            pass
        previous(kind, value, trace)

    sys.excepthook = on_main

    previous_thread = threading.excepthook

    def on_thread(args) -> None:
        try:
            logging.getLogger(LOGGER_NAME).error(
                "unhandled-exception thread=%s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:  # pragma: no cover
            pass
        previous_thread(args)

    threading.excepthook = on_thread
