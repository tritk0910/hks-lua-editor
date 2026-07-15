"""Finding a special effect: first among the combos held in memory, then in the
loaded file's text (which catches ids that only appear as a registration line).

Mixed into MainWindow; uses `combos`, `seq` and `_loaded_text`.
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import QInputDialog

from models import BoolNode, Branch, ComboSequence
from ui.helpers import _combo_label


class FindOpsMixin:
    # --- find by special-effect ------------------------------------------

    def _cond_has_speffect(self, items, eid) -> bool:
        for it in items:
            if isinstance(it, BoolNode):
                if self._cond_has_speffect(it.terms, eid):
                    return True
            elif getattr(it, "kind", None) == "speffect" and it.effect_id == eid:
                return True
        return False

    def _find_speffect(self, eid):
        """Return [(combo, branch_or_None)] where the special-effect id is used
        (interrupt keyed on it, or a HasSpecialEffectId term)."""
        res = []

        def walk(combo, items):
            for it in items:
                if isinstance(it, Branch):
                    if self._cond_has_speffect(it.terms, eid):
                        res.append((combo, it))
                    walk(combo, it.true_branch)
                    walk(combo, it.false_branch)

        for c in self.combos:
            if isinstance(c, ComboSequence):
                if c.trigger_type == "special_effect" and int(c.trigger_id) == eid:
                    res.append((c, None))
                walk(c, c.steps)
        return res

    def _scan_file_speffect(self, eid):
        """[(lineno, line)] in the loaded file where `eid` appears in a
        special-effect context (registration / interrupt key / HasSpecialEffectId)."""
        token = re.compile(rf"\b{eid}\b")
        hits = []
        for n, ln in enumerate(self._loaded_text.splitlines(), 1):
            if token.search(ln) and ("SpecialEffect" in ln
                                     or "interruptEffectIdentifier" in ln):
                hits.append((n, ln))
        return hits

    def _find_speffect_in_file(self, eid) -> bool:
        """Offer to open the file at a line mentioning `eid`. Returns True if a
        hit was found (and handled), False otherwise."""
        hits = self._scan_file_speffect(eid)
        if not hits:
            return False
        if len(hits) == 1:
            self._open_at_line(self.loaded_path, hits[0][0])
            return True
        labels = [f"{n}: {ln.strip()[:90]}" for n, ln in hits]
        choice, ok = QInputDialog.getItem(
            self, "Find special effect",
            f"{len(hits)} lines in the file — open at line:", labels, 0, False)
        if ok:
            self._open_at_line(self.loaded_path, hits[labels.index(choice)][0])
        return True

    def _find_speffect_ui(self):
        eid, ok = QInputDialog.getInt(self, "Find by special-effect id",
                                      "Effect id:", 0, 0, 99_999_999)
        if not ok:
            return
        matches = self._find_speffect(eid)
        if not matches:
            # fall back to the file: catch ids that only live in the .lua (e.g. a
            # registration line) and aren't loaded as a combo in memory.
            if self._find_speffect_in_file(eid):
                return
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText(f"Special effect {eid} not found in combos or file.")
            return
        combo, node = matches[0]
        if len(matches) > 1:
            labels = [f"{i + 1}. {_combo_label(c)}" + (" — in a branch" if n else "")
                      for i, (c, n) in enumerate(matches)]
            choice, ok = QInputDialog.getItem(
                self, "Matches", f"{len(matches)} matches for {eid}:", labels, 0, False)
            if not ok:
                return
            combo, node = matches[labels.index(choice)]
        self.seq = combo
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()
        if node is not None:
            self._select_obj(node)
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Found special effect {eid}.")
