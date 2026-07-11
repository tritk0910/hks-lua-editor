"""Tests for dsas.py — importing DSAS combo-viewer text."""

from dsas import parse_dsas_combo, export_dsas
from generator import generate_act
from models import Branch, ComboSequence, ComboStep, randam


def test_parse_dsas_spin_opener():
    text = "EnemyComboAtk 3000\nEnemyComboAtk 3001\nEnemyComboAtk 3002"
    steps = parse_dsas_combo(text, first_is_spin=True)
    assert [s.goal_type for s in steps] == [
        "ComboAttackTunableSpin", "ComboRepeat", "ComboRepeat"]
    assert [s.anim_id for s in steps] == [3000, 3001, 3002]
    assert all(s.priority == 10 and s.distance == 9999 and s.extra_args == [0, 0]
               for s in steps)


def test_parse_dsas_no_spin_opener():
    steps = parse_dsas_combo("EnemyComboAtk 3010\nEnemyComboAtk 3011",
                             first_is_spin=False)
    assert [s.goal_type for s in steps] == ["ComboRepeat", "ComboRepeat"]


def test_parse_dsas_ignores_blank_and_numberless_lines():
    steps = parse_dsas_combo("\nheader\nEnemyComboAtk 3000\n\n")
    assert len(steps) == 1 and steps[0].anim_id == 3000


def test_generate_act_from_dsas_matches_expected():
    steps = parse_dsas_combo("EnemyComboAtk 3000\nEnemyComboAtk 3001\nEnemyComboAtk 3002")
    seq = ComboSequence(name="imported", trigger_type="act_entry", trigger_id=50,
                        steps=steps)
    expected = (
        "Goal.Act50 = function(arg0, arg1, arg2)\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboAttackTunableSpin, 10, 3000, TARGET_ENE_0, 9999, 0, 0)\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboRepeat, 10, 3001, TARGET_ENE_0, 9999, 0, 0)\n"
        "    arg1:AddSubGoal(GOAL_COMMON_ComboRepeat, 10, 3002, TARGET_ENE_0, 9999, 0, 0)\n"
        "    GetWellSpace_Odds = 100\n"
        "    return GetWellSpace_Odds\n"
        "end"
    )
    assert generate_act(seq) == expected


def test_export_dsas_flat():
    steps = [ComboStep("ComboAttackTunableSpin", 3009, 10),
             ComboStep("ComboRepeat", 3011, 10),
             ComboStep("ComboFinal", 3007, 10)]
    assert export_dsas(steps) == "EnemyComboAtk 3009\nEnemyComboAtk 3011\nEnemyComboAtk 3007"


def test_export_dsas_branch_choice():
    # ComboRepeat 3002, then if <=33 [3024] elseif <=66 [3085] else [3070]
    elseif66 = Branch(terms=[randam(66)], from_elseif=True,
                      true_branch=[ComboStep("ComboRepeat", 3085, 10)],
                      false_branch=[ComboStep("ComboRepeat", 3070, 10)])
    head = Branch(terms=[randam(33)],
                  true_branch=[ComboStep("ComboRepeat", 3024, 10)],
                  false_branch=[elseif66])
    steps = [ComboStep("ComboRepeat", 3002, 10), head]
    # default: follow the `if` arm (3024)
    assert export_dsas(steps) == "EnemyComboAtk 3002\nEnemyComboAtk 3024"
    # pick the elseif <=66 arm (index 1) -> 3085
    assert export_dsas(steps, {id(head): 1}) == "EnemyComboAtk 3002\nEnemyComboAtk 3085"
    # pick else -> 3070
    assert export_dsas(steps, {id(head): "else"}) == "EnemyComboAtk 3002\nEnemyComboAtk 3070"
