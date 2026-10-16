"""Clickable step indicator for a guided experiment."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QWidget

from xtalflow.application.experiment_workflow import (
    STEP_LABELS,
    StepState,
    StepStatus,
    WorkflowStep,
)
from xtalflow.ui import theme

# Left and right padding of a step button, as its style sheet below sets it.
_BUTTON_PADDING = 10

STATE_SYMBOLS = {
    StepState.COMPLETE: theme.SYMBOL_OK,
    StepState.ATTENTION: theme.SYMBOL_ATTENTION,
    StepState.INCOMPLETE: "",
}


class Stepper(QWidget):
    step_selected = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.SPACING_S)
        self.setLayout(self._layout)
        self.buttons: dict[WorkflowStep, QPushButton] = {}
        bold = self.font()
        bold.setBold(True)
        self._bold_metrics = QFontMetrics(bold)

    def show_steps(
        self, statuses: tuple[StepStatus, ...], current: WorkflowStep, compact: bool = False
    ) -> None:
        if tuple(self.buttons) != tuple(status.step for status in statuses):
            self._rebuild(tuple(status.step for status in statuses))
        for status in statuses:
            button = self.buttons[status.step]
            label = STEP_LABELS[status.step]
            if compact:
                label = label.split()[-1].capitalize()
            symbol = STATE_SYMBOLS[status.state]
            button.setText(f"{label} {symbol}" if symbol else label)
            # Room for the widest symbol in the bold the current step is drawn in,
            # so the steps do not shift as one is marked or becomes current.
            widest = max(
                self._bold_metrics.horizontalAdvance(f"{label} {item}")
                for item in STATE_SYMBOLS.values()
            )
            button.setFixedWidth(widest + 2 * _BUTTON_PADDING)
            button.setToolTip(status.message)
            button.setAccessibleName(f"{STEP_LABELS[status.step]}: {status.message}")
            is_current = status.step is current
            button.setChecked(is_current)
            # Only a real problem is coloured; done and not-yet steps stay quiet.
            color = {
                StepState.COMPLETE: theme.TEXT_MUTED,
                StepState.ATTENTION: theme.ATTENTION,
                StepState.INCOMPLETE: theme.TEXT_MUTED,
            }[status.state]
            button.setStyleSheet(
                f"QPushButton {{ border: none; border-radius: 0; border-bottom: 2px solid "
                f"{theme.TEXT if is_current else 'transparent'}; padding: 6px 10px 4px; "
                f"color: {theme.TEXT if is_current else color}; "
                f"font-weight: {'600' if is_current else '400'}; background: transparent; }}"
            )

    def _rebuild(self, steps: tuple[WorkflowStep, ...]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                # Hide now: deleteLater leaves the old step visible until the event loop runs.
                item.widget().hide()
                item.widget().deleteLater()
        self.buttons = {}
        for index, step in enumerate(steps):
            button = QPushButton(STEP_LABELS[step])
            button.setCheckable(True)
            button.setFlat(True)
            button.clicked.connect(lambda _=False, selected=step: self.step_selected.emit(selected))
            self._layout.addWidget(button)
            self.buttons[step] = button
        self._layout.addStretch()
