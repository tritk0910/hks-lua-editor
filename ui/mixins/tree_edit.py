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
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem

from models import (
    ActActivator, Branch, ComboStep, KengekiActivator, Weight, unchain_branch,
)
from visualizer import condition_text
from ui.branch_dialog import BranchDialog
from ui.helpers import _index_of, _parse_val
from ui.step_dialog import StepDialog

_COMBO_HEADERS = ["Structure", "Anim", "Prio", "Dist", "Extra"]
# the selectors only have a weight per row — the combo columns are meaningless
_WEIGHT_HEADERS = ["Structure", "Weight", "", "", ""]


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
                # selector row: `act[21]` + its weight (col 1), editable inline
                table = "act" if isinstance(self.seq, ActActivator) else "kengeki"
                node = QTreeWidgetItem(parent, [f"{table}[{obj.index}]",
                                                str(obj.value)])
                node.setFlags(node.flags() | Qt.ItemIsEditable)
                self._store_payload(node, {"kind": "weight", "obj": obj,
                                           "list": items_list, "owner": owner,
                                           "owner_list": owner_list})
            elif isinstance(obj, ComboStep):
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
