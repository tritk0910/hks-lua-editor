"""Dialog to add or edit a Branch (a split in the combo)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
)

from models import Branch

KINDS = ["randam_percent", "state_check", "ninsatsu", "raw"]
NINSATSU_OPS = ["<=", ">=", "==", "<", ">"]


class BranchDialog(QDialog):
    """Collect a Branch's kind + parameters. When editing, the existing
    true/false sub-lists are preserved (only the condition is changed)."""

    def __init__(self, parent=None, branch: Branch | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit branch" if branch else "Add branch")
        self._existing = branch

        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.kind.currentTextChanged.connect(self._update_visibility)

        self.threshold = QSpinBox()          # randam percent
        self.threshold.setRange(1, 100)
        self.threshold.setValue(50)
        self.state_index = QSpinBox()
        self.state_index.setRange(0, 999)
        self.state_value = QSpinBox()
        self.state_value.setRange(-999, 999)
        self.ninsatsu_op = QComboBox()
        self.ninsatsu_op.addItems(NINSATSU_OPS)
        self.ninsatsu_value = QSpinBox()     # deathblow count / phase
        self.ninsatsu_value.setRange(0, 20)
        self.ninsatsu_value.setValue(1)
        self.raw_condition = QLineEdit()

        if branch is not None:
            self.kind.setCurrentText(branch.kind)
            if branch.kind == "randam_percent":
                self.threshold.setValue(branch.threshold or 50)
            elif branch.kind == "ninsatsu":
                self.ninsatsu_op.setCurrentText(branch.operator or "<=")
                self.ninsatsu_value.setValue(branch.threshold or 1)
            self.state_index.setValue(branch.state_index or 0)
            self.state_value.setValue(branch.state_value or 0)
            self.raw_condition.setText(branch.raw_condition or "")

        form = QFormLayout(self)
        form.addRow("Kind", self.kind)
        self._rows = {
            "threshold": (QLabel("Randam threshold (<= N%)"), self.threshold),
            "sidx": (QLabel("State index"), self.state_index),
            "sval": (QLabel("State value"), self.state_value),
            "nop": (QLabel("Ninsatsu operator"), self.ninsatsu_op),
            "nval": (QLabel("Ninsatsu value (deathblows/phase)"), self.ninsatsu_value),
            "raw": (QLabel("Raw Lua condition"), self.raw_condition),
        }
        for lbl, widget in self._rows.values():
            form.addRow(lbl, widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._update_visibility(self.kind.currentText())

    def _update_visibility(self, kind: str):
        visible = {
            "randam_percent": {"threshold"},
            "state_check": {"sidx", "sval"},
            "ninsatsu": {"nop", "nval"},
            "raw": {"raw"},
        }.get(kind, set())
        for key, (lbl, widget) in self._rows.items():
            lbl.setVisible(key in visible)
            widget.setVisible(key in visible)

    def result_branch(self) -> Branch:
        kind = self.kind.currentText()
        true_b = self._existing.true_branch if self._existing else []
        false_b = self._existing.false_branch if self._existing else []
        branch = Branch(kind=kind, true_branch=true_b, false_branch=false_b)
        if kind == "randam_percent":
            branch.threshold = self.threshold.value()
        elif kind == "state_check":
            branch.state_index = self.state_index.value()
            branch.state_value = self.state_value.value()
        elif kind == "ninsatsu":
            branch.operator = self.ninsatsu_op.currentText()
            branch.threshold = self.ninsatsu_value.value()
        elif kind == "raw":
            branch.raw_condition = self.raw_condition.text().strip()
        return branch
