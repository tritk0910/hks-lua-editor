"""Main window: build combos (with nested branches) in a tree, switch between
several combos held in memory, see the generated Lua + diagram live, copy it,
or load combos from an existing .lua file.

Kept functional-not-fancy. All combo logic is delegated to the core modules;
this file only wires widgets to the model.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from models import Branch, ComboSequence, ComboStep, KengekiActivator, unchain_branch
from parser import parse_file
from visualizer import condition_text
from ui.branch_dialog import BranchDialog
from ui.lua_highlighter import LuaHighlighter
from ui.step_dialog import StepDialog

TRIGGER_TYPES = ["act_entry", "special_effect", "kengeki_move"]


def _step_label(step: ComboStep) -> str:
    txt = f"[{step.anim_id} {step.goal_type}]  prio={step.priority} dist={step.distance}"
    if step.extra_args:
        txt += "  (" + ", ".join(str(a) for a in step.extra_args) + ")"
    return txt


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
        self.setWindowTitle("Sekiro Combo Builder")
        self.resize(1040, 660)

        first = ComboSequence(name="my_combo", trigger_type="act_entry",
                              trigger_id=50)
        self.combos = [first]      # every combo/activator held in memory
        self.seq = first           # the one being viewed/edited
        self._syncing = False

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
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Combo:"))
        sel_row.addWidget(self.combo_selector, 1)
        sel_row.addWidget(new_btn)
        sel_row.addWidget(load_btn)

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

        # tree of steps + branches
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Combo structure"])
        self.tree.setColumnCount(1)
        self.tree.doubleClicked.connect(lambda *_: self._edit_selected())

        add_step_btn = QPushButton("Add step")
        add_branch_btn = QPushButton("Add branch")
        edit_btn = QPushButton("Edit")
        dup_btn = QPushButton("Duplicate")
        rm_btn = QPushButton("Remove")
        up_btn = QPushButton("↑")
        down_btn = QPushButton("↓")
        add_step_btn.clicked.connect(self._add_step)
        add_branch_btn.clicked.connect(self._add_branch)
        edit_btn.clicked.connect(self._edit_selected)
        dup_btn.clicked.connect(self._duplicate_selected)
        rm_btn.clicked.connect(self._remove_selected)
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        down_btn.clicked.connect(lambda: self._move_selected(1))
        btn_row = QHBoxLayout()
        for b in (add_step_btn, add_branch_btn, edit_btn, dup_btn, rm_btn, up_btn, down_btn):
            btn_row.addWidget(b)

        hint = QLabel("Select a branch's true/false node, then Add to nest inside it.")
        hint.setStyleSheet("color: gray;")

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
        splitter.setSizes([470, 570])
        root = QHBoxLayout(self)
        root.addWidget(splitter)

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
            lst, idx = self._target_list_and_index()
            obj = dlg.result_branch()
            lst.insert(idx, obj)
            self.refresh(select=obj)

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
        self.combos.extend(items)
        self.seq = items[0]
        self._refresh_selector()
        self._sync_form_from_seq()
        self.refresh()
        self.status.setStyleSheet("color: #27ae60;")
        self.status.setText(f"Loaded {len(items)} items "
                            f"({len(result.warnings)} parse warnings).")

    # --- refresh -----------------------------------------------------------

    def refresh(self, select=None):
        self._rebuild_tree(select)
        self._refresh_output()

    def _rebuild_tree(self, select=None):
        self.tree.clear()
        self._payloads = []
        if self._is_combo():
            self._add_tree_items(self.seq.steps, self.tree.invisibleRootItem())
            self.tree.expandAll()
            if select is not None:
                self._select_obj(select)

    def _store_payload(self, node, payload):
        token = len(self._payloads)
        self._payloads.append(payload)
        node.setData(0, Qt.UserRole, token)

    def _add_tree_items(self, items_list, parent):
        for obj in items_list:
            if isinstance(obj, ComboStep):
                node = QTreeWidgetItem(parent, [_step_label(obj)])
                self._store_payload(node, {"kind": "step", "obj": obj, "list": items_list})
            elif isinstance(obj, Branch):
                # ladder: render the if/elseif/else chain at this one level
                arms, else_items = unchain_branch(obj, items_list)
                for k, (arm, containing) in enumerate(arms):
                    kw = "if" if k == 0 else "elseif"
                    node = QTreeWidgetItem(parent, [f"{kw} {condition_text(arm)}"])
                    self._store_payload(node, {"kind": "branch", "obj": arm, "list": containing})
                    self._add_tree_items(arm.true_branch, node)  # body as children
                if else_items:
                    else_node = QTreeWidgetItem(parent, ["else"])
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
