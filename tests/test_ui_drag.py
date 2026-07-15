"""Drag-and-drop reordering in the combo tree.

The tree only renders the model, so a drop must be translated into a list edit
rather than letting Qt reparent rows — these pin down that translation.
"""

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView

from models import Branch, ComboSequence, ComboStep, randam

ABOVE = QAbstractItemView.AboveItem
BELOW = QAbstractItemView.BelowItem
ON = QAbstractItemView.OnItem
VIEWPORT = QAbstractItemView.OnViewport


def _step(anim):
    return ComboStep("ComboRepeat", anim, 10, extra_args=[0, 0])


@pytest.fixture
def ladder(window):
    """if A [100] / elseif B [200] / else [300], plus a top-level step 900."""
    inner = Branch(terms=[randam(50)], from_elseif=True,
                   true_branch=[_step(200)], false_branch=[_step(300)])
    head = Branch(terms=[randam(50)], true_branch=[_step(100)], false_branch=[inner])
    seq = ComboSequence(name="t", trigger_type="act_entry", trigger_id=1,
                        steps=[head, _step(900)])
    window.seq = seq
    window.refresh()
    return window, head, inner


def _row(window, pred):
    for item in window._iter_tree_items():
        data = window._payload_of(item)
        if data and pred(data):
            return item, data
    raise AssertionError("no such row")


def _step_row(window, anim):
    return _row(window, lambda d: d["kind"] == "step" and d["obj"].anim_id == anim)


def _drag(window, objs):
    window._select_objs(objs)


# --- where a drop lands -----------------------------------------------------

def test_drop_onto_a_branch_header_enters_its_then_body(ladder):
    window, head, inner = ladder
    tail = window.seq.steps[1]                 # the top-level 900
    _drag(window, [tail])
    arm, _ = _row(window, lambda d: d["kind"] == "branch" and d["obj"] is head)
    assert window._handle_drop(arm, ON)
    assert head.true_branch[-1] is tail
    assert tail not in window.seq.steps


def test_drop_onto_an_else_header_enters_the_else_body(ladder):
    window, head, inner = ladder
    tail = window.seq.steps[1]
    _drag(window, [tail])
    else_row, _ = _row(window, lambda d: d["kind"] == "else")
    assert window._handle_drop(else_row, ON)
    assert inner.false_branch[-1] is tail


def test_drop_above_a_step_inserts_before_it(ladder):
    window, head, inner = ladder
    tail = window.seq.steps[1]
    _drag(window, [tail])
    row, _ = _step_row(window, 200)             # inside the elseif body
    assert window._handle_drop(row, ABOVE)
    assert [s.anim_id for s in inner.true_branch] == [900, 200]


def test_drop_below_a_step_inserts_after_it(ladder):
    window, head, inner = ladder
    tail = window.seq.steps[1]
    _drag(window, [tail])
    row, _ = _step_row(window, 200)
    assert window._handle_drop(row, BELOW)
    assert [s.anim_id for s in inner.true_branch] == [200, 900]


def test_drop_on_empty_space_moves_to_the_end_of_the_combo(ladder):
    window, head, inner = ladder
    nested = inner.true_branch[0]               # the 200, inside the elseif
    _drag(window, [nested])
    assert window._handle_drop(None, VIEWPORT)
    assert window.seq.steps[-1] is nested
    assert nested not in inner.true_branch


# --- reordering within one list --------------------------------------------

def test_reordering_within_a_list_accounts_for_the_removal(window):
    a, b, c = _step(1), _step(2), _step(3)
    window.seq = ComboSequence(name="t", trigger_type="act_entry", trigger_id=1,
                               steps=[a, b, c])
    window.refresh()
    _drag(window, [a])
    row, _ = _step_row(window, 3)
    assert window._handle_drop(row, BELOW)      # move `a` to the very end
    assert [s.anim_id for s in window.seq.steps] == [2, 3, 1]


def test_dropping_several_steps_keeps_their_order(ladder):
    window, head, inner = ladder
    window.seq.steps.extend([_step(901), _step(902)])
    window.refresh()
    first = window.seq.steps[1]
    picks = [first, window.seq.steps[2], window.seq.steps[3]]
    _drag(window, picks)
    arm, _ = _row(window, lambda d: d["kind"] == "branch" and d["obj"] is head)
    assert window._handle_drop(arm, ON)
    assert [s.anim_id for s in head.true_branch] == [100, 900, 901, 902]


# --- guards -----------------------------------------------------------------

def test_a_branch_cannot_be_dropped_into_its_own_body(ladder):
    """Splicing a branch into itself would detach the whole subtree."""
    window, head, inner = ladder
    _drag(window, [head])
    row, _ = _step_row(window, 100)             # a step inside head's own body
    assert window._handle_drop(row, BELOW) is False
    assert window.seq.steps[0] is head          # nothing moved
    assert head.true_branch[0].anim_id == 100


def test_dropping_a_branch_into_a_nested_arms_body_is_refused(ladder):
    window, head, inner = ladder
    _drag(window, [head])
    row, _ = _step_row(window, 300)             # inside head -> inner -> else
    assert window._handle_drop(row, BELOW) is False
    assert window.seq.steps[0] is head


def test_selector_weights_are_not_draggable(window, ref_lua):
    """Weights are written by line, so moving one in the tree would be a lie."""
    from models import ActActivator
    assert window._load_path(ref_lua)
    window.seq = next(c for c in window.combos if isinstance(c, ActActivator))
    window.refresh()
    row, data = _row(window, lambda d: d["kind"] == "weight")
    window.tree.setCurrentItem(row)
    row.setSelected(True)
    assert window._handle_drop(row, BELOW) is False


# --- the Qt glue itself -----------------------------------------------------
# The tests above call _handle_drop directly, so they only cover the drop ->
# model translation. These drive ComboTree.dropEvent with a real QDropEvent:
# a hand-rolled fake event accepts arguments the real API rejects, which is
# exactly how `setDropAction(0)` shipped broken.

def _drop_event(pos=QPointF(20, 10)):
    return QDropEvent(pos, Qt.MoveAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)


def test_dropevent_moves_the_model_through_the_real_qt_api(ladder):
    window, head, inner = ladder
    tail = window.seq.steps[1]                  # top-level 900
    _drag(window, [tail])
    window.tree.dropEvent(_drop_event())        # would raise if the API is misused
    assert tail not in window.seq.steps         # it went somewhere in the ladder


def test_dropevent_clears_the_move_action(ladder):
    """Leaving MoveAction set makes QAbstractItemView.startDrag call
    clearOrRemove(), deleting rows from the tree we rebuilt ourselves."""
    window, head, inner = ladder
    _drag(window, [window.seq.steps[1]])
    event = _drop_event()
    window.tree.dropEvent(event)
    assert event.dropAction() == Qt.IgnoreAction
    assert event.isAccepted()


def test_dropevent_without_a_handler_is_ignored(qapp):
    from ui.combo_tree import ComboTree
    tree = ComboTree()
    event = _drop_event()
    tree.dropEvent(event)
    assert not event.isAccepted()


def test_drop_with_nothing_selected_does_nothing(ladder):
    window, head, inner = ladder
    window.tree.clearSelection()
    window.tree.setCurrentItem(None)
    row, _ = _step_row(window, 100)
    assert window._handle_drop(row, BELOW) is False
