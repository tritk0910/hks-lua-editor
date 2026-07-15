"""Import from / export to DS Animation Studio combo text.

Mixed into MainWindow; uses `seq`, `status` and the tree's insert target.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QMessageBox,
)

import dsas
from models import Branch, unchain_branch
from visualizer import condition_text


class DsasOpsMixin:
    def _import_dsas(self):
        """Paste DSAS combo-viewer text and append the steps to the current combo."""
        if not self._is_combo():
            QMessageBox.information(self, "Import from DSAS",
                                    "Select an Act/Kengeki combo first (not a kengeki selector).")
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Import from DSAS",
            "Paste combo lines (e.g. 'EnemyComboAtk 3000'):", "")
        if not ok or not text.strip():
            return
        has_spin = self._has_spin(self.seq.steps)
        steps = dsas.parse_dsas_combo(text, first_is_spin=not has_spin)
        if not steps:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("No anim ids found in the pasted text.")
            return
        self._push_undo()
        lst, idx = self._target_list_and_index()   # insert at the selected body
        for k, st in enumerate(steps):
            lst.insert(idx + k, st)
        self.refresh(select=steps[0])
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Imported {len(steps)} steps from DSAS.")

    def _export_dsas(self):
        """Serialise the current combo to DSAS text; pick an arm per branch."""
        if not self._is_combo():
            QMessageBox.information(self, "Export to DSAS",
                                    "Select an Act/Kengeki combo (not a kengeki selector).")
            return
        branches = []
        self._collect_branches(self.seq.steps, branches)
        if not branches:
            self._show_dsas_text(dsas.export_dsas(self.seq.steps))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Export to DSAS — pick a branch path")
        form = QFormLayout(dlg)
        rows = []
        for head, labels in branches:
            cb = QComboBox()
            cb.addItems(labels)
            form.addRow(labels[0][:48], cb)
            rows.append((head, cb, len(labels) - 1))   # last index == "else"
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        choices = {}
        for head, cb, else_idx in rows:
            i = cb.currentIndex()
            choices[id(head)] = "else" if i == else_idx else i
        self._show_dsas_text(dsas.export_dsas(self.seq.steps, choices))

    def _collect_branches(self, items, out):
        for it in items:
            if isinstance(it, Branch):
                arms, else_items = unchain_branch(it, items)
                labels = [f"{'if' if k == 0 else 'elseif'} {condition_text(arm)}"
                          for k, (arm, _l) in enumerate(arms)] + ["else"]
                out.append((it, labels))
                for arm, _l in arms:
                    self._collect_branches(arm.true_branch, out)
                self._collect_branches(else_items, out)

    def _show_dsas_text(self, text):
        if not text.strip():
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("Nothing to export (no steps on the chosen path).")
            return
        QApplication.clipboard().setText(text)
        QInputDialog.getMultiLineText(self, "DSAS export (copied to clipboard)",
                                      "Combo for DS Animation Studio:", text)
