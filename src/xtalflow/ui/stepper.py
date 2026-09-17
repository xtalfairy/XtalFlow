"""Clickable step indicator for a guided experiment."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QWidget

from xtalflow.application.experiment_workflow import (
    STEP_LABELS,
    StepState,
    StepStatus,
    WorkflowStep,
)
from xtalflow.ui import theme

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
            button.setToolTip(status.message)
            button.setAccessibleName(f"{STEP_LABELS[status.step]}: {status.message}")
            is_current = status.step is current
            button.setChecked(is_current)
            # Only a real problem is coloured; done and not-yet steps stay quiet.
            color = {
                StepState.COMPLETE: theme.TEXT,
                StepState.ATTENTION: theme.ATTENTION,
                StepState.INCOMPLETE: theme.TEXT_MUTED,
            }[status.state]
            button.setStyleSheet(
                f"QPushButton {{ border: none; border-bottom: 2px solid "
                f"{theme.FOCUS if is_current else 'transparent'}; padding: 6px 10px 4px; "
                f"color: {theme.FOCUS if is_current else color}; "
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
