"""Small "?" next to a control: details stay one click away instead of on screen."""

from __future__ import annotations

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QToolButton, QToolTip, QWidget


class HelpButton(QToolButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.help_text = text
        self.setText("?")
        self.setAutoRaise(True)
        self.setToolTip(text)
        self.setAccessibleName("Help")
        self.setAccessibleDescription(text)
        self.setStyleSheet("QToolButton { min-height: 18px; padding: 0 5px; }")
        self.clicked.connect(self.show_help)

    def show_help(self) -> None:
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.help_text, self)
