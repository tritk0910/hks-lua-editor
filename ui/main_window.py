"""Main window: build a ComboSequence, see its Lua + diagram live, copy it,
or load an existing .lua file to inspect combos already in it.

Kept deliberately functional-not-fancy. All combo logic is delegated to the
core modules; this file only wires widgets to the model.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

import generator
import visualizer
from models import Branch, ComboSequence, ComboStep, KengekiActivator
from parser import parse_file
from ui.step_dialog import StepDialog

TRIGGER_TYPES = ["act_entry", "special_effect", "kengeki_move"]


def _branch_summary(branch: Branch) -> str:
    if branch.kind == "random_percent":
        return f"⟨branch⟩ random {branch.threshold}%"
    if branch.kind == "state_check":
        return f"⟨branch⟩ GetNumber({branch.state_index}) == {branch.state_value}"
    return f"⟨branch⟩ raw: {branch.raw_condition}"


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sekiro Combo Builder")
        self.resize(1000, 640)

        self.seq = ComboSequence(name="my_combo", trigger_type="act_entry",
                                 trigger_id=50)
        self._syncing = False  # guards against signal feedback while we push
                               # model values into the form widgets

        self._build_ui()
        self._sync_form_from_seq()
        self.refresh()

    # --- construction ------------------------------------------------------

    def _build_ui(self):
        # left: metadata + step table + buttons
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
        form.addRow("Trigger id (Act# or effect id)", self.trigger_id)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Goal type", "Anim", "Prio", "Distance", "Extra"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(lambda *_: self._edit_step())

        add_btn = QPushButton("Add step")
        edit_btn = QPushButton("Edit")
        rm_btn = QPushButton("Remove")
        up_btn = QPushButton("↑")
        down_btn = QPushButton("↓")
        add_btn.clicked.connect(self._add_step)
        edit_btn.clicked.connect(self._edit_step)
        rm_btn.clicked.connect(self._remove_step)
        up_btn.clicked.connect(lambda: self._move_step(-1))
        down_btn.clicked.connect(lambda: self._move_step(1))

        btn_row = QHBoxLayout()
        for b in (add_btn, edit_btn, rm_btn, up_btn, down_btn):
            btn_row.addWidget(b)

        load_btn = QPushButton("Load .lua file…")
        load_btn.clicked.connect(self._load_file)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(QLabel("Steps (double-click to edit):"))
        left.addWidget(self.table, 1)
        left.addLayout(btn_row)
        left.addWidget(load_btn)
        left_widget = QWidget()
        left_widget.setLayout(left)

        # right: output tabs + copy
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)

        self.lua_view = QPlainTextEdit()
        self.lua_view.setReadOnly(True)
        self.lua_view.setFont(mono)
        self.diagram_view = QPlainTextEdit()
        self.diagram_view.setReadOnly(True)
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
        splitter.setSizes([440, 560])

        root = QHBoxLayout(self)
        root.addWidget(splitter)

    # --- model <-> form sync ----------------------------------------------

    def _sync_form_from_seq(self):
        editable = isinstance(self.seq, ComboSequence)
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
        if self._syncing or not isinstance(self.seq, ComboSequence):
            return
        self.seq.name = self.name_edit.text()
        self.seq.trigger_type = self.trigger_type.currentText()
        self.seq.trigger_id = self.trigger_id.value()
        self.refresh()

    # --- step editing ------------------------------------------------------

    def _selected_row(self) -> int:
        if not isinstance(self.seq, ComboSequence):
            return -1
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _is_combo(self) -> bool:
        return isinstance(self.seq, ComboSequence)

    def _add_step(self):
        if not self._is_combo():
            return
        dlg = StepDialog(self)
        if dlg.exec():
            self.seq.steps.append(dlg.result_step())
            self.refresh()

    def _edit_step(self):
        row = self._selected_row()
        if row < 0:
            return
        item = self.seq.steps[row]
        if not isinstance(item, ComboStep):
            QMessageBox.information(self, "Not editable",
                                    "Branch editing isn't supported yet — this "
                                    "row came from a loaded file.")
            return
        dlg = StepDialog(self, step=item)
        if dlg.exec():
            self.seq.steps[row] = dlg.result_step()
            self.refresh()

    def _remove_step(self):
        row = self._selected_row()
        if row < 0:
            return
        del self.seq.steps[row]
        self.refresh()

    def _move_step(self, delta: int):
        row = self._selected_row()
        if row < 0:
            return
        new = row + delta
        if not (0 <= new < len(self.seq.steps)):
            return
        steps = self.seq.steps
        steps[row], steps[new] = steps[new], steps[row]
        self.refresh()
        self.table.selectRow(new)

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
        kind_names = {"act_entry": "Act", "special_effect": "Interrupt",
                      "kengeki_move": "Kengeki"}
        labels = []
        for it in items:
            if isinstance(it, KengekiActivator):
                labels.append(f"Kengeki_Activate ({len(it.blocks)} effect blocks)")
            else:
                kind = kind_names.get(it.trigger_type, it.trigger_type)
                labels.append(f"{kind} {it.trigger_id} — {it.name}")
        choice, ok = QInputDialog.getItem(
            self, "Pick a combo",
            f"{len(items)} items parsed ({len(result.warnings)} warnings):",
            labels, 0, False)
        if not ok:
            return
        self.seq = items[labels.index(choice)]
        self._sync_form_from_seq()
        self.refresh()

    # --- refresh -----------------------------------------------------------

    def refresh(self):
        self._refresh_table()
        self._refresh_output()

    def _refresh_table(self):
        if not isinstance(self.seq, ComboSequence):
            self.table.setRowCount(0)
            return
        self.table.setRowCount(len(self.seq.steps))
        for row, item in enumerate(self.seq.steps):
            if isinstance(item, ComboStep):
                values = [item.goal_type, str(item.anim_id), str(item.priority),
                          str(item.distance),
                          ", ".join(str(a) for a in item.extra_args)]
                for col, val in enumerate(values):
                    self.table.setItem(row, col, QTableWidgetItem(val))
            else:  # Branch — read-only summary spanning the row
                cell = QTableWidgetItem(_branch_summary(item))
                cell.setForeground(Qt.gray)
                self.table.setItem(row, 0, cell)
                for col in range(1, 5):
                    self.table.setItem(row, col, QTableWidgetItem(""))

    def _refresh_output(self):
        self.status.setText("")
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
