"""Combos keep statements the model can't represent (SetNumber, return true,
ClearSubGoal, ...) as RawLine, instead of dropping them silently.

Before this, an interrupt whose body was just `arg1:SetNumber(0, 0)` / `return
true` parsed to zero steps: nothing in the tree, and writing it wiped the branch.
"""

import pytest

import writer
from generator import generate_interrupt_branch
from models import Branch, ComboSequence, ComboStep, RawLine
from parser import parse_file

USER_BRANCH = """\
Goal.Interrupt = function(arg0, arg1, arg2)
    if IsInterupt(arg1) then
        elseif interruptEffectIdentifier == 3710020 then
            arg1:SetNumber(0, 0)
            return true
        end
    end
end
"""


def _interrupt(parsed, eid):
    return next(s for s in parsed.sequences
                if s.trigger_type == "special_effect" and s.trigger_id == eid)


# --- no longer silent -------------------------------------------------------

def test_a_body_of_only_unmodelled_statements_is_kept(parsed):
    """710300_battle.lua has these; they used to parse to zero steps."""
    for eid in (5029, 110620):
        seq = _interrupt(parsed, eid)
        assert seq.steps, f"interrupt {eid} lost its whole body"
        assert all(isinstance(s, RawLine) for s in seq.steps)


def test_setnumber_shows_up_as_a_raw_step():
    seq = next(s for s in parse_file(USER_BRANCH).sequences if s.trigger_id == 3710020)
    texts = [s.text.strip() for s in seq.steps if isinstance(s, RawLine)]
    assert texts == ["arg1:SetNumber(0, 0)", "return true"]


def test_clearsubgoal_is_kept_in_position_not_forced_first(parsed):
    """Kengeki01 is `SetNumber` then `ClearSubGoal` — order must survive."""
    ken = next(s for s in parsed.sequences
               if s.trigger_type == "kengeki_move" and s.trigger_id == 1)
    raw = [s.text.strip() for s in ken.steps if isinstance(s, RawLine)]
    assert raw[:2] == ["arg0:SetNumber(3, 1)", "arg1:ClearSubGoal()"]


# --- boilerplate is still NOT kept (would double) ---------------------------

def test_generator_boilerplate_is_not_kept_as_raw(parsed):
    act = next(s for s in parsed.sequences
               if s.trigger_type == "act_entry" and s.trigger_id == 1)

    def raws(items):
        for it in items:
            if isinstance(it, RawLine):
                yield it.text.strip()
            elif isinstance(it, Branch):
                yield from raws(it.true_branch)
                yield from raws(it.false_branch)

    kept = list(raws(act.steps))
    assert not any(t.startswith(("local local", "Approach_Act_Flex",
                                 "GetWellSpace_Odds", "return GetWellSpace_Odds"))
                   for t in kept)


# --- writing: the user's case round-trips -----------------------------------

def test_the_users_interrupt_writes_without_a_spurious_clearsubgoal():
    seq = next(s for s in parse_file(USER_BRANCH).sequences if s.trigger_id == 3710020)
    assert writer._combo_is_faithful(USER_BRANCH, seq)
    out, _ = writer.apply_sequence(USER_BRANCH, seq)
    assert "arg1:SetNumber(0, 0)" in out
    assert "return true" in out
    assert "ClearSubGoal" not in out          # none in the source, none added


def test_rewriting_an_unedited_kept_combo_changes_nothing(parsed, text):
    """5029 is only `return arg0.Damaged(...)`. Writing it back is a no-op."""
    seq = _interrupt(parsed, 5029)
    out, _ = writer.apply_sequence(text, seq)
    assert out == text


# --- the gate ---------------------------------------------------------------

def test_an_act_with_inlined_locals_is_refused_not_corrupted(parsed, text):
    """Act01 resolves `local`s into its args, so the generator can't reproduce
    it — writing must refuse, leaving the file untouched."""
    act = next(s for s in parsed.sequences
               if s.trigger_type == "act_entry" and s.trigger_id == 1)
    assert writer._combo_is_faithful(text, act) is False
    out, summary = writer.apply_sequence(text, act)
    assert out == text
    assert "can't reproduce" in summary[0]


def test_a_brand_new_combo_is_not_gated():
    """Nothing on disk to be faithful to."""
    seq = ComboSequence(name="new", trigger_type="act_entry", trigger_id=77,
                        steps=[ComboStep("ComboFinal", 3000, 10, extra_args=[0])])
    assert writer._combo_is_faithful("-- empty file\n", seq) is True


# --- fresh combos are seeded with ClearSubGoal ------------------------------

@pytest.mark.parametrize("ttype, receiver", [
    ("special_effect", "arg2"), ("kengeki_move", "arg1")])
def test_new_interrupt_and_kengeki_get_a_clearsubgoal(window, monkeypatch, ttype, receiver):
    import ui.main_window as mw

    class Dlg:
        def __init__(self, *a, **k): pass
        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Accepted
        def result(self):
            return ("fresh", ttype, 42)

    monkeypatch.setattr(mw, "ComboDialog", Dlg)
    window._new_combo()
    first = window.seq.steps[0]
    assert isinstance(first, RawLine)
    assert first.text.strip() == f"{receiver}:ClearSubGoal()"


def test_a_hand_built_interrupt_without_clearsubgoal_emits_none():
    """The generator no longer forces one in — it comes from the model."""
    seq = ComboSequence(name="i", trigger_type="special_effect", trigger_id=1,
                        steps=[RawLine("            arg2:SetNumber(0, 0)")])
    assert "ClearSubGoal" not in generate_interrupt_branch(seq)


# --- raw rows in the tree ---------------------------------------------------

def test_raw_rows_are_shown_and_removable(window):
    window.seq = ComboSequence(name="i", trigger_type="special_effect", trigger_id=1,
                               steps=[RawLine("            arg2:ClearSubGoal()"),
                                      ComboStep("ComboFinal", 3000, 10)])
    window._tag(window.seq)
    window.combos.append(window.seq)
    window.refresh()
    raw_row = next(it for it in window._iter_tree_items()
                   if (window._payload_of(it) or {}).get("kind") == "raw")
    window.tree.setCurrentItem(raw_row)
    window._remove_selected()
    assert not any(isinstance(s, RawLine) for s in window.seq.steps)
