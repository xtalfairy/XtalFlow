"""Keyboard and mouse reference opened with ? or Help ▸ Keyboard Shortcuts."""

from __future__ import annotations

import sys

from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

COMMAND = "Cmd" if sys.platform == "darwin" else "Ctrl"

SHORTCUTS = (
    ("Left click", "Image", "Add a soaking position"),
    ("Right click", "Image", "Remove the nearest position"),
    ("← / →", "Image", "Previous / next image"),
    ("↑ / ↓", "Image or plate list", "Previous / next plate"),
    ("↑ / ↓", "Selected wells table", "Previous / next position and its image"),
    ("Space + drag", "Image", "Pan"),
    ("0", "Image", "Fit image"),
    ("+ / −", "Image", "Zoom in / out"),
    ("Esc", "Setting 3 points", "Cancel well calibration"),
    ("Enter", "Well field", "Go to the entered well"),
    ("Esc", "Well field", "Restore the current well"),
    (f"{COMMAND}+L", "An experiment", "Go to Select wells and the well field"),
    (f"{COMMAND}+Shift+T", "Select wells", "Show or hide the selected wells table"),
    (f"{COMMAND}+Shift+P", "Select wells", "Show or hide the plate list"),
    (f"{COMMAND}+Shift+S", "Anywhere", "Show or hide the workspace panel"),
    ("Delete", "Selected wells table", "Delete the selected positions"),
    ("?", "Outside text fields", "Show this reference"),
)


class ShortcutsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(640, 600)
        table = QTableWidget(len(SHORTCUTS), 3)
        table.setHorizontalHeaderLabels(("Input", "Where", "Action"))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for row, values in enumerate(SHORTCUTS):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(table)
        layout.addWidget(buttons)
        self.setLayout(layout)
