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
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
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
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
import generator
import visualizer
import writer
from models import Branch, ComboSequence, ComboStep, KengekiActivator, unchain_branch
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

        first = ComboSequence(name="my_combo", trigger_type="act_entry",
                              trigger_id=50)
        self.combos = [first]      # every combo/activator held in memory
        self.seq = first           # the one being viewed/edited
        self.loaded_path = None    # last .lua loaded, default write target
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
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Combo:"))
        sel_row.addWidget(self.combo_selector, 1)
        sel_row.addWidget(new_btn)
        sel_row.addWidget(load_btn)
        sel_row.addWidget(write_btn)

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
        self.tree.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

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

        hint = QLabel("Edit Anim/Prio/Dist inline (double-click or type; Enter → next). "
                      "Select a branch → Add step/branch nests inside; Add elseif chains a condition.")
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
        file_menu.addSeparator()
        file_menu.addAction(act("Exit", self.close))

        edit_menu = bar.addMenu("Edit")
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
        seq = ComboSequence(name=f"combo{len(self.combos) + 1}",
                            trigger_type="act_entry", trigger_id=0)
        self.combos.append(seq)
        self.seq = seq
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()

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
        dlg = StepDialog(self)
        if dlg.exec():
            lst, idx = self._target_list_and_index()
            obj = dlg.result_step()
            lst.insert(idx, obj)
            self.refresh(select=obj)

    def _add_branch(self):
        if not self._is_combo():
            return
        dlg = BranchDialog(self)
        if dlg.exec():
            data = self._selected_payload()
            lst, idx = self._target_list_and_index()
            obj = dlg.result_branch()
            # a branch added into an empty else slot becomes an elseif arm
            if data and data["kind"] == "else" and len(lst) == 0:
                obj.from_elseif = True
            lst.insert(idx, obj)
            self.refresh(select=obj)

    def _add_elseif(self):
        """Append an `elseif` arm to the selected branch's ladder."""
        if not self._is_combo():
            return
        data = self._selected_payload()
        if data is None or data["kind"] not in ("branch", "else"):
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("Select an if/elseif arm (or its else) to add an elseif.")
            return
        cur = data["obj"]  # head arm (for else, the owning branch)
        while (len(cur.false_branch) == 1 and isinstance(cur.false_branch[0], Branch)
               and cur.false_branch[0].from_elseif):
            cur = cur.false_branch[0]
        if cur.false_branch:
            QMessageBox.information(self, "Can't add elseif",
                                    "This branch already has an else body — remove it "
                                    "first (an elseif can't come after else).")
            return
        dlg = BranchDialog(self)
        if dlg.exec():
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
            new = maker()
            lst[_index_of(lst, obj)] = new
            self.refresh(select=new)

    def _duplicate_selected(self):
        data = self._selected_obj_data()
        if data is None:
            return
        obj = data["obj"]
        lst = data["list"]
        clone = copy.deepcopy(obj)
        lst.insert(_index_of(lst, obj) + 1, clone)
        self.refresh(select=clone)

    def _remove_selected(self):
        data = self._selected_obj_data()
        if data is None:
            return
        lst = data["list"]
        del lst[_index_of(lst, data["obj"])]
        self.refresh()

    def _move_selected(self, delta: int):
        data = self._selected_obj_data()
        if data is None:
            return
        lst, obj = data["list"], data["obj"]
        i = _index_of(lst, obj)
        j = i + delta
        if not (0 <= j < len(lst)):
            return
        lst[i], lst[j] = lst[j], lst[i]
        self.refresh(select=obj)

    # --- inline editing ----------------------------------------------------

    def _on_item_changed(self, item, column):
        """Commit an inline cell edit back to the ComboStep."""
        if self._building:
            return
        data = self._payload_of(item)
        if not data or data["kind"] != "step":
            return
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
            result = parse_file(f.read())
        items = list(result.sequences) + list(result.activators)
        if not items:
            QMessageBox.warning(self, "Nothing parsed",
                                "No combos or kengeki selector were found.")
            return
        self.loaded_path = path
        self.combos.extend(items)
        self.seq = items[0]
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Loaded {len(items)} items "
                            f"({len(result.warnings)} parse warnings).")

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
        new_text, summary = writer.apply_sequence(text, self.seq)
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

    def _add_tree_items(self, items_list, parent):
        for obj in items_list:
            if isinstance(obj, ComboStep):
                extra = ", ".join(str(a) for a in obj.extra_args)
                node = QTreeWidgetItem(parent, [obj.goal_type, str(obj.anim_id),
                                                str(obj.priority), str(obj.distance), extra])
                node.setFlags(node.flags() | Qt.ItemIsEditable)  # inline-editable
                self._store_payload(node, {"kind": "step", "obj": obj, "list": items_list})
            elif isinstance(obj, Branch):
                # ladder: render the if/elseif/else chain at this one level
                arms, else_items = unchain_branch(obj, items_list)
                for k, (arm, containing) in enumerate(arms):
                    kw = "if" if k == 0 else "elseif"
                    node = QTreeWidgetItem(parent, [f"{kw} {condition_text(arm)}"])
                    self._store_payload(node, {"kind": "branch", "obj": arm, "list": containing})
                    self._add_tree_items(arm.true_branch, node)  # body as children
                # always render an else slot (even empty) so it is reachable:
                # add a step -> else body; add a branch -> becomes an elseif.
                else_node = QTreeWidgetItem(parent, ["else" if else_items else "else (empty)"])
                else_node.setForeground(0, QColor("gray"))
                self._store_payload(else_node, {"kind": "else", "obj": obj, "list": else_items})
                self._add_tree_items(else_items, else_node)

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
                self.tree.setCurrentItem(item)
                self.tree.clearSelection()
                item.setSelected(True)
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
