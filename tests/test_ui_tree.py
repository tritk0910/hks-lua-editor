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

def _home(head, inner, obj) -> str:
    """Which body of the ladder `obj` currently sits in."""
    for name, body in (("if", head.true_branch), ("elseif", inner.true_branch),
                       ("else", inner.false_branch)):
        if any(x is obj for x in body):
            return name
    return "top"


def test_block_moves_into_the_else_not_the_if(ladder):
    """Reported bug: with several steps selected, Alt+Up from below the block
    jumped into the `if` arm — the block move walked list adjacency, which can't
    see elseif/else (they hang off false_branch, and are only neighbours on
    screen)."""
    window, head, inner = ladder
    a, b = _step(900), _step(901)
    window.seq.steps.extend([a, b])
    window.refresh()
    window._select_objs([a, b])
    window._move_selected(-1)
    assert [s.anim_id for s in inner.false_branch] == [300, 900, 901]
    assert [s.anim_id for s in head.true_branch] == [100]     # NOT the if arm


def test_a_block_walks_the_ladder_exactly_like_a_single_step(window):
    """Parity is the point: one set of destination rules, so the two paths
    can't drift apart again."""
    def build():
        inner = Branch(terms=[randam(50)], from_elseif=True,
                       true_branch=[_step(200)], false_branch=[_step(300)])
        head = Branch(terms=[randam(50)], true_branch=[_step(100)],
                      false_branch=[inner])
        seq = ComboSequence(name="t", trigger_type="act_entry", trigger_id=1,
                            steps=[head, _step(900), _step(901)])
        window.seq = seq
        window.combos.append(window._tag(seq))
        window.refresh()
        return head, inner, seq.steps[1], seq.steps[2]

    head, inner, lead, second = build()
    single = []
    for _ in range(5):
        window._select_obj(lead)
        window._move_step(window._selected_payload(), -1)
        single.append(_home(head, inner, lead))

    head, inner, lead, second = build()
    block = []
    for _ in range(5):
        window._select_objs([lead, second])
        window._move_selected(-1)
        block.append(_home(head, inner, lead))
        assert _home(head, inner, second) == block[-1]   # they travel together

    assert block == single == ["else", "else", "elseif", "elseif", "if"]


def test_block_moves_down_from_the_if_body_into_the_elseif_body(ladder):
    window, head, inner = ladder
    a, b = _step(101), _step(102)
    head.true_branch.extend([a, b])
    window.refresh()
    window._select_objs([a, b])
    window._move_selected(1)
    assert [s.anim_id for s in inner.true_branch] == [101, 102, 200]
    assert [s.anim_id for s in head.true_branch] == [100]


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

# --- removing a selection ---------------------------------------------------

def test_remove_deletes_every_selected_step(window):
    """Reported bug: Remove only ever dropped the focused row."""
    a, b, c = _step(1), _step(2), _step(3)
    window.seq = ComboSequence(name="t", trigger_type="act_entry", trigger_id=1,
                               steps=[a, b, c])
    window.combos.append(window._tag(window.seq))
    window.refresh()
    window._select_objs([a, b])
    window._remove_selected()
    assert [s.anim_id for s in window.seq.steps] == [3]


def test_remove_across_different_bodies(ladder):
    window, head, inner = ladder
    window._select_objs([head.true_branch[0], inner.false_branch[0]])
    window._remove_selected()
    assert head.true_branch == []
    assert inner.false_branch == []
    assert [s.anim_id for s in inner.true_branch] == [200]      # untouched


def test_removing_a_branch_together_with_a_step_inside_it(ladder):
    """The step's list belongs to the branch that is going away — the second
    delete must not fall through to `del lst[-1]`."""
    window, head, inner = ladder
    window.seq.steps.append(_step(900))
    window.refresh()
    window._select_objs([head, head.true_branch[0]])
    window._remove_selected()
    assert head not in window.seq.steps
    assert [s.anim_id for s in window.seq.steps] == [900]       # survivor intact


def test_remove_falls_back_to_the_current_row_when_nothing_is_selected(ladder):
    window, head, inner = ladder
    step = head.true_branch[0]
    window._select_obj(step)
    window.tree.clearSelection()          # current row stays set
    window._remove_selected()
    assert head.true_branch == []


def test_remove_deletes_selected_weights(window, ref_lua):
    from models import ActActivator
    assert window._load_path(ref_lua)
    window.seq = next(c for c in window.combos if isinstance(c, ActActivator))
    window.refresh()
    rows = [it for it in window._iter_tree_items()
            if (window._payload_of(it) or {}).get("kind") == "weight"][:2]
    doomed = [window._payload_of(it)["obj"] for it in rows]
    window.tree.setCurrentItem(rows[0])
    for it in rows:
        it.setSelected(True)
    import writer
    before = len(writer._activator_parts(window.seq)[1])
    window._remove_selected()
    after = writer._activator_parts(window.seq)[1]
    assert len(after) == before - 2
    assert not any(w is d for w in after for d in doomed)


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
