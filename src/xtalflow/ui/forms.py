"""Label-and-field forms that stay where they are put.

macOS centres a form in the space it is given, so a message appearing beside a
field changes the form's width and shifts every row sideways while it is being
typed. Forms here start at the left edge instead, and keep their fields' own
growth, so a field is as wide as it was laid out to be.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFormLayout


def form_layout() -> QFormLayout:
    form = QFormLayout()
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setLabelAlignment(Qt.AlignLeft)
    return form
