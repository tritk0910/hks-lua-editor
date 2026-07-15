"""Main window: wires the widgets together and owns the combo list, the form,
undo/redo and the generated-Lua/diagram output.

The heavy lifting lives in `ui/mixins/` (tree editing, file I/O, recent files,
DSAS import/export, special-effect search) — this file stays close to layout and
state. All combo logic is delegated to the core modules.
"""

from __future__ import annotations

import copy
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)
import generator
import visualizer
import writer
from models import Branch, ComboSequence, ComboStep, KengekiActivator
from ui.combo_dialog import ComboDialog
from ui.helpers import TRIGGER_TYPES, _combo_label, _index_of
from ui.lua_highlighter import LuaHighlighter
from ui.mixins.dsas_ops import DsasOpsMixin
from ui.mixins.file_ops import FileOpsMixin
from ui.mixins.find_ops import FindOpsMixin
from ui.mixins.recent_files import RecentFilesMixin
from ui.mixins.tree_edit import TreeEditMixin
from ui.tree_delegate import StepDelegate


class MainWindow(TreeEditMixin, FileOpsMixin, RecentFilesMixin, FindOpsMixin,
                 DsasOpsMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HKS Lua Editor")
        self.resize(1040, 660)

        self._next_uid = 0
        first = self._tag(ComboSequence(name="my_combo", trigger_type="act_entry",
                                        trigger_id=50))
        self.combos = [first]      # every combo/activator held in memory
        self.seq = first           # the one being viewed/edited
        self.loaded_path = None    # last .lua loaded, default write target
        self._loaded_text = ""     # raw text of the loaded file (for Find in file)
        self._originals = {}       # uid -> deepcopy snapshot at load (Revert)
        self._history = {}         # uid -> {"undo": [...], "redo": [...]}
        self._syncing = False
        self._building = False     # True while rebuilding the tree (ignore edits)
        self._clipboard = None     # deepcopy of a copied step/branch

        self._build_ui()
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()

    # --- construction ------------------------------------------------------

    def _build_ui(self):
        # combo switcher
        self.combo_selector = QComboBox()
        self.combo_selector.currentIndexChanged.connect(self._on_combo_switched)
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._new_combo)
        del_combo_btn = QPushButton("Delete combo")
        del_combo_btn.clicked.connect(self._delete_combo)
        load_btn = QPushButton("Load .lua…")
        load_btn.clicked.connect(self._load_file)
        close_btn = QPushButton("Close file")
        close_btn.clicked.connect(self._close_file)
        write_btn = QPushButton("Write to file…")
        write_btn.clicked.connect(self._write_to_file)
        open_btn = QPushButton("Open in editor")
        open_btn.clicked.connect(self._open_in_editor)
        import_btn = QPushButton("Import DSAS…")
        import_btn.clicked.connect(self._import_dsas)
        export_btn = QPushButton("Export DSAS…")
        export_btn.clicked.connect(self._export_dsas)
        # row 1: pick/create combos; row 2: file + import/export actions —
        # split so the buttons don't crowd the combo selector off-screen.
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Combo:"))
        sel_row.addWidget(self.combo_selector, 1)
        sel_row.addWidget(new_btn)
        sel_row.addWidget(del_combo_btn)

        file_row = QHBoxLayout()
        for b in (load_btn, close_btn, write_btn, open_btn, import_btn, export_btn):
            file_row.addWidget(b)
        file_row.addStretch(1)

        # metadata form
        self.name_edit = QLineEdit()
        self.trigger_type = QComboBox()
        self.trigger_type.addItems(TRIGGER_TYPES)
        self.trigger_id = QSpinBox()
        self.trigger_id.setRange(0, 99_999_999)
        self.name_edit.textChanged.connect(self._on_form_changed)
        self.trigger_type.currentTextChanged.connect(self._on_form_changed)
        self.trigger_id.valueChanged.connect(self._on_form_changed)
        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Trigger type", self.trigger_type)
        form.addRow("Trigger id (Act# / effect id)", self.trigger_id)

        # tree of steps + branches (multi-column, steps inline-editable)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Structure", "Anim", "Prio", "Dist", "Extra"])
        self.tree.setColumnCount(5)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 220)
        self.tree.setItemDelegate(StepDelegate(self))
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        for seq, d in (("Alt+Up", -1), ("Alt+Down", 1)):
            sc = QShortcut(QKeySequence(seq), self.tree)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=d: self._move_selected(d))

        add_step_btn = QPushButton("Add step")
        add_branch_btn = QPushButton("Add branch")
        add_elseif_btn = QPushButton("Add elseif")
        edit_btn = QPushButton("Edit")
        dup_btn = QPushButton("Duplicate")
        rm_btn = QPushButton("Remove")
        up_btn = QPushButton("↑")
        down_btn = QPushButton("↓")
        add_step_btn.clicked.connect(self._add_step)
        add_branch_btn.clicked.connect(self._add_branch)
        add_elseif_btn.clicked.connect(self._add_elseif)
        edit_btn.clicked.connect(self._edit_selected)
        dup_btn.clicked.connect(self._duplicate_selected)
        rm_btn.clicked.connect(self._remove_selected)
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        down_btn.clicked.connect(lambda: self._move_selected(1))
        btn_row = QHBoxLayout()
        for b in (add_step_btn, add_branch_btn, add_elseif_btn, edit_btn,
                  dup_btn, rm_btn, up_btn, down_btn):
            btn_row.addWidget(b)

        hint = QLabel("Edit Anim/Prio/Dist inline (type or double-click; Enter → next). "
                      "↑/↓ or Alt+↑/↓ reorders and nests across branches (multi-select ok). "
                      "Select a branch → Add branch nests a child; Add elseif adds a same-level arm.")
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)

        left = QVBoxLayout()
        left.addLayout(sel_row)
        left.addLayout(file_row)
        left.addLayout(form)
        left.addWidget(self.tree, 1)
        left.addLayout(btn_row)
        left.addWidget(hint)
        left_widget = QWidget()
        left_widget.setLayout(left)

        # right: output tabs + copy
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.lua_view = QPlainTextEdit(readOnly=True)
        self.lua_view.setFont(mono)
        self._highlighter = LuaHighlighter(self.lua_view.document())
        self.diagram_view = QPlainTextEdit(readOnly=True)
        self.diagram_view.setFont(mono)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.lua_view, "Generated Lua")
        self.tabs.addTab(self.diagram_view, "Diagram")
        copy_btn = QPushButton("Copy current tab")
        copy_btn.clicked.connect(self._copy_current)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #c0392b;")
        right = QVBoxLayout()
        right.addWidget(self.tabs, 1)
        right.addWidget(self.status)
        right.addWidget(copy_btn)
        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([560, 480])
        root = QVBoxLayout(self)
        root.setMenuBar(self._build_menu())
        root.addWidget(splitter)

    def _build_menu(self) -> QMenuBar:
        bar = QMenuBar(self)

        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            self.addAction(a)   # activate the shortcut window-wide
            return a

        file_menu = bar.addMenu("File")
        file_menu.addAction(act("New combo", self._new_combo, "Ctrl+N"))
        file_menu.addAction(act("Load .lua…", self._load_file, "Ctrl+O"))
        self._recent_menu = file_menu.addMenu("Open Recent")
        file_menu.addAction(act("Close file", self._close_file, "Ctrl+W"))
        file_menu.addAction(act("Write to file…", self._write_to_file, "Ctrl+S"))
        file_menu.addAction(act("Remove from file…", self._remove_from_file))
        file_menu.addAction(act("Remove special effect…", self._remove_speffect_from_file))
        file_menu.addAction(act("Open in editor", self._open_in_editor))
        file_menu.addAction(act("Import from DSAS…", self._import_dsas))
        file_menu.addAction(act("Export to DSAS…", self._export_dsas))
        file_menu.addSeparator()
        file_menu.addAction(act("Find special effect…", self._find_speffect_ui, "Ctrl+F"))
        file_menu.addAction(act("Find in file…", self._find_in_file, "Ctrl+Shift+F"))
        file_menu.addSeparator()
        file_menu.addAction(act("Exit", self.close))

        edit_menu = bar.addMenu("Edit")
        edit_menu.addAction(act("Undo", self._undo, "Ctrl+Z"))
        edit_menu.addAction(act("Redo", self._redo, "Ctrl+Y"))
        edit_menu.addAction(act("Revert changes", self._revert_changes))
        edit_menu.addSeparator()
        edit_menu.addAction(act("Copy", self._copy_selected, "Ctrl+C"))
        edit_menu.addAction(act("Paste", self._paste, "Ctrl+V"))
        edit_menu.addAction(act("Duplicate", self._duplicate_selected, "Ctrl+D"))
        edit_menu.addAction(act("Delete", self._remove_selected, "Del"))
        edit_menu.addSeparator()
        edit_menu.addAction(act("Delete combo", self._delete_combo))
        edit_menu.addSeparator()
        edit_menu.addAction(act("Add step", self._add_step))
        edit_menu.addAction(act("Add branch", self._add_branch))
        edit_menu.addAction(act("Add elseif", self._add_elseif))

        help_menu = bar.addMenu("Help")
        help_menu.addAction(act("About", self._about))
        self._rebuild_recent_menu()
        return bar

    def _about(self):
        QMessageBox.about(
            self, "About HKS Lua Editor",
            "HKS Lua Editor\n\nBuild, visualize and generate Sekiro enemy AI "
            "combos (Act / Interrupt / Kengeki) as HKS Lua, and write them back "
            "into a behavior .lua file.")

    # --- combo switching ---------------------------------------------------

    def _refresh_selector(self):
        self._syncing = True
        try:
            self.combo_selector.clear()
            for it in self.combos:
                self.combo_selector.addItem(_combo_label(it))
            self.combo_selector.setCurrentIndex(_index_of(self.combos, self.seq))
        finally:
            self._syncing = False

    def _on_combo_switched(self, idx: int):
        if self._syncing or not (0 <= idx < len(self.combos)):
            return
        self.seq = self.combos[idx]
        self._sync_form_from_seq()
        self.refresh()

    def _current_file_text(self) -> str:
        """Freshest text of the write target for conflict checks: the file on
        disk if we have one, else the snapshot captured at load, else empty."""
        if self.loaded_path and os.path.exists(self.loaded_path):
            with open(self.loaded_path, encoding="utf-8", errors="ignore") as f:
                return f.read()
        return self._loaded_text or ""

    def _combo_conflict(self, trigger_type: str, trigger_id: int) -> str | None:
        """Reason string if a combo with these props already exists (in the
        loaded .lua or among the open combos), else None."""
        # already open in the dropdown?
        for c in self.combos:
            if (isinstance(c, ComboSequence)
                    and c.trigger_type == trigger_type and c.trigger_id == trigger_id):
                return f"A {trigger_type} combo with id {trigger_id} is already open."
        text = self._current_file_text()
        if not text:
            return None
        if trigger_type == "act_entry":
            if writer.function_exists(text, f"Act{trigger_id:02d}"):
                return f"Goal.Act{trigger_id:02d} already exists in the file."
        elif trigger_type == "kengeki_move":
            if writer.function_exists(text, f"Kengeki{trigger_id:02d}"):
                return f"Goal.Kengeki{trigger_id:02d} already exists in the file."
        elif trigger_type == "special_effect":
            registered = (not generator.needs_registration(trigger_id, "TARGET_SELF", text)
                          or not generator.needs_registration(trigger_id, "TARGET_ENE_0", text))
            if registered:
                return f"Special effect {trigger_id} is already registered in the file."
            if writer._existing_branch_span(text, trigger_id) is not None:
                return f"An interrupt branch for {trigger_id} already exists in the file."
        return None

    def _new_combo(self):
        name = f"combo{len(self.combos) + 1}"
        ttype, tid = "act_entry", 0
        while True:
            dlg = ComboDialog(self, name=name, trigger_type=ttype, trigger_id=tid)
            if dlg.exec() != QDialog.Accepted:
                return
            name, ttype, tid = dlg.result()
            reason = self._combo_conflict(ttype, tid)
            if reason is None:
                break
            QMessageBox.warning(self, "Already exists",
                                reason + "\n\nChoose different props.")
        seq = self._tag(ComboSequence(name=name, trigger_type=ttype, trigger_id=tid))
        self.combos.append(seq)
        self.seq = seq
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()

    def _delete_combo(self):
        """Remove the current combo from the dropdown/memory (does NOT touch any
        .lua file — use 'Remove from file…' for that)."""
        if not self._is_combo():
            self.status.setStyleSheet("color: gray;")
            self.status.setText("Select a combo to delete.")
            return
        ok = QMessageBox.question(
            self, "Delete combo",
            f"Remove '{self.seq.name}' from the list?\n"
            "(This only removes it here — the .lua file is not changed.)",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._drop_current_combo()

    def _has_content(self) -> bool:
        """Whether there is anything worth warning about before discarding."""
        return bool(self.loaded_path or len(self.combos) > 1 or self.seq.steps)

    def _confirm_discard(self, verb: str) -> bool:
        """Standard Save / Don't Save / Cancel prompt before losing work.
        Returns True if it's OK to proceed (Save was handled, or Don't Save),
        False if the user cancelled."""
        if not self._has_content():
            return True
        box = QMessageBox(self)
        box.setWindowTitle(verb)
        box.setIcon(QMessageBox.Warning)
        box.setText("You may have unsaved changes.")
        box.setInformativeText(f"Save to the .lua file before {verb.lower()}?")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Save)
        choice = box.exec()
        if choice == QMessageBox.Cancel:
            return False
        if choice == QMessageBox.Save:
            self._write_to_file()      # writes the current combo to its target
        return True

    def closeEvent(self, event):
        # remind to save on app exit, like other editors
        if self._confirm_discard("Exit"):
            event.accept()
        else:
            event.ignore()

    def _close_file(self):
        """Discard the loaded file and all combos, returning to the fresh
        startup state (a single empty default combo, no write target)."""
        if not self._confirm_discard("Close file"):
            return
        first = self._tag(ComboSequence(name="my_combo", trigger_type="act_entry",
                                        trigger_id=50))
        self.combos = [first]
        self.seq = first
        self.loaded_path = None
        self._loaded_text = ""
        self._originals = {}
        self._history = {}
        self._clipboard = None
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()
        self.status.setStyleSheet("color: gray;")
        self.status.setText("Closed file — back to default state")

    def _tag(self, combo):
        """Give a combo a stable uid (survives deepcopy) for history/revert."""
        combo._uid = self._next_uid
        self._next_uid += 1
        return combo

    def _replace_current(self, snap):
        idx = _index_of(self.combos, self.seq)
        if idx < 0:
            return
        self.combos[idx] = snap
        self.seq = snap
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()

    # --- undo / redo (per-combo) ------------------------------------------

    def _push_undo(self):
        if not self._is_combo():
            return
        uid = getattr(self.seq, "_uid", None)
        if uid is None:
            return
        h = self._history.setdefault(uid, {"undo": [], "redo": []})
        h["undo"].append(copy.deepcopy(self.seq))
        if len(h["undo"]) > 50:
            h["undo"].pop(0)
        h["redo"].clear()

    def _undo(self):
        uid = getattr(self.seq, "_uid", None)
        h = self._history.get(uid) if uid is not None else None
        if not h or not h["undo"]:
            return
        h["redo"].append(copy.deepcopy(self.seq))
        self._replace_current(h["undo"].pop())

    def _redo(self):
        uid = getattr(self.seq, "_uid", None)
        h = self._history.get(uid) if uid is not None else None
        if not h or not h["redo"]:
            return
        h["undo"].append(copy.deepcopy(self.seq))
        self._replace_current(h["redo"].pop())

    def _revert_changes(self):
        if not self._is_combo():
            return
        uid = getattr(self.seq, "_uid", None)
        orig = self._originals.get(uid) if uid is not None else None
        if orig is None:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("No saved original for this combo (only loaded ones).")
            return
        if QMessageBox.question(self, "Revert changes",
                                "Discard all edits and restore this combo to its "
                                "loaded state?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._push_undo()
        self._replace_current(copy.deepcopy(orig))
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText("Reverted to loaded state.")


    # --- model <-> form sync ----------------------------------------------

    def _is_combo(self) -> bool:
        return isinstance(self.seq, ComboSequence)

    def _sync_form_from_seq(self):
        editable = self._is_combo()
        # Name stays editable inline; trigger type/id are set once at creation
        # (via the New-combo modal) and shown read-only afterwards.
        self.name_edit.setEnabled(editable)
        self.trigger_type.setEnabled(False)
        self.trigger_id.setReadOnly(True)
        self.trigger_id.setButtonSymbols(QAbstractSpinBox.NoButtons)
        if not editable:
            return
        self._syncing = True
        try:
            self.name_edit.setText(self.seq.name)
            self.trigger_type.setCurrentText(self.seq.trigger_type)
            self.trigger_id.setValue(int(self.seq.trigger_id))
        finally:
            self._syncing = False

    def _on_form_changed(self, *_):
        if self._syncing or not self._is_combo():
            return
        self.seq.name = self.name_edit.text()
        self.seq.trigger_type = self.trigger_type.currentText()
        self.seq.trigger_id = self.trigger_id.value()
        # keep the switcher label in sync without a full rebuild
        idx = _index_of(self.combos, self.seq)
        self.combo_selector.setItemText(idx, _combo_label(self.seq))
        self.refresh()

    def _has_spin(self, items) -> bool:
        """True if any step in the combo (nested included) is a spin opener."""
        for it in items:
            if isinstance(it, ComboStep) and it.goal_type == "ComboAttackTunableSpin":
                return True
            if isinstance(it, Branch):
                if self._has_spin(it.true_branch) or self._has_spin(it.false_branch):
                    return True
        return False


    def _drop_current_combo(self):
        """Remove the current combo from the in-memory list/dropdown, selecting
        a neighbour (or a fresh default combo if none are left)."""
        idx = _index_of(self.combos, self.seq)
        if idx < 0:
            return
        uid = getattr(self.seq, "_uid", None)
        self._originals.pop(uid, None)
        self._history.pop(uid, None)
        del self.combos[idx]
        if not self.combos:
            self.combos = [self._tag(ComboSequence(
                name="my_combo", trigger_type="act_entry", trigger_id=50))]
            self.seq = self.combos[0]
        else:
            self.seq = self.combos[min(idx, len(self.combos) - 1)]
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()


    # --- refresh -----------------------------------------------------------

    def refresh(self, select=None):
        self._rebuild_tree(select)
        self._refresh_output()

    def _refresh_output(self):
        self.status.setText("")
        self.status.setStyleSheet("color: #c0392b;")
        try:
            if isinstance(self.seq, KengekiActivator):
                lua = generator.generate_kengeki_activate(self.seq)
            elif self.seq.trigger_type == "act_entry":
                lua = generator.generate_act(self.seq)
            elif self.seq.trigger_type == "kengeki_move":
                lua = generator.generate_kengeki_move(self.seq)
            else:
                lua = generator.generate_interrupt_branch(self.seq)
                if generator.needs_registration(self.seq.trigger_id, "TARGET_SELF", ""):
                    reg = generator.registration_line(self.seq.trigger_id)
                    lua = f"-- register in Goal.Activate:\n{reg}\n\n{lua}"
        except Exception as exc:  # empty/invalid combo -> show, don't crash
            lua = ""
            self.status.setText(f"Cannot generate Lua: {exc}")
        self.lua_view.setPlainText(lua)
        try:
            if isinstance(self.seq, KengekiActivator):
                diagram = visualizer.visualize_kengeki(self.seq)
            else:
                diagram = visualizer.visualize(self.seq)
            self.diagram_view.setPlainText(diagram)
        except Exception as exc:
            self.diagram_view.setPlainText(f"(diagram error: {exc})")

    def _copy_current(self):
        text = (self.lua_view if self.tabs.currentIndex() == 0
                else self.diagram_view).toPlainText()
        QApplication.clipboard().setText(text)
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText("Copied to clipboard.")
