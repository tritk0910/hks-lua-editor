"""Building, selecting and editing the combo tree.

Everything about the QTreeWidget lives here: rendering the if/elseif/else ladder,
add/edit/duplicate/remove, clipboard, inline cell edits, and the Alt+Up/Down
move logic that nests steps into and out of branch bodies.

Mixed into MainWindow; uses its `tree`, `seq`, `status`, `_payloads`,
`_building` and `_clipboard` state.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QMessageBox, QTreeWidgetItem

from models import (
    ActActivator, Branch, ComboStep, KengekiActivator, RawLine, Weight,
    unchain_branch,
)
from visualizer import condition_text
from ui.branch_dialog import BranchDialog
from ui.helpers import _index_of, _parse_val
from ui.step_dialog import StepDialog

_COMBO_HEADERS = ["Structure", "Anim", "Prio", "Dist", "Extra"]
# the selectors only have a weight per row — the combo columns are meaningless
_WEIGHT_HEADERS = ["Structure", "Weight", "", "", ""]


def _list_inside(branch, lst) -> bool:
    """Is `lst` one of the item lists inside `branch`'s own subtree?

    Compared by identity: dropping a branch into its own body would splice the
    subtree into itself and lose it.
    """
    for body in (branch.true_branch, branch.false_branch):
        if body is lst:
            return True
        if any(isinstance(it, Branch) and _list_inside(it, lst) for it in body):
            return True
    return False


class TreeEditMixin:
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
        if data is None or data["kind"] == "weight":
            return          # weights are edited inline in the Weight column
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
        if isinstance(clone, Weight):
            clone.line = None    # a copy is a NEW assignment, not the same line
        lst.insert(_index_of(lst, obj) + 1, clone)
        self.refresh(select=clone)

    def _remove_selected(self):
        """Remove every selected row, not just the focused one."""
        sel = [d for d in (self._payload_of(it) for it in self.tree.selectedItems())
               if d and d["kind"] in ("step", "branch", "weight", "raw")]
        if not sel:
            data = self._selected_obj_data()   # nothing selected: use the current row
            if data is None:
                return
            sel = [data]
        self._push_undo()
        for data in sel:
            i = _index_of(data["list"], data["obj"])
            if i >= 0:      # already gone with an enclosing branch — never del [-1]
                del data["list"][i]
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
        """Move several selected steps (same list) together as one block.

        Uses the same destination rules as a single step: the anchor is the row
        the block leads with in the direction of travel, so a block walks into
        elseif/else bodies exactly like one step does.
        """
        lst = sel[0]["list"]
        objs = [d["obj"] for d in sorted(sel, key=lambda d: _index_of(lst, d["obj"]))]
        anchor_obj = objs[-1] if delta > 0 else objs[0]
        anchor = next((d for d in sel if d["obj"] is anchor_obj), None)
        item = self._item_for(anchor_obj)
        if anchor is None or item is None:
            return
        dest = self._step_destination(anchor, delta, item)
        if dest is None or not self._apply_move(objs, [lst] * len(objs), dest):
            return
        self.refresh()
        self._select_objs(objs)

    def _item_for(self, obj):
        for item in self._iter_tree_items():
            data = self._payload_of(item)
            if data and data.get("obj") is obj:
                return item
        return None

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

    @staticmethod
    def _body_of(data):
        """The item list a branch-arm / else row owns."""
        return data["list"] if data["kind"] == "else" else data["obj"].true_branch

    def _step_destination(self, data, delta, item):
        """Where a step at `item` lands when moved by `delta`, following the
        tree's VISIBLE order: (dest_list, anchor, where) with `where` one of
        before/after/start/end (anchor is None for start/end). None = can't move.

        Pure — it computes, it doesn't mutate. Both the single-step and the
        multi-step move go through it, so they can't drift apart: list adjacency
        alone can't see elseif/else bodies, since those hang off false_branch
        and are only neighbours on screen.
        """
        lst = data["list"]
        neighbour = (self.tree.itemBelow(item) if delta > 0
                     else self.tree.itemAbove(item))
        owner, owner_list = data.get("owner"), data.get("owner_list")
        if neighbour is None:
            # last visible row: DOWN still pops out one level so a step can
            # leave the branch (e.g. out of the else body). UP has nowhere to go.
            if delta > 0 and owner is not None and owner_list is not None:
                return owner_list, owner, "after"
            return None
        nd = self._payload_of(neighbour)
        if nd is None:
            return None

        if delta > 0:                                   # DOWN
            if nd["kind"] in ("branch", "else"):
                return self._body_of(nd), None, "start"     # enter body from top
            if nd["list"] is lst:
                return lst, nd["obj"], "after"              # reorder within list
            return nd["list"], nd["obj"], "before"          # pop out before it

        # UP
        if nd["kind"] == "step":
            if nd["list"] is lst:
                return lst, nd["obj"], "before"             # reorder within list
            return nd["list"], nd["obj"], "after"           # nest at that body's end
        if item.parent() is neighbour:
            # first child of the container above -> the previous visible body
            # (an else's first child goes to the end of the elseif above it), or
            # out of the branch when nothing sits above that header.
            above = self.tree.itemAbove(neighbour)
            ad = self._payload_of(above) if above is not None else None
            if ad and ad["kind"] == "step":
                return ad["list"], ad["obj"], "after"
            if ad and ad["kind"] in ("branch", "else"):
                return self._body_of(ad), None, "end"
            if owner is None or owner_list is None:
                return None
            return owner_list, owner, "before"
        # a container sits above the whole block -> enter its last body at the end
        return self._body_of(nd), None, "end"

    def _apply_move(self, objs, srcs, dest) -> bool:
        """Pull `objs` out of their `srcs` lists and splice them into `dest`."""
        dest_list, anchor, where = dest
        for obj, src in zip(objs, srcs):
            i = _index_of(src, obj)
            if i < 0:
                return False
            del src[i]
        # only now look the anchor up: removing the items shifts its index
        # whenever the source and destination are the same list
        if where == "start":
            at = 0
        elif where == "end":
            at = len(dest_list)
        else:
            at = _index_of(dest_list, anchor)
            if at < 0:
                return False
            if where == "after":
                at += 1
        dest_list[at:at] = objs
        return True

    def _move_step(self, data, delta):
        obj = data["obj"]
        item = self.tree.currentItem()
        dest = self._step_destination(data, delta, item)
        if dest is None or not self._apply_move([obj], [data["list"]], dest):
            return
        self.refresh(select=obj)

    # --- drag and drop -----------------------------------------------------

    def _drop_destination(self, target, position):
        """(list, index) the dragged items should land in, or None if the drop
        makes no sense. Mirrors _target_list_and_index, but honours whether the
        cursor sat above/below/on the target row."""
        data = self._payload_of(target)
        if target is None or data is None:
            return self.seq.steps, len(self.seq.steps)      # empty space -> end
        if position == QAbstractItemView.OnItem:
            # onto a header: into its body (branch arm -> its `then` side)
            if data["kind"] == "branch":
                return data["obj"].true_branch, len(data["obj"].true_branch)
            if data["kind"] == "else":
                return data["list"], len(data["list"])
            # onto a step: refused — you drop *between* steps, not onto one.
            # Qt shouldn't even offer this (step rows aren't drop-enabled), but
            # the rule belongs here rather than resting on the flag alone.
            return None
        if data["kind"] == "else":
            # there is no slot beside an else header — use its body
            return data["list"], 0 if position == QAbstractItemView.AboveItem \
                else len(data["list"])
        lst = data["list"]
        i = _index_of(lst, data["obj"])
        return lst, i if position == QAbstractItemView.AboveItem else i + 1

    def _handle_drop(self, target, position) -> bool:
        """Move the dragged selection into the dropped-on slot. Returns True if
        the model changed (the view is rebuilt from it)."""
        if not self._is_combo():
            return False        # selector weights are edited in place, not moved
        dragged = [d for d in (self._payload_of(it) for it in self.tree.selectedItems())
                   if d and d["kind"] in ("step", "branch", "raw")]
        if not dragged:
            return False
        dest = self._drop_destination(target, position)
        if dest is None:
            return False
        dest_list, dest_index = dest
        objs = [d["obj"] for d in dragged]
        # dropping a branch into its own body would detach that whole subtree
        if any(isinstance(obj, Branch) and _list_inside(obj, dest_list)
               for obj in objs):
            return False
        self._push_undo()
        # remove first, then insert — removing shifts the destination index when
        # the items came from the same list ahead of the drop point
        for d, obj in zip(dragged, objs):
            src = d["list"]
            i = _index_of(src, obj)
            if src is dest_list and i < dest_index:
                dest_index -= 1
            del src[i]
        for k, obj in enumerate(objs):
            dest_list.insert(dest_index + k, obj)
        self.refresh()
        self._select_objs(objs)
        return True

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
        """Commit an inline cell edit back to the ComboStep / Weight."""
        if self._building:
            return
        data = self._payload_of(item)
        if data and data["kind"] == "weight":
            if column == 1:
                self._push_undo()
                data["obj"].value = _parse_val(item.text(1))
                self._refresh_output()
            return
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

    # --- rendering ---------------------------------------------------------

    def _rebuild_tree(self, select=None):
        self._building = True
        try:
            self.tree.clear()
            self._payloads = []
            root = self.tree.invisibleRootItem()
            if self._is_combo():
                self.tree.setHeaderLabels(_COMBO_HEADERS)
                self._add_tree_items(self.seq.steps, root)
            elif isinstance(self.seq, ActActivator):
                self.tree.setHeaderLabels(_WEIGHT_HEADERS)
                self._add_tree_items(self.seq.items, root)
            elif isinstance(self.seq, KengekiActivator):
                self.tree.setHeaderLabels(_WEIGHT_HEADERS)
                for block in self.seq.blocks:
                    node = QTreeWidgetItem(root, [f"effect {block.effect_id}"])
                    node.setForeground(0, QColor("#2980b9"))
                    self._add_tree_items(block.items, node)
                if self.seq.extra_items:
                    # the vetoes that apply whichever effect matched
                    node = QTreeWidgetItem(root, ["after all effects"])
                    node.setForeground(0, QColor("#2980b9"))
                    self._add_tree_items(self.seq.extra_items, node)
            else:
                return
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
            if isinstance(obj, Weight):
                # selector row: `act[21]` + its weight (col 1), editable inline.
                # Not draggable: weights are written back by line number, so
                # reordering one here would mean nothing on write.
                table = "act" if isinstance(self.seq, ActActivator) else "kengeki"
                node = QTreeWidgetItem(parent, [f"{table}[{obj.index}]",
                                                str(obj.value)])
                node.setFlags((node.flags() | Qt.ItemIsEditable)
                              & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
                self._store_payload(node, {"kind": "weight", "obj": obj,
                                           "list": items_list, "owner": owner,
                                           "owner_list": owner_list})
            elif isinstance(obj, RawLine):
                # a statement we keep verbatim (SetNumber, ClearSubGoal, return…):
                # show it, let it move/remove, but don't pretend it's editable
                node = QTreeWidgetItem(parent, [obj.text.strip()])
                node.setForeground(0, QColor("#8a6d3b"))
                node.setFlags(node.flags() & ~Qt.ItemIsDropEnabled)   # drop between
                self._store_payload(node, {"kind": "raw", "obj": obj, "list": items_list,
                                           "owner": owner, "owner_list": owner_list})
            elif isinstance(obj, ComboStep):
                extra = ", ".join(str(a) for a in obj.extra_args)
                node = QTreeWidgetItem(parent, [obj.goal_type, str(obj.anim_id),
                                                str(obj.priority), str(obj.distance), extra])
                # Inline-editable, and NOT a drop target: a step has no body to
                # drop into. Clearing ItemIsDropEnabled makes Qt turn an
                # "onto this row" hover into Above/Below (see
                # QAbstractItemViewPrivate::position), so dragging over a step
                # only ever offers the insert line between rows. Its 2px
                # above/below margin is otherwise near-impossible to hit.
                node.setFlags((node.flags() | Qt.ItemIsEditable)
                              & ~Qt.ItemIsDropEnabled)
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
