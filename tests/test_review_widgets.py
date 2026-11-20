"""The widgets of the review window, where their behaviour is not the window's.

One test so far, and it is about a crash rather than a behaviour: it runs in
a subprocess, because what it guards against takes the interpreter down and
no assertion in this process would survive to report it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import xtalflow

_OUTLIVING_TIMER = textwrap.dedent(
    """
    import gc, threading, time
    from PyQt5.QtWidgets import QApplication, QMainWindow
    from xtalflow.ui.review_widgets import StatusMessageLabel

    app = QApplication([])

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            # A reference cycle, which every real window of this program has:
            # it is what makes the window collectable only by the collector,
            # and so collectable at a moment nobody chose.
            self.me = self
            self.label = StatusMessageLabel()
            self.statusBar().addWidget(self.label)

    window = Window()
    window.label.show_message("soon gone", 50)
    window.close()
    del window
    # Collected off the main thread, which is where this program's share and
    # image readers run. PyQt does not delete the C++ window from there: it
    # is left alive with its timer still running and its Python side gone.
    worker = threading.Thread(target=gc.collect)
    worker.start()
    worker.join()
    time.sleep(0.2)
    app.processEvents()
    print("alive")
    """
)


def test_a_message_timer_that_outlives_its_python_side_does_not_crash() -> None:
    """A closed window's timer must not take the program down when it fires.

    The window is closed and dropped, and the collector reaches it on a
    worker thread - a share reader, in the program itself. What is left is a
    C++ window that is still alive, with this label's clear timer still
    running, and no Python object behind it.

    Connecting that timer to a lambda made this fatal. PyQt keeps a lambda
    alive for as long as the sender, so the connection stood while the
    collector cleared the lambda's closure, and the slot then ran with no
    cells to read: a segmentation fault inside processEvents, in a frame
    with no line number because it died before its first instruction. A
    bound method of a QObject is proxied through a weak reference instead,
    so the connection goes when the receiver does.

    It is checked in a subprocess by its exit status, because the failure is
    the interpreter dying rather than anything this process could assert.
    """
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", _OUTLIVING_TIMER],
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": str(Path(xtalflow.__file__).parents[1]),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "alive" in result.stdout
