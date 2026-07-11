"""Main window: build combos (with nested branches) in a tree, switch between
several combos held in memory, see the generated Lua + diagram live, copy it,
or load combos from an existing .lua file.

Kept functional-not-fancy. All combo logic is delegated to the core modules;
this file only wires widgets to the model.
"""

from __future__ import annotations

import copy
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
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
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
import dsas
import generator
import visualizer
import writer
from models import (
    BoolNode, Branch, ComboSequence, ComboStep, KengekiActivator, unchain_branch,
)
from parser import parse_file
from visualizer import condition_text
from ui.branch_dialog import BranchDialog
from ui.lua_highlighter import LuaHighlighter
from ui.step_dialog import StepDialog
from ui.tree_delegate import StepDelegate


def _parse_val(text: str):
    """Int if the text looks like one, else the trimmed string (an expression)."""
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return text

TRIGGER_TYPES = ["act_entry", "special_effect", "kengeki_move"]


def _index_of(lst, obj) -> int:
    """Index of `obj` in `lst` by IDENTITY, not equality. Dataclass instances
    compare equal by value, so a duplicated step/branch would make list.index()
    return the wrong (first-equal) position — this finds the actual object."""
    for i, x in enumerate(lst):
        if x is obj:
            return i
    return -1


def _combo_label(item) -> str:
    if isinstance(item, KengekiActivator):
        return f"Kengeki_Activate ({len(item.blocks)} blocks)"
    kinds = {"act_entry": "Act", "special_effect": "Interrupt",
             "kengeki_move": "Kengeki"}
    kind = kinds.get(item.trigger_type, item.trigger_type)
    return f"{kind} {item.trigger_id} — {item.name}"


