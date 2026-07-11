"""Dialog to add or edit a Branch — a condition made of one or more Terms
joined by `and`/`or`."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models import Branch, Term

TERM_KINDS = ["randam", "state", "ninsatsu", "speffect", "raw"]
NINSATSU_OPS = ["<=", ">=", "==", "<", ">"]
TARGETS = ["TARGET_SELF", "TARGET_ENE_0"]


class TermRow(QWidget):
    """One editable condition term: kind + `not` + the fields for that kind."""

    def __init__(self, term: Term | None = None, on_remove=None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)

        self.kind = QComboBox()
        self.kind.addItems(TERM_KINDS)
        self.kind.currentTextChanged.connect(self._update_visibility)
        self.negate = QCheckBox("not")

        self.threshold = QSpinBox()          # randam %
        self.threshold.setRange(1, 100)
        self.threshold.setValue(50)
        self.state_index = QSpinBox(); self.state_index.setRange(0, 999)
        self.state_value = QSpinBox(); self.state_value.setRange(-999, 999)
        self.nin_op = QComboBox(); self.nin_op.addItems(NINSATSU_OPS)
        self.nin_value = QSpinBox(); self.nin_value.setRange(0, 20); self.nin_value.setValue(1)
        self.sp_target = QComboBox(); self.sp_target.addItems(TARGETS)
        self.sp_effect = QLineEdit(); self.sp_effect.setPlaceholderText("effect id")
        self.raw = QLineEdit(); self.raw.setPlaceholderText("raw Lua condition")

        remove = QPushButton("✕")
        remove.setFixedWidth(28)
        if on_remove:
            remove.clicked.connect(lambda: on_remove(self))

        for w in (self.kind, self.negate, self.threshold, self.state_index,
                  self.state_value, self.nin_op, self.nin_value, self.sp_target,
                  self.sp_effect, self.raw):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(remove)

        if term is not None:
            self._load(term)
        self._update_visibility(self.kind.currentText())

    def _load(self, t: Term):
        self.kind.setCurrentText(t.kind)
        self.negate.setChecked(t.negate)
        if t.kind == "randam":
            self.threshold.setValue(t.threshold or 50)
        elif t.kind == "state":
            self.state_index.setValue(t.state_index or 0)
            self.state_value.setValue(t.state_value or 0)
        elif t.kind == "ninsatsu":
            self.nin_op.setCurrentText(t.operator or "<=")
            self.nin_value.setValue(t.threshold or 1)
        elif t.kind == "speffect":
            self.sp_target.setCurrentText(t.target or "TARGET_ENE_0")
            self.sp_effect.setText("" if t.effect_id is None else str(t.effect_id))
        elif t.kind == "raw":
            self.raw.setText(t.raw or "")

    def _update_visibility(self, kind: str):
        vis = {
            "randam": {self.threshold},
            "state": {self.state_index, self.state_value},
            "ninsatsu": {self.nin_op, self.nin_value},
            "speffect": {self.sp_target, self.sp_effect},
            "raw": {self.raw},
        }.get(kind, set())
        for w in (self.threshold, self.state_index, self.state_value, self.nin_op,
                  self.nin_value, self.sp_target, self.sp_effect, self.raw):
            w.setVisible(w in vis)

    def to_term(self) -> Term:
        kind = self.kind.currentText()
        t = Term(kind=kind, negate=self.negate.isChecked())
        if kind == "randam":
            t.threshold = self.threshold.value()
        elif kind == "state":
            t.state_index = self.state_index.value()
            t.state_value = self.state_value.value()
        elif kind == "ninsatsu":
            t.operator = self.nin_op.currentText()
            t.threshold = self.nin_value.value()
        elif kind == "speffect":
            t.target = self.sp_target.currentText()
            try:
                t.effect_id = int(self.sp_effect.text().strip())
            except ValueError:
                t.effect_id = 0
        elif kind == "raw":
            t.raw = self.raw.text().strip()
        return t


class BranchDialog(QDialog):
    def __init__(self, parent=None, branch: Branch | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit branch" if branch else "Add branch")
        self.resize(560, 240)
        self._existing = branch

        outer = QVBoxLayout(self)
        conn_row = QHBoxLayout()
        conn_row.addWidget(QLabel("Join terms with:"))
        self.connective = QComboBox(); self.connective.addItems(["and", "or"])
        conn_row.addWidget(self.connective)
        conn_row.addStretch(1)
        add_btn = QPushButton("Add term")
        add_btn.clicked.connect(lambda: self._add_row())
        conn_row.addWidget(add_btn)
        outer.addLayout(conn_row)

        self._rows_box = QVBoxLayout()
        outer.addLayout(self._rows_box)
        outer.addStretch(1)

        self._rows: list[TermRow] = []
        if branch and branch.terms:
            self.connective.setCurrentText(branch.connective)
            for t in branch.terms:
                self._add_row(t)
        else:
            self._add_row()  # start with one empty term

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _add_row(self, term: Term | None = None):
        row = TermRow(term, on_remove=self._remove_row)
        self._rows.append(row)
        self._rows_box.addWidget(row)

    def _remove_row(self, row: TermRow):
        if len(self._rows) <= 1:
            return  # always keep at least one term
        self._rows.remove(row)
        row.setParent(None)

    def result_branch(self) -> Branch:
        terms = [r.to_term() for r in self._rows]
        true_b = self._existing.true_branch if self._existing else []
        false_b = self._existing.false_branch if self._existing else []
        from_elseif = self._existing.from_elseif if self._existing else False
        return Branch(terms=terms, connective=self.connective.currentText(),
                      from_elseif=from_elseif, true_branch=true_b, false_branch=false_b)
