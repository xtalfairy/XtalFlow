"""The log has to hold up on the morning it is needed, which is a bad one."""

from __future__ import annotations

import getpass
import logging
import threading
from pathlib import Path

import pytest

from xtalflow.infrastructure import operations_log


@pytest.fixture(autouse=True)
def fresh_logger(monkeypatch):
    """Start each test with the module unconfigured and the logger empty."""
    logger = logging.getLogger(operations_log.LOGGER_NAME)
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for filter_ in tuple(logger.filters):
        logger.removeFilter(filter_)
    monkeypatch.setattr(operations_log, "_configured", False)
    yield
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _lines(directory: Path) -> list[str]:
    return (directory / "xtalflow.log").read_text(encoding="utf-8").splitlines()


def test_a_line_says_when_where_who_and_which_process(tmp_path: Path) -> None:
    """One line out of a mail has to stand on its own.

    Two accounts on one server write two files and two copies of the program
    in one account write the same one, so the account and the pid are on
    every line, not in a header nobody will have.
    """
    operations_log.configure(tmp_path)
    operations_log.event("share", key="echo650", mounted=False)
    line = _lines(tmp_path)[0]

    assert "share key=echo650 mounted=False" in line
    assert "pid=" in line
    assert "@" in line  # account@host
    # The offset, because the reader may be in another zone than the server.
    assert line[:4].isdigit()
    assert "+" in line.split()[1] or "-" in line.split()[1]


def test_the_log_never_brings_the_program_down(tmp_path: Path, monkeypatch) -> None:
    """A program that dies because it could not log is worse than a silent one.

    This is called from worker threads, from excepthooks and from the middle
    of a save, so there is no caller in a position to handle it.
    """
    operations_log.configure(tmp_path)

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        logging.getLogger(operations_log.LOGGER_NAME), "info", explode
    )
    monkeypatch.setattr(
        logging.getLogger(operations_log.LOGGER_NAME), "error", explode
    )

    # None of these may raise.
    operations_log.event("share", key="echo650")
    operations_log.problem("mxlive-read", path="samples/BL-5C/")
    try:
        raise ValueError("nothing")
    except ValueError:
        operations_log.exception("unhandled-exception")


def test_a_folder_that_cannot_be_written_falls_back_and_says_so(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A read-only home is the case this has to survive, not the one it fails on.

    At BL-5C the home is network storage, so the folder this wants may be
    away exactly when a share going away is the thing worth recording.
    """
    refused = tmp_path / "refused"
    refused.write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "fallback"
    monkeypatch.setattr(operations_log, "default_log_directory", lambda: refused)
    monkeypatch.setattr(operations_log, "FALLBACK_ROOT", fallback)
    path = operations_log.configure()

    assert path is not None
    assert fallback in path.parents

    operations_log.event("startup")

    assert "startup" in _lines(path)[0]


def test_with_nowhere_to_write_the_program_goes_on_and_says_once(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """No log at all is survivable; an operator looking for the file is owed why."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(operations_log, "default_log_directory", lambda: blocked)
    monkeypatch.setattr(operations_log, "FALLBACK_ROOT", blocked)

    assert operations_log.configure() is None
    assert "without one" in capsys.readouterr().err

    operations_log.event("share", key="echo650")  # Must not raise.


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)
def test_a_worker_thread_that_dies_leaves_its_traceback(tmp_path: Path) -> None:
    """The one place a worker's traceback can be caught.

    A worker dying took its reason with it: the window showed a step that
    never finished and there was nothing to read afterwards.
    """
    operations_log.configure(tmp_path)
    before = threading.excepthook
    try:
        operations_log.install_exception_hooks()
        thread = threading.Thread(target=lambda: 1 / 0, name="share-reader")
        thread.start()
        thread.join()
    finally:
        threading.excepthook = before
    written = "\n".join(_lines(tmp_path))

    assert "unhandled-exception thread=share-reader" in written
    assert "ZeroDivisionError" in written


def test_a_field_with_a_space_is_still_one_field(tmp_path: Path) -> None:
    """grep for a path is the common way this is read."""
    operations_log.configure(tmp_path)
    operations_log.event("worksheet-file", path="/data/users/a b/x.csv", where="held")
    line = _lines(tmp_path)[0]

    assert 'path="/data/users/a b/x.csv"' in line
    assert "where=held" in line


def test_the_startup_line_names_the_build_and_the_profile(tmp_path: Path) -> None:
    """Half of diagnosing a report is knowing which build and which profile.

    The development settings create missing instrument folders and the
    operating ones refuse to, and the same symptom means different things
    under each.
    """
    from dataclasses import replace

    from xtalflow.settings import DEFAULT_SETTINGS

    operations_log.configure(tmp_path)
    operations_log.record_startup(
        replace(DEFAULT_SETTINGS, create_missing_instrument_roots=False),
        profile="operating",
    )
    written = "\n".join(_lines(tmp_path))

    assert "profile=operating" in written
    assert "setting name=create_missing_instrument_roots value=False" in written
    assert "instrument id=echo650" in written
    assert "commit=" in written


