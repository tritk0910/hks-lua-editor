"""Loading, writing and removing content in the target .lua file, plus opening
it in an external editor and raw text search.

Mixed into MainWindow; uses `loaded_path`, `_loaded_text`, `combos`, `seq`.
"""

from __future__ import annotations

import copy
import os

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

import generator
import writer
from models import ComboSequence
from ui.document import Document
from parser import parse_file


class FileOpsMixin:
    # --- find raw text in the loaded file ---------------------------------

    def _find_in_file(self):
        if not self._loaded_text:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("Load a .lua file first.")
            return
        query, ok = QInputDialog.getText(self, "Find in file", "Text to find:")
        if not ok or not query.strip():
            return
        q = query.strip()
        hits = [(n, ln) for n, ln in enumerate(self._loaded_text.splitlines(), 1)
                if q in ln]
        if not hits:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText(f"'{q}' not found in file.")
            return
        labels = [f"{n}: {ln.strip()[:90]}" for n, ln in hits]
        choice, ok = QInputDialog.getItem(
            self, "Find in file", f"{len(hits)} matches — open at line:", labels, 0, False)
        if not ok:
            return
        self._open_at_line(self.loaded_path, hits[labels.index(choice)][0])

    def _open_at_line(self, path, line):
        import shutil
        import subprocess
        code = shutil.which("code") or shutil.which("code.cmd")
        try:
            if code:
                subprocess.Popen([code, "-g", f"{path}:{line}"])
                self.status.setStyleSheet("color: #27ae60;")
                self.status.setText(f"Opened {os.path.basename(path)} at line {line}.")
            else:
                os.startfile(path)
                self.status.setStyleSheet("color: #27ae60;")
                self.status.setText(f"Opened file (line {line} — install 'code' CLI to jump).")
        except Exception as exc:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText(f"Could not open: {exc}")

    # --- load from file ----------------------------------------------------

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open behavior .lua", "", "Lua files (*.lua);;All files (*)")
        if path:
            self._load_path(path)

    def _load_path(self, path: str) -> bool:
        """Open `path` in its own tab. Returns True on success; leaves state
        unchanged (and warns) if nothing parsed or the read fails."""
        already = self._document_for(path)
        if already is not None:      # same file twice -> just go to its tab
            self.doc = already
            self._refresh_file_tabs()
            self._show_document(already)
            return True
        doc = self._read_document(path, warn=True)
        if doc is None:
            return False
        # its own tab: combos must never mix with another file's, or writing one
        # would splice it into the other file
        self._open_document(doc)
        self._add_recent(path)
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Loaded {len(doc.combos)} items "
                            f"({len(doc.warnings)} parse warnings).")
        return True

    def _read_document(self, path: str, warn: bool = False):
        """Read+parse `path` into a fresh Document (uid-tagged, snapshotted for
        Revert). None on read/parse failure — `warn` pops a dialog when so.
        Shared by open and hot-reload."""
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as exc:
            if warn:
                QMessageBox.warning(self, "Cannot open", f"Could not read the file:\n{exc}")
            return None
        result = parse_file(text)
        items = list(result.sequences) + list(result.activators)
        if not items:
            if warn:
                QMessageBox.warning(self, "Nothing parsed",
                                    "No combos or kengeki selector were found.")
            return None
        for it in items:                          # uid + original snapshot for revert
            self._tag(it)
            self._originals[it._uid] = copy.deepcopy(it)
        return Document(path=path, text=text, warnings=list(result.warnings),
                        combos=items, current=items[0])

    def _open_in_editor(self):
        """Open the loaded .lua with the Windows default app (e.g. VSCode)."""
        if not self.loaded_path or not os.path.exists(self.loaded_path):
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("No loaded .lua file to open — Load one first.")
            return
        try:
            os.startfile(self.loaded_path)  # Windows default-app launch
        except Exception as exc:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText(f"Could not open file: {exc}")

    def _write_to_file(self):
        # pick target: default the last-loaded file, else prompt
        path = self.loaded_path
        if not path or not os.path.exists(path):
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose .lua file to write into", "",
                "Lua files (*.lua);;All files (*)")
        if not path:
            return
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        # For a NEW Act/Kengeki, offer a cooldown (SetCoolTime) alongside the
        # REGIST_FUNC line. Cancel = register without a cooldown.
        cooldown = None
        target = "TARGET_SELF"
        if (isinstance(self.seq, ComboSequence)
                and self.seq.trigger_type in ("act_entry", "kengeki_move")):
            fam = "Act" if self.seq.trigger_type == "act_entry" else "Kengeki"
            name = f"{fam}{self.seq.trigger_id:02d}"
            if not writer.function_exists(text, name):
                val, ok = QInputDialog.getInt(
                    self, "Cooldown", "Cooldown seconds (Cancel = none):",
                    15, 0, 999)
                cooldown = val if ok else None
        elif (isinstance(self.seq, ComboSequence)
                and self.seq.trigger_type == "special_effect"
                and generator.needs_registration(self.seq.trigger_id, "TARGET_SELF", text)
                and generator.needs_registration(self.seq.trigger_id, "TARGET_ENE_0", text)):
            # only ask when the effect isn't registered on either target yet
            choice, ok = QInputDialog.getItem(
                self, "Observe target",
                "Register the special effect on which target?",
                ["TARGET_SELF", "TARGET_ENE_0"], 0, False)
            if not ok:
                return
            target = choice
        new_text, summary = writer.apply_sequence(text, self.seq, cooldown, target)
        if new_text == text:
            QMessageBox.information(self, "Nothing written", "\n".join(summary)
                                    or "No change was produced.")
            return
        prompt = (f"Target:\n{path}\n\nChanges:\n  - " + "\n  - ".join(summary)
                  + "\n\nA backup (.bak) will be made. Proceed?")
        lossy = self._lossy_warnings()
        if lossy:
            # this combo is rebuilt from the model, so anything the parser
            # couldn't read is simply not in the output
            prompt = (f"Target:\n{path}\n\nThis combo has {len(lossy)} line(s) "
                      "the tool could not read. Rewriting it will DROP them:\n  - "
                      + "\n  - ".join(str(w) for w in lossy[:8])
                      + ("\n  - …" if len(lossy) > 8 else "")
                      + "\n\nChanges:\n  - " + "\n  - ".join(summary)
                      + "\n\nA backup (.bak) will be made. Proceed anyway?")
        ok = QMessageBox.question(self, "Write to file", prompt,
                                  QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        backup = writer.write_file(path, new_text, backup=True)
        self._note_written(path, new_text)
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Wrote {os.path.basename(path)}"
                            + (f" (backup: {os.path.basename(backup)})" if backup else ""))

    def _note_written(self, path: str, new_text: str):
        """After writing `new_text` to `path`, make it the current file so
        Find-in-file / conflict checks see the fresh content (not the stale
        load-time snapshot), and record it in Open Recent."""
        self.loaded_path = path
        self._loaded_text = new_text
        self._add_recent(path)

    def _lossy_warnings(self) -> list:
        """The current combo's warnings whose source text isn't in the model.

        Only for combos: a selector is spliced line by line, so nothing it
        couldn't read is at risk.
        """
        if not isinstance(self.seq, ComboSequence):
            return []
        return [w for w in getattr(self.seq, "warnings", []) or [] if w.lossy]

    def _target_text(self):
        """Return (path, text) for the write target (loaded file, else prompt),
        or (None, None) if the user cancels."""
        path = self.loaded_path
        if not path or not os.path.exists(path):
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose .lua file", "", "Lua files (*.lua);;All files (*)")
        if not path:
            return None, None
        with open(path, encoding="utf-8", errors="ignore") as f:
            return path, f.read()

    def _commit_removal(self, path, text, new_text, summary) -> bool:
        """Confirm + back up + write a removal; update status. Returns True if
        the file was actually written."""
        if new_text == text or not summary:
            QMessageBox.information(self, "Nothing removed",
                                    "No matching lines were found.")
            return False
        ok = QMessageBox.question(
            self, "Remove from file",
            f"Target:\n{path}\n\nWill remove:\n  - " + "\n  - ".join(summary)
            + "\n\nA backup (.bak) will be made. Proceed?",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return False
        backup = writer.write_file(path, new_text, backup=True)
        self._note_written(path, new_text)
        self.status.setStyleSheet("color: #e67e22;")
        self.status.setText(f"Removed from {os.path.basename(path)}"
                            + (f" (backup: {os.path.basename(backup)})" if backup else ""))
        return True

    def _remove_from_file(self):
        """Delete the current combo from the target file: an Act/Kengeki (+ its
        REGIST/cooldown) or an interrupt branch (+ its special-effect
        registration on both targets)."""
        if not isinstance(self.seq, ComboSequence):
            QMessageBox.information(self, "Remove from file",
                                    "Select an Act, Kengeki, or interrupt combo first.")
            return
        path, text = self._target_text()
        if path is None:
            return
        if self.seq.trigger_type == "act_entry":
            new_text, summary = writer.remove_function(text, f"Act{self.seq.trigger_id:02d}")
        elif self.seq.trigger_type == "kengeki_move":
            new_text, summary = writer.remove_function(text, f"Kengeki{self.seq.trigger_id:02d}")
        elif self.seq.trigger_type == "special_effect":
            new_text, summary = writer.remove_interrupt_branch(text, self.seq.trigger_id)
            new_text, reg = writer.remove_registration(new_text, self.seq.trigger_id)
            summary = summary + reg
        else:
            QMessageBox.information(self, "Remove from file",
                                    f"Cannot remove trigger type {self.seq.trigger_type}.")
            return
        if self._commit_removal(path, text, new_text, summary):
            self._drop_current_combo()   # also drop it from the dropdown

    def _remove_speffect_from_file(self):
        """Delete a special-effect registration (both TARGET_SELF and
        TARGET_ENE_0) by id from the target file."""
        eid, ok = QInputDialog.getInt(self, "Remove special effect",
                                      "Special-effect id to unregister:", 0, 0, 99999999)
        if not ok:
            return
        path, text = self._target_text()
        if path is None:
            return
        new_text, summary = writer.remove_registration(text, eid)
        if not summary:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText(f"{eid} is not registered")
            return
        self._commit_removal(path, text, new_text, summary)
