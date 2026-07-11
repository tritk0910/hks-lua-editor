"""Dialog to add or edit a Branch — a condition made of one or more Terms
joined by `and`/`or`."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models import BoolNode, Branch, Term

TERM_KINDS = ["randam", "state", "ninsatsu", "speffect", "raw"]
NINSATSU_OPS = ["<=", ">=", "==", "<", ">"]
TARGETS = ["TARGET_SELF", "TARGET_ENE_0"]


class TermRow(QWidget):
    """One editable condition term: kind + `not` + the fields for that kind."""

    def __init__(self, term: Term | None = None, on_remove=None):
        super().__init__()
        # hug our content vertically so a lone term doesn't stretch to fill the
        # scroll area (the max-height look in the old dialog)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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


class GroupWidget(QFrame):
    """A boolean group: a connective (and/or) + optional `not`, holding a mixed
    list of TermRow and nested GroupWidget children. Recurses for `(A or B) and C`."""

    def __init__(self, op="and", items=None, negate=False, on_remove=None, root=False):
        super().__init__()
        if not root:
            self.setFrameShape(QFrame.StyledPanel)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._children = []   # list[TermRow | GroupWidget]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        header = QHBoxLayout()
        self.op = QComboBox(); self.op.addItems(["and", "or"]); self.op.setCurrentText(op)
        self.negate = QCheckBox("not")
        self.negate.setChecked(negate)
        add_term_btn = QPushButton("+ term"); add_term_btn.clicked.connect(lambda: self.add_term())
        add_group_btn = QPushButton("+ group"); add_group_btn.clicked.connect(lambda: self.add_group())
        header.addWidget(QLabel("Group:" if not root else "Match all/any:"))
        header.addWidget(self.op)
        header.addWidget(self.negate)
        header.addWidget(add_term_btn)
        header.addWidget(add_group_btn)
        header.addStretch(1)
        if not root:
            # far-right ✕, consistent with each TermRow's remove button
            rm = QPushButton("✕"); rm.setFixedWidth(28)
            if on_remove:
                rm.clicked.connect(lambda: on_remove(self))
            header.addWidget(rm)
        outer.addLayout(header)

        self._box = QVBoxLayout()
        self._box.setContentsMargins(18, 2, 0, 0)   # indent children
        self._box.setSpacing(4)
        outer.addLayout(self._box)
        if root:
            outer.addStretch(1)   # keep rows packed at the top

        if items:
            for it in items:
                if isinstance(it, BoolNode):
                    self.add_group(it)
                else:
                    self.add_term(it)
        elif root:
            self.add_term()

    def add_term(self, term: Term | None = None):
        row = TermRow(term, on_remove=self._remove_child)
        self._children.append(row)
        self._box.addWidget(row)

    def add_group(self, node: BoolNode | None = None):
        g = GroupWidget(op=node.op if node else "and",
                        items=node.terms if node else None,
                        negate=node.negate if node else False,
                        on_remove=self._remove_child)
        self._children.append(g)
        self._box.addWidget(g)

    def _remove_child(self, w):
        if w in self._children:
            self._children.remove(w)
            w.setParent(None)

    def to_items(self):
        return [c.to_term() if isinstance(c, TermRow) else c.to_node()
                for c in self._children]

    def to_node(self) -> BoolNode:
        return BoolNode(op=self.op.currentText(), terms=self.to_items(),
                        negate=self.negate.isChecked())


class BranchDialog(QDialog):
    def __init__(self, parent=None, branch: Branch | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit branch" if branch else "Add branch")
        self.resize(680, 320)
        self._existing = branch

        self.root = GroupWidget(op=branch.connective if branch else "and",
                                items=branch.terms if branch else None, root=True)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.root)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def result_branch(self) -> Branch:
        op = self.root.op.currentText()
        terms = self.root.to_items()
        if self.root.negate.isChecked():
            terms = [BoolNode(op=op, terms=terms, negate=True)]
            op = "and"
        true_b = self._existing.true_branch if self._existing else []
        false_b = self._existing.false_branch if self._existing else []
        from_elseif = self._existing.from_elseif if self._existing else False
        return Branch(terms=terms, connective=op,
                      from_elseif=from_elseif, true_branch=true_b, false_branch=false_b)
