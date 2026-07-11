"""Dialog to add or edit a single ComboStep."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
)

from models import ComboStep

# The common GOAL_TYPE values seen in real behavior files (CLAUDE.md).
GOAL_TYPES = [
    "ComboFinal",
    "ComboRepeat",
    "ComboAttackTunableSpin",
    "AttackTunableSpin",
    "AttackImmediateAction",
    "EndureAttack",
]


class StepDialog(QDialog):
    """Collect the fields of one ComboStep. `distance` and `extra_args` are
    free text because real values are often expressions (e.g.
    "3.5 - arg0:GetMapHitRadius(TARGET_SELF)") or comma lists like "0, 0".
    """

    def __init__(self, parent=None, step: ComboStep | None = None,
                 default_goal_type: str = "ComboRepeat"):
        super().__init__(parent)
        self.setWindowTitle("Edit step" if step else "Add step")

        self.goal_type = QComboBox()
        self.goal_type.setEditable(True)   # allow rare types not in the list
        self.goal_type.addItems(GOAL_TYPES)
        self.goal_type.setCurrentText(default_goal_type)   # for a new step

        self.anim_id = QSpinBox()
        self.anim_id.setRange(0, 9_999_999)
        self.priority = QSpinBox()
        self.priority.setRange(0, 999)
        self.priority.setValue(10)
        self.distance = QLineEdit("9999")
        self.target = QLineEdit("TARGET_ENE_0")
        self.extra_args = QLineEdit("0, 0")

        if step is not None:
            self.goal_type.setCurrentText(step.goal_type)
            self.anim_id.setValue(int(step.anim_id) if str(step.anim_id).isdigit() else 0)
            self.priority.setValue(int(step.priority) if str(step.priority).isdigit() else 10)
            self.distance.setText(str(step.distance))
            self.target.setText(step.target)
            self.extra_args.setText(", ".join(str(a) for a in step.extra_args))

        form = QFormLayout(self)
        form.addRow("Goal type", self.goal_type)
        form.addRow("Anim ID", self.anim_id)
        form.addRow("Priority", self.priority)
        form.addRow("Distance (arg5)", self.distance)
        form.addRow("Target", self.target)
        form.addRow("Extra args", self.extra_args)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _parse_value(text: str):
        """Int if it looks like one, otherwise the raw string (expression)."""
        text = text.strip()
        try:
            return int(text)
        except ValueError:
            return text

    def result_step(self) -> ComboStep:
        extra = [self._parse_value(p) for p in self.extra_args.text().split(",")
                 if p.strip()]
        return ComboStep(
            goal_type=self.goal_type.currentText().strip(),
            anim_id=self.anim_id.value(),
            priority=self.priority.value(),
            distance=self._parse_value(self.distance.text()),
            target=self.target.text().strip() or "TARGET_ENE_0",
            extra_args=extra,
        )
