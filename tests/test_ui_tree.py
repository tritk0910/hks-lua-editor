"""Regression tests for moving/nesting steps in the combo tree (Alt+Up/Down).

This is the area that broke repeatedly: a step must walk the tree's VISIBLE
order, stepping into and out of if/elseif/else bodies rather than jumping out of
the whole branch.
"""

import pytest

from models import Branch, ComboSequence, ComboStep, randam


def _step(anim):
    return ComboStep("ComboRepeat", anim, 10, extra_args=[0, 0])


@pytest.fixture
def ladder(window):
    """if A [100] / elseif B [200] / else [300], loaded into the window.

    Returns (window, head, inner) — `head` is the `if` arm, `inner` the `elseif`
    arm whose false_branch is the else body.
    """
    inner = Branch(terms=[randam(50)], from_elseif=True,
                   true_branch=[_step(200)], false_branch=[_step(300)])
    head = Branch(terms=[randam(50)], true_branch=[_step(100)], false_branch=[inner])
    seq = ComboSequence(name="t", trigger_type="act_entry", trigger_id=1, steps=[head])
    window.seq = seq
    window.refresh()
    return window, head, inner


def _move(window, obj, delta):
    """Select `obj` in the tree and move it one slot up (-1) / down (+1)."""
    window._select_obj(obj)
    window._move_step(window._selected_payload(), delta)


# --- UP ---------------------------------------------------------------------

def test_up_from_else_first_child_enters_elseif_body_end(ladder):
    """The reported bug: a step at the top of `else` jumped out of the whole
    branch instead of moving up into the `elseif` above it."""
    window, head, inner = ladder
    spin = inner.false_branch[0]           # the 300 step, first child of else
    _move(window, spin, -1)
    assert inner.true_branch[-1] is spin   # appended to the elseif body
    assert [s.anim_id for s in inner.true_branch] == [200, 300]
    assert spin not in window.seq.steps    # must NOT pop out to top level
    assert spin not in inner.false_branch


def test_up_again_reorders_within_elseif_body(ladder):
    window, head, inner = ladder
    spin = inner.false_branch[0]
    _move(window, spin, -1)                # into elseif body end -> [200, 300]
    _move(window, spin, -1)                # swap within that body
    assert [s.anim_id for s in inner.true_branch] == [300, 200]


def test_up_from_leading_if_first_child_pops_out_above_branch(ladder):
    window, head, inner = ladder
    first = head.true_branch[0]            # 100, first child of the leading `if`
    _move(window, first, -1)
    assert window.seq.steps[0] is first    # popped out above the whole branch
    assert first not in head.true_branch


def test_up_top_level_step_below_block_enters_else_body_end(ladder):
    """A step sitting below the whole if/elseif/else block moves up into the
    last visible body (the else)."""
    window, head, inner = ladder
    tail = _step(999)
    window.seq.steps.append(tail)          # top level, after the branch
    window.refresh()
    _move(window, tail, -1)
    assert inner.false_branch[-1] is tail  # entered the else body at its end
    assert tail not in window.seq.steps


# --- DOWN -------------------------------------------------------------------

def test_down_from_if_body_enters_elseif_body_top(ladder):
    window, head, inner = ladder
    first = head.true_branch[0]
    _move(window, first, 1)
    assert inner.true_branch[0] is first
    assert first not in head.true_branch


def test_down_from_else_last_child_pops_out_after_branch(ladder):
    """Down from the last visible row still leaves the branch (else body)."""
    window, head, inner = ladder
    last = inner.false_branch[-1]
    _move(window, last, 1)
    assert last in window.seq.steps        # popped out to top level
    assert last not in inner.false_branch


# --- multi-select ------------------------------------------------------------

def test_multi_select_sibling_steps_move_as_a_block(window):
    a, b, c = _step(1), _step(2), _step(3)
    seq = ComboSequence(name="t", trigger_type="act_entry", trigger_id=1,
                        steps=[a, b, c])
    window.seq = seq
    window.refresh()
    window._select_objs([a, b])            # select the first two
    window._move_selected(1)               # move the block down past c
    assert [s.anim_id for s in window.seq.steps] == [3, 1, 2]


# --- add into else -----------------------------------------------------------

def test_add_branch_into_else_nests_a_child(ladder, monkeypatch):
    """Add branch with the `else` node selected nests inside the else body
    (a same-level arm is what 'Add elseif' is for)."""
    window, head, inner = ladder
    import ui.mixins.tree_edit as tree_edit      # patch where it's used
    new = Branch(terms=[randam(25)])

    class Dlg:
        def __init__(self, *a, **k): pass
        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Accepted
        def result_branch(self):
            return new

    monkeypatch.setattr(tree_edit, "BranchDialog", Dlg)
    # select the else node, then add a branch
    else_item = next(it for it in window._iter_tree_items()
                     if (window._payload_of(it) or {}).get("kind") == "else")
    window.tree.setCurrentItem(else_item)
    window._add_branch()
    assert new in inner.false_branch       # nested in the else body
    assert new.from_elseif is False
