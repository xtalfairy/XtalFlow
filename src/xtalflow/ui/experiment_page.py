"""Frame for one experiment: identity, steps, the current step, and next action."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application.experiment_workflow import StepStatus, WorkflowStep
from xtalflow.ui import theme
from xtalflow.ui.stepper import Stepper


PAGE_STYLE = f"""
QFrame#StepNavigation {{ border: none; border-bottom: 1px solid {theme.BORDER}; }}
QFrame#StepFooter {{ border: none; border-top: 1px solid {theme.BORDER}; }}
QPushButton#HomeLink {{ border: none; color: {theme.FOCUS}; padding: 2px 4px; }}
QPushButton#HomeLink:hover {{ text-decoration: underline; }}
"""


class ExperimentPage(QWidget):
    home_requested = pyqtSignal()
    back_requested = pyqtSignal()
    primary_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(PAGE_STYLE)
        self.home_button = QPushButton("‹ Experiments")
        self.home_button.setObjectName("HomeLink")
        self.title_label = QLabel("Experiment")
        self.title_label.setObjectName("PrimaryHeading")
        self.type_label = QLabel()
        self.type_label.setObjectName("Muted")
        self.lifecycle_label = QLabel()
        self.lifecycle_label.setObjectName("Muted")
        self.stepper = Stepper()
        # One sentence at most; details live next to the controls they explain.
        self.step_hint_label = QLabel()
        self.step_hint_label.setObjectName("Muted")
        self.step_hint_label.hide()
        self.body = QStackedWidget()
        self._step_pages: dict[WorkflowStep, QWidget] = {}
        # Keeps another experiment's step bodies alive while they are not shown.
        self._page_holder = QWidget(self)
        self._page_holder.hide()

        self.footer_status_label = QLabel()
        self.footer_notes_label = QLabel()
        self.footer_notes_label.setObjectName("Muted")
        self.back_button = QPushButton("Back")
        self.primary_button = QPushButton("Continue")
        self.primary_button.setObjectName("Primary")

        header = QHBoxLayout()
        header.setSpacing(theme.SPACING_M)
        header.addWidget(self.home_button)
        header.addWidget(self.title_label)
        header.addWidget(self.type_label)
        header.addStretch()
        header.addWidget(self.lifecycle_label)
        navigation = QHBoxLayout()
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.addWidget(self.stepper)
        navigation.addStretch()
        navigation.addWidget(self.step_hint_label)
        self.navigation = QFrame()
        self.navigation.setObjectName("StepNavigation")
        self.navigation.setLayout(navigation)
        footer_text = QHBoxLayout()
        footer_text.setSpacing(theme.SPACING_L)
        footer_text.addWidget(self.footer_status_label)
        footer_text.addWidget(self.footer_notes_label, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, theme.SPACING_M, 0, theme.SPACING_S)
        footer.addLayout(footer_text, 1)
        footer.addWidget(self.back_button)
        footer.addWidget(self.primary_button)
        self.footer = QFrame()
        self.footer.setObjectName("StepFooter")
        self.footer.setLayout(footer)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACING_M)
        layout.addLayout(header)
        layout.addWidget(self.navigation)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.footer)
        self.setLayout(layout)

        self.home_button.clicked.connect(self.home_requested.emit)
        self.back_button.clicked.connect(self.back_requested.emit)
        self.primary_button.clicked.connect(self.primary_requested.emit)

    def set_pages(self, pages: dict[WorkflowStep, QWidget]) -> None:
        """Show one experiment's step bodies; pages shared between experiments stay."""
        for step, page in tuple(self._step_pages.items()):
            if pages.get(step) is not page:
                self.body.removeWidget(page)
                page.hide()
                page.setParent(self._page_holder)
        self._step_pages = dict(pages)
        for page in pages.values():
            if self.body.indexOf(page) < 0:
                self.body.addWidget(page)

    def page_for(self, step: WorkflowStep) -> QWidget | None:
        return self._step_pages.get(step)

    def show_step(self, step: WorkflowStep, hint: str = "") -> None:
        self.body.setCurrentWidget(self._step_pages[step])
        self.step_hint_label.setText(hint)
        self.step_hint_label.setVisible(bool(hint))

    def show_identity(self, name: str, plan_type_label: str, lifecycle: str) -> None:
        self.title_label.setText(name)
        self.type_label.setText(plan_type_label)
        self.lifecycle_label.setText(lifecycle)

    def show_progress(
        self, statuses: tuple[StepStatus, ...], current: WorkflowStep, compact: bool = False
    ) -> None:
        self.stepper.show_steps(statuses, current, compact)

    def show_footer(
        self,
        status: tuple[str, str],
        notes: str,
        primary_text: str,
        primary_enabled: bool,
        back_enabled: bool,
    ) -> None:
        text, kind = status
        self.footer_status_label.setText(text)
        # Colour and weight are for problems; progress reads as plain text.
        self.footer_status_label.setStyleSheet(
            theme.status_style(kind) if kind in ("attention", "error")
            else f"color: {theme.TEXT_MUTED if kind == 'muted' else theme.TEXT};"
        )
        self.footer_notes_label.setText(notes)
        self.footer_notes_label.setVisible(bool(notes))
        self.primary_button.setText(primary_text)
        self.primary_button.setEnabled(primary_enabled)
        self.back_button.setEnabled(back_enabled)
