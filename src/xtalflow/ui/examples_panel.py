"""Window of example images the lab approved, opened from Select wells."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QVBoxLayout, QWidget

from xtalflow.infrastructure.examples import Example
from xtalflow.ui import theme


class ExamplesPanel(QDialog):
    def __init__(
        self, examples: tuple[Example, ...], directory: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Examples")
        self.setModal(False)
        self.resize(900, 600)
        self.examples = examples
        self.example_list = QListWidget()
        self.example_list.setMaximumWidth(260)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(420, 320)
        self.caption_label = QLabel()
        self.caption_label.setWordWrap(True)
        self.empty_label = QLabel(
            "No examples are set up. Put lab-approved images, each with a .txt file of "
            "the same name describing it, in a folder and start XtalFlow with "
            "--examples-dir."
            if directory is None else
            f"No example images were found in {directory}."
        )
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("Muted")

        detail = QVBoxLayout()
        detail.addWidget(self.image_label, 1)
        detail.addWidget(self.caption_label)
        layout = QHBoxLayout()
        layout.setSpacing(theme.SPACING_L)
        if examples:
            for example in examples:
                self.example_list.addItem(example.caption.splitlines()[0])
            layout.addWidget(self.example_list)
            layout.addLayout(detail, 1)
        else:
            layout.addWidget(self.empty_label)
        self.setLayout(layout)
        self.example_list.currentRowChanged.connect(self._show_example)
        if examples:
            self.example_list.setCurrentRow(0)

    def _show_example(self, row: int) -> None:
        if not 0 <= row < len(self.examples):
            return
        example = self.examples[row]
        pixmap = QPixmap(str(example.image_path))
        if pixmap.isNull():
            self.image_label.setText(f"Cannot open {example.image_path.name}")
        else:
            self.image_label.setPixmap(
                pixmap.scaled(640, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self.caption_label.setText(example.caption)
