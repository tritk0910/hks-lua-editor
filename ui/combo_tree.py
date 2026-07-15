"""The combo tree widget, with drag-and-drop that defers to the model.

The tree is a *rendering* of the combo: an if/elseif/else ladder is flattened
into header rows whose children are the bodies, and the rows carry payloads
pointing back at the real lists. Letting Qt move QTreeWidgetItems around would
desync that, so the drop is intercepted, handed to a callback that edits the
model, and the tree is rebuilt from it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget


class ComboTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        #: called as drop_handler(target_item, position) -> bool; set by the window
        self.drop_handler = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)

    def dropEvent(self, event):
        """Translate the drop into a model edit; never let Qt reparent rows."""
        if self.drop_handler is None:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        position = self.dropIndicatorPosition()
        # deliberately NOT calling super(): the model is the source of truth and
        # the view is rebuilt from it right after.
        # The action must not stay MoveAction either — startDrag() runs
        # `if (drag->exec(...) == MoveAction) clearOrRemove()`, which would rip
        # rows out of the tree we just rebuilt.
        event.setDropAction(Qt.IgnoreAction)
        if self.drop_handler(target, position):
            event.accept()
        else:
            event.ignore()