class MainWindow(QWidget):
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
        load_btn = QPushButton("Load .lua…")
        load_btn.clicked.connect(self._load_file)
        write_btn = QPushButton("Write to file…")
        write_btn.clicked.connect(self._write_to_file)
        open_btn = QPushButton("Open in editor")
        open_btn.clicked.connect(self._open_in_editor)
        import_btn = QPushButton("Import DSAS…")
        import_btn.clicked.connect(self._import_dsas)
        export_btn = QPushButton("Export DSAS…")
        export_btn.clicked.connect(self._export_dsas)
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Combo:"))
        sel_row.addWidget(self.combo_selector, 1)
        sel_row.addWidget(new_btn)
        sel_row.addWidget(load_btn)
        sel_row.addWidget(write_btn)
        sel_row.addWidget(open_btn)
        sel_row.addWidget(import_btn)
        sel_row.addWidget(export_btn)

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
        edit_menu.addAction(act("Add step", self._add_step))
        edit_menu.addAction(act("Add branch", self._add_branch))
        edit_menu.addAction(act("Add elseif", self._add_elseif))

        help_menu = bar.addMenu("Help")
        help_menu.addAction(act("About", self._about))
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

    def _new_combo(self):
        seq = self._tag(ComboSequence(name=f"combo{len(self.combos) + 1}",
                                      trigger_type="act_entry", trigger_id=0))
        self.combos.append(seq)
        self.seq = seq
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()

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

    # --- model <-> form sync ----------------------------------------------

    def _is_combo(self) -> bool:
        return isinstance(self.seq, ComboSequence)

    def _sync_form_from_seq(self):
        editable = self._is_combo()
        for w in (self.name_edit, self.trigger_type, self.trigger_id):
            w.setEnabled(editable)
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

    # --- tree selection helpers -------------------------------------------

    def _payload_of(self, item):
        # Item data goes through QVariant, which COPIES Python containers, so
        # we store only an int token and keep the live payload (with real list
        # references) in self._payloads.
        if item is None:
            return None
        token = item.data(0, Qt.UserRole)
        if token is None:
            return None
        return self._payloads[token]

    def _selected_payload(self):
        # currentItem() is the single highlighted row and tracks user clicks;
        # prefer it over selectedItems() (which also stays reliable headless).
        item = self.tree.currentItem()
        if item is None:
            items = self.tree.selectedItems()
            item = items[0] if items else None
        return self._payload_of(item)

    def _target_list_and_index(self):
        """Where an Add should place a new item: (list, index).

        Ladder semantics:
          - a branch arm (if/elseif) selected -> add into its body (true_branch)
          - an 'else' node selected           -> add into the else body
          - a step selected                   -> add as a sibling after it
          - nothing selected                  -> append at top level
        """
        data = self._selected_payload()
        if data is None:
            return self.seq.steps, len(self.seq.steps)
        if data["kind"] == "branch":
            body = data["obj"].true_branch
            return body, len(body)
        if data["kind"] == "else":
            return data["list"], len(data["list"])
        lst = data["list"]
        return lst, _index_of(lst, data["obj"]) + 1

    def _selected_obj_data(self):
        """Payload for a selected step/branch (an editable object), or None."""
        data = self._selected_payload()
        if data is None or data["kind"] == "else":
            return None
        return data

    # --- tree operations ---------------------------------------------------

    def _add_step(self):
        if not self._is_combo():
            return
        # an empty Act's first step is the spin opener; otherwise ComboRepeat
        default_gt = ("ComboAttackTunableSpin"
                      if self.seq.trigger_type == "act_entry" and not self._has_spin(self.seq.steps)
                      else "ComboRepeat")
        dlg = StepDialog(self, default_goal_type=default_gt)
        if dlg.exec():
            self._push_undo()
            lst, idx = self._target_list_and_index()
            obj = dlg.result_step()
            lst.insert(idx, obj)
            self.refresh(select=obj)

    def _add_branch(self):
        if not self._is_combo():
            return
        dlg = BranchDialog(self)
        if dlg.exec():
            self._push_undo()
            lst, idx = self._target_list_and_index()
            obj = dlg.result_branch()   # nests inside the target (child of else too)
            lst.insert(idx, obj)        # use "Add elseif" for a same-level arm
            self.refresh(select=obj)

    def _add_elseif(self):
        """Add an `elseif` arm at the SAME level as the selected branch (vs
        Add branch, which nests a child inside the branch)."""
        if not self._is_combo():
            return
        data = self._selected_payload()
        if data is None or data["kind"] not in ("branch", "else"):
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("Select an if/elseif arm (or its else) to add an elseif.")
            return
        cur = data["obj"]   # head arm (for else, the owning branch)
        while (len(cur.false_branch) == 1 and isinstance(cur.false_branch[0], Branch)
               and cur.false_branch[0].from_elseif):
            cur = cur.false_branch[0]
        if cur.false_branch:
            QMessageBox.information(self, "Can't add elseif",
                                    "This branch already has an else body — an elseif "
                                    "can't come after else.")
            return
        dlg = BranchDialog(self)
        if dlg.exec():
            self._push_undo()
            new = dlg.result_branch()
            new.from_elseif = True
            cur.false_branch.append(new)
            self.refresh(select=new)

    # --- clipboard ---------------------------------------------------------

    def _copy_selected(self):
        data = self._selected_obj_data()
        if data is not None:
            self._clipboard = copy.deepcopy(data["obj"])
            self.status.setStyleSheet("color: #27ae60;")
            self.status.setText(f"Copied {type(data['obj']).__name__}.")

    def _paste(self):
        if self._clipboard is None or not self._is_combo():
            return
        self._push_undo()
        lst, idx = self._target_list_and_index()
        clone = copy.deepcopy(self._clipboard)
        lst.insert(idx, clone)
        self.refresh(select=clone)

    def _edit_selected(self):
        data = self._selected_obj_data()
        if data is None:
            return
        obj = data["obj"]
        lst = data["list"]
        if isinstance(obj, ComboStep):
            dlg = StepDialog(self, step=obj)
            maker = dlg.result_step
        else:
            dlg = BranchDialog(self, branch=obj)
            maker = dlg.result_branch
        if dlg.exec():
            self._push_undo()
            new = maker()
            lst[_index_of(lst, obj)] = new
            self.refresh(select=new)

    def _duplicate_selected(self):
        data = self._selected_obj_data()
        if data is None:
            return
        self._push_undo()
        obj = data["obj"]
        lst = data["list"]
        clone = copy.deepcopy(obj)
        lst.insert(_index_of(lst, obj) + 1, clone)
        self.refresh(select=clone)

    def _remove_selected(self):
        data = self._selected_obj_data()
        if data is None:
            return
        self._push_undo()
        lst = data["list"]
        del lst[_index_of(lst, data["obj"])]
        self.refresh()

    def _move_selected(self, delta: int):
        """Move ↑/↓ (Alt+↑/↓). Steps follow the tree's visual order so they nest
        into any body (if/elseif/else); several selected sibling steps move as a
        block; branches use simple same-list logic."""
        if not self._is_combo():
            return
        self._push_undo()
        sel = [d for d in (self._payload_of(it) for it in self.tree.selectedItems())
               if d and d["kind"] == "step"]
        if len(sel) > 1 and len({id(d["list"]) for d in sel}) == 1:
            self._move_block(sel, delta)
            return
        data = self._selected_obj_data()
        if data is None:
            return
        if isinstance(data["obj"], ComboStep):
            self._move_step(data, delta)
        else:
            self._move_branch(data, delta)

    def _move_block(self, sel, delta):
        """Move several selected steps (same list) together as one block."""
        lst = sel[0]["list"]
        objs = [d["obj"] for d in sorted(sel, key=lambda d: _index_of(lst, d["obj"]))]
        idxs = [_index_of(lst, o) for o in objs]
        if delta > 0:                       # DOWN
            after = idxs[-1] + 1
            if after < len(lst):
                nxt = lst[after]
                for o in objs:
                    lst.remove(o)
                if isinstance(nxt, Branch):
                    nxt.true_branch[0:0] = objs          # into the branch body
                else:
                    p = _index_of(lst, nxt) + 1
                    lst[p:p] = objs                      # past the next step
            else:
                if not self._block_pop_out(sel, objs, lst, delta):
                    return
        else:                               # UP
            before = idxs[0] - 1
            if before >= 0:
                prv = lst[before]
                for o in objs:
                    lst.remove(o)
                if isinstance(prv, Branch):
                    prv.true_branch.extend(objs)         # into the branch body end
                else:
                    p = _index_of(lst, prv)
                    lst[p:p] = objs                      # before the previous step
            else:
                if not self._block_pop_out(sel, objs, lst, delta):
                    return
        self.refresh()
        self._select_objs(objs)

    def _block_pop_out(self, sel, objs, lst, delta):
        owner, owner_list = sel[0].get("owner"), sel[0].get("owner_list")
        if owner is None or owner_list is None:
            return False
        for o in objs:
            lst.remove(o)
        oi = _index_of(owner_list, owner) + (1 if delta > 0 else 0)
        owner_list[oi:oi] = objs
        return True

    def _move_branch(self, data, delta):
        lst, obj = data["list"], data["obj"]
        i = _index_of(lst, obj)
        j = i + delta
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]
            self.refresh(select=obj)
            return
        owner, owner_list = data.get("owner"), data.get("owner_list")
        if owner is None or owner_list is None:
            return
        del lst[i]
        oi = _index_of(owner_list, owner)
        owner_list.insert(oi + (1 if delta > 0 else 0), obj)
        self.refresh(select=obj)

    def _pop_out(self, data, delta):
        """Move a step out of its body into the list around the owning branch
        (after it going down, before it going up). Returns True if moved."""
        owner, owner_list = data.get("owner"), data.get("owner_list")
        if owner is None or owner_list is None:
            return False
        data["list"].remove(data["obj"])
        owner_list.insert(_index_of(owner_list, owner) + (1 if delta > 0 else 0), data["obj"])
        return True

    def _move_step(self, data, delta):
        lst, obj = data["list"], data["obj"]
        cur = self.tree.currentItem()
        neighbor_item = (self.tree.itemBelow(cur) if delta > 0
                         else self.tree.itemAbove(cur))
        if neighbor_item is None:
            # last (or first) visible row: DOWN at the very end still pops the
            # step out one level so it can leave the branch (e.g. the else body)
            if delta > 0 and self._pop_out(data, delta):
                self.refresh(select=obj)
            return
        nd = self._payload_of(neighbor_item)
        if nd is None:
            return
        i = _index_of(lst, obj)

        if delta > 0:   # DOWN
            if nd["kind"] in ("branch", "else"):
                body = nd["obj"].true_branch if nd["kind"] == "branch" else nd["list"]
                del lst[i]
                body.insert(0, obj)                       # enter body from top
            elif nd["list"] is lst:
                lst[i], lst[i + 1] = lst[i + 1], lst[i]   # reorder within list
            else:   # neighbor is in an outer list -> pop out before it
                del lst[i]
                nd["list"].insert(_index_of(nd["list"], nd["obj"]), obj)
        else:           # UP
            if nd["kind"] == "step" and nd["list"] is lst:
                lst[i], lst[i - 1] = lst[i - 1], lst[i]   # reorder within list
            elif nd["kind"] == "step":  # neighbor is deeper -> nest at its body end
                nlist = nd["list"]
                del lst[i]
                nlist.insert(_index_of(nlist, nd["obj"]) + 1, obj)
            elif cur.parent() is neighbor_item:
                # obj is the first child of the container above -> move up into
                # the previous visible body (e.g. else's first child goes to the
                # end of the elseif above it), or pop out before the branch if
                # nothing sits above the header (first child of the leading `if`).
                above = self.tree.itemAbove(neighbor_item)
                ad = self._payload_of(above) if above is not None else None
                if ad and ad["kind"] == "step":
                    del lst[i]
                    ad["list"].insert(_index_of(ad["list"], ad["obj"]) + 1, obj)
                elif ad and ad["kind"] in ("branch", "else"):
                    body = ad["list"] if ad["kind"] == "else" else ad["obj"].true_branch
                    del lst[i]
                    body.append(obj)
                else:
                    owner, owner_list = data.get("owner"), data.get("owner_list")
                    if owner is None or owner_list is None:
                        return
                    del lst[i]
                    owner_list.insert(_index_of(owner_list, owner), obj)
            else:
                # a container sits above the whole block obj is under -> enter its
                # last body at the end (fixes: top-level step below a block → else/if)
                body = nd["list"] if nd["kind"] == "else" else nd["obj"].true_branch
                del lst[i]
                body.append(obj)
        self.refresh(select=obj)

    def _select_objs(self, objs):
        matches = []
        for item in self._iter_tree_items():
            d = self._payload_of(item)
            if d and d["kind"] in ("step", "branch") and any(d["obj"] is o for o in objs):
                matches.append(item)
        if not matches:
            return
        # set current FIRST (it resets selection in ExtendedSelection), then
        # re-select the whole block so the highlight stays on the moved items
        self.tree.setCurrentItem(matches[0])
        for it in matches:
            it.setSelected(True)
        self.tree.scrollToItem(matches[0])

    # --- inline editing ----------------------------------------------------

    def _on_item_changed(self, item, column):
        """Commit an inline cell edit back to the ComboStep."""
        if self._building:
            return
        data = self._payload_of(item)
        if not data or data["kind"] != "step":
            return
        self._push_undo()
        step = data["obj"]
        text = item.text(column)
        if column == 0:
            step.goal_type = text.strip()
        elif column == 1:
            step.anim_id = _parse_val(text)
        elif column == 2:
            step.priority = _parse_val(text)
        elif column == 3:
            step.distance = _parse_val(text)
        elif column == 4:
            step.extra_args = [_parse_val(p) for p in text.split(",") if p.strip()]
        self._refresh_output()   # update Lua/diagram; keep tree/edit position

    def _on_double_click(self, item, column):
        # steps edit inline; a branch row opens the full condition dialog
        data = self._payload_of(item)
        if data and data["kind"] == "branch":
            self._edit_selected()

    def _edit_next_step_cell(self):
        """Move to the same column of the next step row and start editing."""
        col = self.tree.currentColumn()
        cur = self.tree.currentItem()
        if cur is None:
            return
        nxt = self.tree.itemBelow(cur)
        while nxt is not None:
            d = self._payload_of(nxt)
            if d and d["kind"] == "step":
                self.tree.setCurrentItem(nxt, col)
                self.tree.editItem(nxt, col)
                return
            nxt = self.tree.itemBelow(nxt)

    # --- load from file ----------------------------------------------------

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open behavior .lua", "", "Lua files (*.lua);;All files (*)")
        if not path:
            return
        with open(path, encoding="utf-8", errors="ignore") as f:
            self._loaded_text = f.read()
        result = parse_file(self._loaded_text)
        items = list(result.sequences) + list(result.activators)
        if not items:
            QMessageBox.warning(self, "Nothing parsed",
                                "No combos or kengeki selector were found.")
            return
        self.loaded_path = path
        for it in items:                          # uid + original snapshot for revert
            self._tag(it)
            self._originals[it._uid] = copy.deepcopy(it)
        # drop untouched placeholder combos (default my_combo / New with no steps)
        self.combos = [c for c in self.combos
                       if isinstance(c, KengekiActivator) or c.steps] + items
        self.seq = items[0]
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Loaded {len(items)} items "
                            f"({len(result.warnings)} parse warnings).")

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

    def _find_speffect_ui(self):
        eid, ok = QInputDialog.getInt(self, "Find by special-effect id",
                                      "Effect id:", 0, 0, 99_999_999)
        if not ok:
            return
        matches = self._find_speffect(eid)
        if not matches:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText(f"No combo uses special effect {eid}.")
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

    def _has_spin(self, items) -> bool:
        """True if any step in the combo (nested included) is a spin opener."""
        for it in items:
            if isinstance(it, ComboStep) and it.goal_type == "ComboAttackTunableSpin":
                return True
            if isinstance(it, Branch):
                if self._has_spin(it.true_branch) or self._has_spin(it.false_branch):
                    return True
        return False

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
        if (isinstance(self.seq, ComboSequence)
                and self.seq.trigger_type in ("act_entry", "kengeki_move")):
            fam = "Act" if self.seq.trigger_type == "act_entry" else "Kengeki"
            name = f"{fam}{self.seq.trigger_id:02d}"
            if not writer.function_exists(text, name):
                val, ok = QInputDialog.getInt(
                    self, "Cooldown", "Cooldown seconds (Cancel = none):",
                    15, 0, 999)
                cooldown = val if ok else None
        new_text, summary = writer.apply_sequence(text, self.seq, cooldown)
        if new_text == text:
            QMessageBox.information(self, "Nothing written", "\n".join(summary)
                                    or "No change was produced.")
            return
        ok = QMessageBox.question(
            self, "Write to file",
            f"Target:\n{path}\n\nChanges:\n  - " + "\n  - ".join(summary)
            + "\n\nA backup (.bak) will be made. Proceed?",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        backup = writer.write_file(path, new_text, backup=True)
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Wrote {os.path.basename(path)}"
                            + (f" (backup: {os.path.basename(backup)})" if backup else ""))

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

    def _commit_removal(self, path, text, new_text, summary):
        """Confirm + back up + write a removal; update status."""
        if new_text == text or not summary:
            QMessageBox.information(self, "Nothing removed",
                                    "No matching lines were found.")
            return
        ok = QMessageBox.question(
            self, "Remove from file",
            f"Target:\n{path}\n\nWill remove:\n  - " + "\n  - ".join(summary)
            + "\n\nA backup (.bak) will be made. Proceed?",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        backup = writer.write_file(path, new_text, backup=True)
        self.status.setStyleSheet("color: #e67e22;")
        self.status.setText(f"Removed from {os.path.basename(path)}"
                            + (f" (backup: {os.path.basename(backup)})" if backup else ""))

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
        self._commit_removal(path, text, new_text, summary)

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

    # --- refresh -----------------------------------------------------------

    def refresh(self, select=None):
        self._rebuild_tree(select)
        self._refresh_output()

    def _rebuild_tree(self, select=None):
        self._building = True
        try:
            self.tree.clear()
            self._payloads = []
            if self._is_combo():
                self._add_tree_items(self.seq.steps, self.tree.invisibleRootItem())
                self.tree.expandAll()
                if select is not None:
                    self._select_obj(select)
        finally:
            self._building = False

    def _store_payload(self, node, payload):
        token = len(self._payloads)
        self._payloads.append(payload)
        node.setData(0, Qt.UserRole, token)

    def _add_tree_items(self, items_list, parent, owner=None, owner_list=None):
        # owner = the branch whose body `items_list` is (None at top level);
        # owner_list = the list that contains `owner`. Used to move items out.
        for obj in items_list:
            if isinstance(obj, ComboStep):
                extra = ", ".join(str(a) for a in obj.extra_args)
                node = QTreeWidgetItem(parent, [obj.goal_type, str(obj.anim_id),
                                                str(obj.priority), str(obj.distance), extra])
                node.setFlags(node.flags() | Qt.ItemIsEditable)  # inline-editable
                self._store_payload(node, {"kind": "step", "obj": obj, "list": items_list,
                                           "owner": owner, "owner_list": owner_list})
            elif isinstance(obj, Branch):
                # ladder: render the if/elseif/else chain at this one level
                arms, else_items = unchain_branch(obj, items_list)
                for k, (arm, containing) in enumerate(arms):
                    kw = "if" if k == 0 else "elseif"
                    node = QTreeWidgetItem(parent, [f"{kw} {condition_text(arm)}"])
                    self._store_payload(node, {"kind": "branch", "obj": arm, "list": containing,
                                               "owner": owner, "owner_list": owner_list})
                    self._add_tree_items(arm.true_branch, node, owner=arm, owner_list=containing)
                # always render an else slot (even empty) so it is reachable:
                # add a step -> else body; add a branch -> becomes an elseif.
                else_node = QTreeWidgetItem(parent, ["else" if else_items else "else (empty)"])
                else_node.setForeground(0, QColor("gray"))
                self._store_payload(else_node, {"kind": "else", "obj": obj, "list": else_items})
                self._add_tree_items(else_items, else_node, owner=obj, owner_list=items_list)

    def _iter_tree_items(self, parent=None):
        parent = parent or self.tree.invisibleRootItem()
        for i in range(parent.childCount()):
            child = parent.child(i)
            yield child
            yield from self._iter_tree_items(child)

    def _select_obj(self, obj):
        for item in self._iter_tree_items():
            data = self._payload_of(item)
            if data and data["kind"] in ("step", "branch") and data["obj"] is obj:
                self.tree.setCurrentItem(item)   # sets current + selects it
                self.tree.scrollToItem(item)
                return

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
