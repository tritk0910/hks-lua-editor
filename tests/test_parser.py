"""Tests for parser.py, run against the real 710300_battle.lua reference."""

import os

import pytest

from models import Branch, ComboStep
from generator import generate_act
from parser import parse_file

REF = os.path.join(os.path.dirname(os.path.dirname(__file__)), "710300_battle.lua")


@pytest.fixture(scope="module")
def parsed():
    with open(REF, encoding="utf-8", errors="ignore") as f:
        return parse_file(f.read())


def _act(parsed, num):
    for seq in parsed.sequences:
        if seq.trigger_type == "act_entry" and seq.trigger_id == num:
            return seq
    raise AssertionError(f"Act{num:02d} not found")


def _interrupt(parsed, eid):
    for seq in parsed.sequences:
        if seq.trigger_type == "special_effect" and seq.trigger_id == eid:
            return seq
    raise AssertionError(f"interrupt {eid} not found")


def test_roundtrip_act04(parsed):
    seq = _act(parsed, 4)
    expected = (
        "Goal.Act04 = function(arg0, arg1, arg2)\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboAttackTunableSpin, 10, 3009, TARGET_ENE_0, 9999, 0, 0)\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboRepeat, 10, 3011, TARGET_ENE_0, 9999, 0, 0)\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3007, TARGET_ENE_0, 9999, 0, 0)\n"
        "    GetWellSpace_Odds = 100\n"
        "    return GetWellSpace_Odds\n"
        "end"
    )
    assert generate_act(seq) == expected


def test_act01_resolve_random_and_distance(parsed):
    seq = _act(parsed, 1)
    # first item is the random branch derived from `local7 <= 30`
    branch = seq.steps[0]
    assert isinstance(branch, Branch)
    assert branch.kind == "random_percent"
    assert branch.threshold == 30
    # first step inside the true branch keeps its distance as a resolved expr
    first_step = branch.true_branch[0]
    assert isinstance(first_step, ComboStep)
    assert first_step.distance == "3.5 - arg0:GetMapHitRadius(TARGET_SELF)"


def test_act01_approach(parsed):
    seq = _act(parsed, 1)
    assert seq.approach is not None
    assert len(seq.approach) == 7
    assert seq.approach[3] == 100
    assert seq.approach[0] == "3.6 - arg0:GetMapHitRadius(TARGET_SELF)"


def test_interrupt_5031_random_two_finals(parsed):
    seq = _interrupt(parsed, 5031)
    branch = seq.steps[0]
    assert isinstance(branch, Branch)
    assert branch.kind == "random_percent"
    assert branch.threshold == 50
    assert branch.true_branch[0].anim_id == 3049
    assert branch.false_branch[0].anim_id == 3041


def test_interrupt_3710071_chained_timing_warns(parsed):
    seq = _interrupt(parsed, 3710071)
    # the branch parses at least one step
    assert seq.steps
    # a warning about the dropped :TimingSetNumber chain was recorded
    assert any("chained call after AddSubGoal" in w for w in parsed.warnings)


def test_act23_param_if_skipped_with_warning(parsed):
    _act(parsed, 23)  # must exist and not crash
    assert any("skipped non-combo if" in w for w in parsed.warnings)


def _kengeki(parsed, num):
    for seq in parsed.sequences:
        if seq.trigger_type == "kengeki_move" and seq.trigger_id == num:
            return seq
    raise AssertionError(f"Kengeki{num:02d} not found")


def test_kengeki01_roundtrip(parsed):
    from generator import generate_kengeki_move
    seq = _kengeki(parsed, 1)
    expected = (
        "Goal.Kengeki01 = function(arg0, arg1, arg2)\n"
        "    arg1:ClearSubGoal()\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3050, TARGET_ENE_0, 9999, 0, 0)\n"
        "end"
    )
    assert generate_kengeki_move(seq) == expected


def test_kengeki02_chained_timing(parsed):
    seq = _kengeki(parsed, 2)
    assert seq.steps  # parsed at least one AddSubGoal
    assert any("chained call after AddSubGoal" in w for w in parsed.warnings)


# --- Slice 3b: Kengeki_Activate selector ----------------------------------

def _block(parsed, eid):
    assert len(parsed.activators) == 1
    for b in parsed.activators[0].blocks:
        if b.effect_id == eid:
            return b
    raise AssertionError(f"kengeki effect block {eid} not found")


def test_activator_parsed_once(parsed):
    assert len(parsed.activators) == 1
    eids = {b.effect_id for b in parsed.activators[0].blocks}
    # 0 (guard) must be excluded; the real effect ids present
    assert 0 not in eids
    assert {200200, 200201, 200210, 200211}.issubset(eids)


def test_flat_block_200210_weights(parsed):
    from models import KengekiWeight
    block = _block(parsed, 200210)
    weights = {w.index: w.value for w in block.items if isinstance(w, KengekiWeight)}
    assert weights == {17: 100, 23: 100, 41: 50, 31: 50, 33: 100, 36: 100}


def test_nested_block_200200_has_branch(parsed):
    from models import Branch, KengekiWeight
    block = _block(parsed, 200200)
    kinds = [type(x).__name__ for x in block.items]
    assert "Branch" in kinds  # distance/GetNumber gating present


def test_generate_kengeki_activate_roundtrip_flat(parsed):
    from generator import generate_kengeki_activate
    from models import KengekiActivator, KengekiEffectBlock
    block = _block(parsed, 200210)
    lua = generate_kengeki_activate(KengekiActivator(blocks=[block]))
    assert lua.startswith("if local0 == 200210 then")
    assert "    kengeki[17] = 100" in lua
    assert lua.endswith("end")
