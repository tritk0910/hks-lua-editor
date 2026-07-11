"""Inline editing for the combo tree.

Only step rows are editable (branch/else rows are not, enforced by the item's
ItemIsEditable flag). Column 0 of a step edits the goal_type via a combo; the
numeric/text columns use the default line-edit editor. Pressing Enter commits
and jumps down to the same column of the next step — spreadsheet-style rapid
anim-id entry.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from ui.step_dialog import GOAL_TYPES


class StepDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def _is_step_col0(self, index) -> bool:
        item = self._win.tree.itemFromIndex(index)
        data = self._win._payload_of(item)
        return bool(data and data["kind"] == "step" and index.column() == 0)

    def createEditor(self, parent, option, index):
        if self._is_step_col0(index):
            cb = QComboBox(parent)
            cb.setEditable(True)
            cb.addItems(GOAL_TYPES)
            return cb
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            editor.setCurrentText(index.data(Qt.DisplayRole) or "")
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.EditRole)
        else:
            super().setModelData(editor, model, index)

    def eventFilter(self, editor, event):
        # Enter: commit and move down to the same column of the next step
        if (event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            self.commitData.emit(editor)
            self.closeEditor.emit(editor)
            QTimer.singleShot(0, self._win._edit_next_step_cell)
            return True
        return super().eventFilter(editor, event)
