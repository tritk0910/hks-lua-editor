"""Dialog to enter the props of a new combo (name + trigger type + trigger id).

Kept UI-only: validation against the loaded .lua is done by the caller so this
dialog stays reusable and decoupled from the file/model state.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
)

TRIGGER_TYPES = ["act_entry", "special_effect", "kengeki_move"]

# what the trigger-id field means for each trigger type
_ID_LABELS = {
    "act_entry": "Act number",
    "special_effect": "Special-effect id",
    "kengeki_move": "Kengeki number",
}


class ComboDialog(QDialog):
    def __init__(self, parent=None, name="", trigger_type="act_entry", trigger_id=0):
        super().__init__(parent)
        self.setWindowTitle("New combo")

        self.name = QLineEdit(name)
        self.trigger_type = QComboBox()
        self.trigger_type.addItems(TRIGGER_TYPES)
        self.trigger_type.setCurrentText(trigger_type)
        self.trigger_id = QSpinBox()
        self.trigger_id.setRange(0, 99_999_999)
        self.trigger_id.setValue(int(trigger_id))

        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Trigger type", self.trigger_type)
        self._id_label = "Trigger id"
        form.addRow(self._id_label, self.trigger_id)
        self._form = form
        self.trigger_type.currentTextChanged.connect(self._update_id_label)
        self._update_id_label(self.trigger_type.currentText())

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _update_id_label(self, trigger_type: str):
        label = self._form.labelForField(self.trigger_id)
        if label is not None:
            label.setText(_ID_LABELS.get(trigger_type, "Trigger id"))

    def result(self) -> tuple[str, str, int]:
        return (self.name.text().strip() or "combo",
                self.trigger_type.currentText(),
                self.trigger_id.value())
