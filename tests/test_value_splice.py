"""Editing a step's VALUE writes even when the combo can't round-trip.

An Act with `local`s (args like `local7`, an `Approach_Act_Flex(arg0, arg1,
local0, ...)`) can't be regenerated faithfully, so a whole-function rewrite is
refused. But changing one anim id only needs that one line: it is spliced,
keeping the args the model inlined and touching nothing else.
"""

import writer
from models import ComboStep

ACT20 = """\
Goal.Act20 = function(arg0, arg1, arg2)
    local local0 = 12.5 - arg0:GetMapHitRadius(TARGET_SELF)
    local local7 = 0
    local local8 = 0
    if arg0:GetNumber(7) == 1 then
        arg0:SetNumber(10, 1)
    end
    Approach_Act_Flex(arg0, arg1, local0, local0, local0, 100, 0, 2.5, 3)
    arg1:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 10, 3008, TARGET_ENE_0, 9999, local7, local8, 0, 0):TimingSetNumber(7, 1, AI_TIMING_SET__ACTIVATE)
    GetWellSpace_Odds = 100
    return GetWellSpace_Odds
end
"""


def _combo(lua, tid=20):
    from parser import parse_file
    return next(s for s in parse_file(lua).sequences if s.trigger_id == tid)


def _steps(items):
    for it in items:
        if isinstance(it, ComboStep):
            yield it
        elif hasattr(it, "true_branch"):
            yield from _steps(it.true_branch)
            yield from _steps(it.false_branch)


def _the_step(seq):
    return next(_steps(seq.steps))


def _changed_lines(before, after):
    a, b = before.split("\n"), after.split("\n")
    return None if len(a) != len(b) else [i + 1 for i in range(len(a)) if a[i] != b[i]]


# --- the reported case ------------------------------------------------------

def test_editing_an_anim_id_writes_and_touches_only_that_line():
    seq = _combo(ACT20)
    assert not writer._combo_is_faithful(ACT20, seq)   # locals -> not faithful
    _the_step(seq).anim_id = 3038
    out, summary = writer.apply_sequence(ACT20, seq)

    assert out != ACT20
    assert _changed_lines(ACT20, out) == [9]           # the AddSubGoal line only
    assert out == ACT20.replace(", 3008,", ", 3038,")  # exactly that, nothing else


def test_the_inlined_local_args_and_chain_are_preserved():
    seq = _combo(ACT20)
    _the_step(seq).anim_id = 3038
    out, _ = writer.apply_sequence(ACT20, seq)
    assert "9999, local7, local8, 0, 0)" in out                 # locals, not 0,0
    assert ":TimingSetNumber(7, 1, AI_TIMING_SET__ACTIVATE)" in out
    assert "Approach_Act_Flex(arg0, arg1, local0" in out        # untouched
    assert "local local7 = 0" in out and "arg0:SetNumber(10, 1)" in out


def test_the_edit_survives_a_reparse():
    seq = _combo(ACT20)
    _the_step(seq).anim_id = 3038
    out, _ = writer.apply_sequence(ACT20, seq)
    assert _the_step(_combo(out)).anim_id == 3038


def test_several_fields_at_once():
    seq = _combo(ACT20)
    step = _the_step(seq)
    step.anim_id, step.priority = 3040, 15
    out, summary = writer.apply_sequence(ACT20, seq)
    assert _changed_lines(ACT20, out) == [9]
    assert ", 15, 3040, " in out
    assert "local7, local8" in out                     # still preserved


def test_a_wrapped_chained_call_keeps_its_wrap():
    wrapped = ACT20.replace(
        ":TimingSetNumber(7, 1, AI_TIMING_SET__ACTIVATE)",
        ":TimingSetNumber(7,\n        1, AI_TIMING_SET__ACTIVATE)")
    seq = _combo(wrapped)
    _the_step(seq).anim_id = 3038
    out, _ = writer.apply_sequence(wrapped, seq)
    assert out == wrapped.replace(", 3008,", ", 3038,")   # only the anim, wrap intact


# --- what value-splice must NOT do ------------------------------------------

def test_adding_a_step_is_refused_not_spliced():
    seq = _combo(ACT20)
    seq.steps.append(ComboStep("ComboFinal", 9999, 10, extra_args=[0]))
    out, summary = writer.apply_sequence(ACT20, seq)
    assert out == ACT20
    assert "NOT written" in summary[0]


def test_removing_a_step_is_refused():
    seq = _combo(ACT20)
    seq.steps[:] = [s for s in seq.steps if not isinstance(s, ComboStep)]
    out, _ = writer.apply_sequence(ACT20, seq)
    assert out == ACT20


def test_reordering_steps_is_refused():
    lua = """\
Goal.Act20 = function(arg0, arg1, arg2)
    local local7 = 0
    arg1:AddSubGoal(GOAL_COMMON_ComboRepeat, 10, 100, TARGET_ENE_0, 9999, local7)
    arg1:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 200, TARGET_ENE_0, 9999, local7)
    GetWellSpace_Odds = 100
    return GetWellSpace_Odds
end
"""
    seq = _combo(lua)
    assert not writer._combo_is_faithful(lua, seq)
    seq.steps.reverse()
    out, _ = writer.apply_sequence(lua, seq)
    assert out == lua                                   # a reorder isn't a value edit


def test_a_faithful_combo_still_regenerates_normally():
    """No locals -> the normal whole-function path handles it; value-splice
    doesn't get involved."""
    lua = """\
Goal.Act20 = function(arg0, arg1, arg2)
    arg1:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3007, TARGET_ENE_0, 9999, 0, 0)
    GetWellSpace_Odds = 100
    return GetWellSpace_Odds
end
"""
    seq = _combo(lua)
    assert writer._combo_is_faithful(lua, seq)
    _the_step(seq).anim_id = 3038
    out, summary = writer.apply_sequence(lua, seq)
    assert "3038" in out
    assert "NOT written" not in (summary[0] if summary else "")