def test_no_key_and_no_signature_is_ever_written(tmp_path: Path) -> None:
    """MxLive is reached with the account's private key, signed into the URL.

    The log is the one place that must not hold it. Only the path of the key
    file is ever named, never what is in it.
    """
    from dataclasses import replace
    from pathlib import Path as _Path

    from xtalflow.settings import DEFAULT_SETTINGS

    operations_log.configure(tmp_path)
    operations_log.record_startup(
        replace(
            DEFAULT_SETTINGS,
            mxlive_key_path=_Path("/data/users/tester/.config/mxdc/keys.dsa"),
        )
    )
    written = "\n".join(_lines(tmp_path))

    assert "keys.dsa" in written  # The path is useful and safe.
    assert "BEGIN" not in written
    assert "PRIVATE" not in written


def test_diagnostics_can_be_copied_without_hunting_for_the_log(
    tmp_path: Path, monkeypatch
) -> None:
    """A report that says "it did not work this morning" cannot be acted on.

    So the build, the profile, the shares as last read and the end of the
    log go on the clipboard in one action, instead of somebody being asked
    to find and paste them by hand under time pressure.
    """
    from dataclasses import replace

    from PyQt5.QtWidgets import QApplication

    from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
    from xtalflow.infrastructure.mounts import ShareState
    from xtalflow.settings import DEFAULT_SETTINGS
    from xtalflow.viewer import ViewerWindow

    app = QApplication.instance() or QApplication([])
    operations_log.configure(tmp_path)
    operations_log.event("share", key="echo650", mounted=False, detail="not mounted")
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=replace(DEFAULT_SETTINGS, fragment_library_directory=tmp_path),
    )
    window._share_states = (
        ShareState("echo650", "ECHO 650", Path("/smbmount/echo"), False, "not mounted"),
    )
    window.copy_diagnostics()
    copied = QApplication.clipboard().text()

    assert "XtalFlow " in copied
    assert "echo650 mounted=False" in copied
    assert "/smbmount/echo" in copied
    # The log itself, so nobody has to be told where it is first.
    assert "share key=echo650" in copied
    assert str(tmp_path) in copied
    window.close()
    app.processEvents()


def test_with_no_log_the_folder_action_says_so_instead_of_opening_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """Nothing opened and nothing said is the one answer that wastes time."""
    from dataclasses import replace

    from PyQt5.QtWidgets import QApplication, QMessageBox

    from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
    from xtalflow.settings import DEFAULT_SETTINGS
    from xtalflow.viewer import ViewerWindow

    app = QApplication.instance() or QApplication([])
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=replace(DEFAULT_SETTINGS, fragment_library_directory=tmp_path),
    )
    said: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: said.append(args[2] if len(args) > 2 else ""),
    )
    opened: list[object] = []
    monkeypatch.setattr(
        "xtalflow.viewer.QDesktopServices.openUrl", lambda url: opened.append(url)
    )
    window.open_log_folder()

    assert not opened
    assert said and "no operations log" in said[0].lower()
    # And the other action still works, which is what it points at.
    window.copy_diagnostics()

    assert "no log was opened" in QApplication.clipboard().text()
    window.close()
    app.processEvents()


def test_diagnostics_say_how_big_the_window_is(tmp_path: Path) -> None:
    """The one thing a report of cut-off text could not say.

    A user reported that the image filter and the well box were cut off.
    Finding the screen size then took a hunt through Xorg logs on the
    beamline server, which gave the console's mode - not the size of the
    window being looked at, and those differ whenever somebody is connected
    remotely. Every label here is laid out in pixels, so this is the first
    number anyone needs and it was the one number missing.

    Font and DPI are in the line too: the same label needs different widths
    in different fonts, which is why a measurement taken on a Mac does not
    settle what happens on CentOS.
    """
    from dataclasses import replace

    from PyQt5.QtWidgets import QApplication

    from xtalflow.infrastructure import RockMakerImageRepository, SQLiteReviewStore
    from xtalflow.settings import DEFAULT_SETTINGS
    from xtalflow.viewer import ViewerWindow

    app = QApplication.instance() or QApplication([])
    operations_log.configure(tmp_path)
    window = ViewerWindow(
        RockMakerImageRepository(tmp_path),
        SQLiteReviewStore(tmp_path / "reviews.sqlite3"),
        settings=replace(DEFAULT_SETTINGS, fragment_library_directory=tmp_path),
    )
    window.resize(1366, 800)
    window.show()
    app.processEvents()

    window.copy_diagnostics()
    copied = QApplication.clipboard().text()

    assert "window 1366x800" in copied
    assert "windowed" in copied
    for expected in ("screen ", "dpi", "ratio ", "font "):
        assert expected in copied, expected

    # Nothing about the person in this line: a diagnostic is pasted into a
    # message to somebody else, so what is added here is the shape of the
    # machine and not who is sitting at it. The log's own path is a separate
    # matter and has always been in here, deliberately.
    size_line = next(line for line in copied.splitlines() if line.startswith("window "))
    assert getpass.getuser() not in size_line

    window.close()
    app.processEvents()
