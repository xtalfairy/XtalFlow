"""Create an experiment project from the current selection."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from xtalflow.domain import PlanType
from xtalflow.ui import theme


PLAN_TYPE_CHOICES = (
    (PlanType.FRAGMENT_SCREENING, "Fragment Screening",
     "Assign one library fragment to each selected well (ECHO + SHIFTER)"),
    (PlanType.RAW_CRYSTAL, "Raw Crystal",
     "Harvest selected crystals without soaking (SHIFTER only)"),
)


class NewProjectDialog(QDialog):
    """Name a project and choose its plan type; the selection is fixed on create."""

    def __init__(
        self,
        well_count: int,
        position_count: int,
        default_names: dict[PlanType, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setMinimumWidth(460)
        self._default_names = default_names
        self._name_edited = False
        summary = QLabel(
            f"{well_count} selected well{'s' if well_count != 1 else ''} · "
            f"{position_count} position{'s' if position_count != 1 else ''}"
        )
        summary.setObjectName("PrimaryHeading")
        snapshot_note = QLabel(
            "The project keeps this selection. Later Image Review changes do not "
            "alter it."
        )
        snapshot_note.setObjectName("Muted")
        snapshot_note.setWordWrap(True)
        self.plan_type_group = QButtonGroup(self)
        plan_types = QVBoxLayout()
        for index, (plan_type, label, description) in enumerate(PLAN_TYPE_CHOICES):
            button = QRadioButton(label)
            button.setProperty("plan_type", plan_type.value)
            button.setToolTip(description)
            self.plan_type_group.addButton(button, index)
            hint = QLabel(description)
            hint.setObjectName("Muted")
            hint.setIndent(22)
            plan_types.addWidget(button)
            plan_types.addWidget(hint)
        for label in ("Solvent Duration", "Cryo Plan", "Custom Soaking"):
            later = QRadioButton(f"{label} (coming later)")
            later.setEnabled(False)
            plan_types.addWidget(later)
        self.plan_type_group.button(0).setChecked(True)
        self.name_input = QLineEdit(default_names[PlanType.FRAGMENT_SCREENING])
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        create_button = self.buttons.button(QDialogButtonBox.Ok)
        create_button.setText("Create Project")
        create_button.setObjectName("Primary")

        form = QFormLayout()
        form.addRow("Name", self.name_input)
        layout = QVBoxLayout()
        layout.setSpacing(theme.SPACING_M)
        layout.addWidget(summary)
        layout.addWidget(snapshot_note)
        layout.addWidget(QLabel("Plan type"))
        layout.addLayout(plan_types)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self.plan_type_group.buttonToggled.connect(self._plan_type_changed)
        self.name_input.textEdited.connect(self._mark_name_edited)
        self.name_input.textChanged.connect(
            lambda text: create_button.setEnabled(bool(text.strip()))
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    @property
    def plan_type(self) -> PlanType:
        return PlanType(self.plan_type_group.checkedButton().property("plan_type"))

    @property
    def project_name(self) -> str:
        return self.name_input.text().strip()

    def select_plan_type(self, plan_type: PlanType) -> None:
        for button in self.plan_type_group.buttons():
            if button.property("plan_type") == plan_type.value:
                button.setChecked(True)

    def _mark_name_edited(self) -> None:
        self._name_edited = True

    def _plan_type_changed(self, button, checked: bool) -> None:
        if checked and not self._name_edited:
            self.name_input.setText(self._default_names[self.plan_type])
