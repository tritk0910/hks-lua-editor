"""Tests for parser.py, run against the real 710300_battle.lua reference.

The `parsed` fixture lives in conftest.py and skips when that file is absent.
"""

from models import Branch, ComboStep
from generator import generate_act


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
    assert branch.terms[0].kind == "randam"
    assert branch.terms[0].threshold == 30
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
    # steps[0] is now the ClearSubGoal RawLine kept verbatim; the branch follows
    branch = next(s for s in seq.steps if isinstance(s, Branch))
    assert branch.terms[0].kind == "randam"
    assert branch.terms[0].threshold == 50
    assert branch.true_branch[0].anim_id == 3049
    assert branch.false_branch[0].anim_id == 3041


def test_interrupt_3710071_chained_timing_warns(parsed):
    seq = _interrupt(parsed, 3710071)
    # the branch parses at least one step
    assert seq.steps
    # a warning about the dropped :TimingSetNumber chain was recorded
    assert any("chained call after AddSubGoal" in w.message for w in parsed.warnings)


def test_act23_param_if_skipped_with_warning(parsed):
    _act(parsed, 23)  # must exist and not crash
    assert any("skipped non-combo if" in w.message for w in parsed.warnings)


def test_ninsatsu_condition_parsed(parsed):
    # 710300_battle.lua uses `arg1:GetNinsatsuNum() <= 1` inside kengeki moves
    # and `ninsatsu <= 1` inside Goal.Interrupt — both must classify as ninsatsu.
    from models import Branch

    def find_ninsatsu(items):
        for it in items:
            if isinstance(it, Branch):
                for t in it.terms:
                    if t.kind == "ninsatsu":
                        return t
                for sub in (it.true_branch, it.false_branch):
                    got = find_ninsatsu(sub)
                    if got:
                        return got
        return None

    found = None
    for seq in parsed.sequences:
        found = find_ninsatsu(seq.steps)
        if found:
            break
    assert found is not None
    assert found.operator in ("<=", ">=", "==", "<", ">")


def test_parses_grouped_condition_to_ast():
    from parser import _classify_condition
    from models import BoolNode, Term
    cond = ("(arg1:HasSpecialEffectId(TARGET_ENE_0, 9505) or "
            "arg1:HasSpecialEffectId(TARGET_ENE_0, 9506)) and getDist <= 13")
    b = _classify_condition(cond, {}, [])
    assert b.connective == "and"
    assert len(b.terms) == 2
    grp = b.terms[0]
    assert isinstance(grp, BoolNode) and grp.op == "or" and len(grp.terms) == 2
    assert all(t.kind == "speffect" for t in grp.terms)
    assert isinstance(b.terms[1], Term)   # getDist <= 13 -> raw term, separate node


def test_speffect_and_compound_condition_parsed(parsed):
    # Goal.Interrupt uses `... and arg1:HasSpecialEffectId(TARGET_SELF, 3710032)`
    # and negated `not arg1:HasSpecialEffectId(...)`. Confirm terms are modelled.
    from models import Branch

    speffect_terms, compound_branches, negated = [], [], []

    def walk(items):
        for it in items:
            if isinstance(it, Branch):
                if len(it.terms) >= 2:
                    compound_branches.append(it)
                for t in it.terms:
                    if t.kind == "speffect":
                        speffect_terms.append(t)
                    if t.negate:
                        negated.append(t)
                walk(it.true_branch)
                walk(it.false_branch)

    for seq in parsed.sequences:
        walk(seq.steps)
    assert speffect_terms, "expected at least one HasSpecialEffectId term"
    assert all(t.target in ("TARGET_SELF", "TARGET_ENE_0") for t in speffect_terms)
    assert compound_branches, "expected at least one multi-term (and/or) condition"


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
        "    arg0:SetNumber(3, 1)\n"          # kept verbatim now (was silently dropped)
        "    arg1:ClearSubGoal()\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3050, TARGET_ENE_0, 9999, 0, 0)\n"
        "end"
    )
    assert generate_kengeki_move(seq) == expected


def test_kengeki37_elseif_vs_nested_if(parsed):
    # Kengeki37: `if <=50 then A else (if <=33 ... elseif <=66 ... else ...)`.
    # The inner `if <=33` is a nested else-if (from_elseif False); only `<=66`
    # is a real elseif (from_elseif True).
    from models import Branch
    seq = _kengeki(parsed, 37)
    outer = next(s for s in seq.steps if isinstance(s, Branch))  # if <=50
    assert outer.terms[0].threshold == 50
    inner = outer.false_branch[0]        # the nested `if <=33`
    assert isinstance(inner, Branch) and inner.terms[0].threshold == 33
    assert inner.from_elseif is False    # reached via `else { if }`, not elseif
    elseif66 = inner.false_branch[0]     # the real `elseif <=66`
    assert isinstance(elseif66, Branch) and elseif66.terms[0].threshold == 66
    assert elseif66.from_elseif is True


def test_kengeki02_chained_timing(parsed):
    seq = _kengeki(parsed, 2)
    assert seq.steps  # parsed at least one AddSubGoal
    assert any("chained call after AddSubGoal" in w.message for w in parsed.warnings)


# --- Slice 3b: Kengeki_Activate selector ----------------------------------

def _kengeki_activator(parsed):
    from models import KengekiActivator
    acts = [a for a in parsed.activators if isinstance(a, KengekiActivator)]
    assert len(acts) == 1
    return acts[0]


def _block(parsed, eid):
    for b in _kengeki_activator(parsed).blocks:
        if b.effect_id == eid:
            return b
    raise AssertionError(f"kengeki effect block {eid} not found")


def test_activator_parsed_once(parsed):
    eids = {b.effect_id for b in _kengeki_activator(parsed).blocks}
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
