"""Golden-string tests for generator.py, anchored to 710300_battle.lua."""

from models import (
    Branch, ComboSequence, ComboStep,
    randam, state, ninsatsu, speffect, raw,
)
from generator import (
    generate_act,
    generate_interrupt_branch,
    goal_type_to_lua,
    needs_registration,
    registration_line,
    render_step,
)


def test_goal_type_prefix():
    assert goal_type_to_lua("ComboFinal") == "GOAL_COMMON_ComboFinal"
    # already-qualified constants pass through untouched
    assert goal_type_to_lua("GOAL_COMMON_EndureAttack") == "GOAL_COMMON_EndureAttack"


def test_render_step_arg_order():
    # model order is anim_id before priority; Lua order is priority before anim_id
    step = ComboStep(goal_type="ComboFinal", anim_id=3007, priority=10,
                     distance=9999, target="TARGET_ENE_0", extra_args=[0, 0])
    assert render_step(step, "arg1", "    ") == (
        "    arg1:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3007, TARGET_ENE_0, 9999, 0, 0)"
    )


def test_generate_act_flat_chain_matches_act04():
    # Reference Goal.Act04, lines 330-335 of 710300_battle.lua
    seq = ComboSequence(
        name="test act", trigger_type="act_entry", trigger_id=4,
        steps=[
            ComboStep("ComboAttackTunableSpin", 3009, 10, extra_args=[0, 0]),
            ComboStep("ComboRepeat", 3011, 10, extra_args=[0, 0]),
            ComboStep("ComboFinal", 3007, 10, extra_args=[0, 0]),
        ],
    )
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


def test_generate_act_random_branch():
    seq = ComboSequence(
        name="branchy", trigger_type="act_entry", trigger_id=2,
        steps=[
            ComboStep("ComboAttackTunableSpin", 3004, 10, extra_args=[0, 0]),
            Branch(
                terms=[randam(50)],
                true_branch=[ComboStep("ComboRepeat", 3028, 10, extra_args=[0, 0])],
                false_branch=[ComboStep("ComboRepeat", 3082, 10, extra_args=[0, 0])],
            ),
        ],
    )
    out = generate_act(seq)
    assert "    if arg0:GetRandam_Int(1, 100) <= 50 then" in out
    assert "        arg1:AddSubGoal(GOAL_COMMON_ComboRepeat, 10, 3028, TARGET_ENE_0, 9999, 0, 0)" in out
    assert "    else" in out
    assert "        arg1:AddSubGoal(GOAL_COMMON_ComboRepeat, 10, 3082, TARGET_ENE_0, 9999, 0, 0)" in out
    assert "    end" in out


def test_generate_interrupt_branch_matches_5031():
    # Reference elseif interruptEffectIdentifier == 5031, lines 911-917
    seq = ComboSequence(
        name="kick", trigger_type="special_effect", trigger_id=5031,
        steps=[
            Branch(
                terms=[randam(50)],
                true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])],
                false_branch=[ComboStep("ComboFinal", 3041, 10, extra_args=[0])],
            ),
        ],
    )
    expected = (
        "        elseif interruptEffectIdentifier == 5031 then\n"
        "            arg2:ClearSubGoal()\n"
        "            if randam <= 50 then\n"
        "                arg2:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3049, TARGET_ENE_0, 9999, 0)\n"
        "            else\n"
        "                arg2:AddSubGoal(GOAL_COMMON_ComboFinal, 10, 3041, TARGET_ENE_0, 9999, 0)\n"
        "            end"
    )
    assert generate_interrupt_branch(seq) == expected


def test_state_check_branch():
    seq = ComboSequence(
        name="stateful", trigger_type="special_effect", trigger_id=3710071,
        steps=[
            Branch(
                terms=[state(12, 0)],
                true_branch=[ComboStep("ComboRepeat", 3006, 5, extra_args=[0])],
            ),
        ],
    )
    out = generate_interrupt_branch(seq)
    assert "if arg1:GetNumber(12) == 0 then" in out


def test_registration_line_targets():
    assert registration_line(5025) == (
        "    arg1:AddObserveSpecialEffectAttribute(TARGET_SELF, 5025)"
    )
    assert registration_line(5025, "TARGET_ENE_0") == (
        "    arg1:AddObserveSpecialEffectAttribute(TARGET_ENE_0, 5025)"
    )


def test_distance_expression_and_approach():
    # distance as a resolved Lua expression string, plus an Approach_Act_Flex line
    seq = ComboSequence(
        name="approachy", trigger_type="act_entry", trigger_id=1,
        approach=[100, 0, "3.6 - arg0:GetMapHitRadius(TARGET_SELF)"],
        steps=[
            ComboStep("ComboAttackTunableSpin", 3000, 10,
                      distance="3.5 - arg0:GetMapHitRadius(TARGET_SELF)",
                      extra_args=[0, 0]),
        ],
    )
    out = generate_act(seq)
    assert "    Approach_Act_Flex(arg0, arg1, 100, 0, 3.6 - arg0:GetMapHitRadius(TARGET_SELF))" in out
    assert ("    arg1:AddSubGoal(GOAL_COMMON_ComboAttackTunableSpin, 10, 3000, "
            "TARGET_ENE_0, 3.5 - arg0:GetMapHitRadius(TARGET_SELF), 0, 0)") in out


def test_ninsatsu_branch_condition():
    seq = ComboSequence(
        name="phase", trigger_type="act_entry", trigger_id=1,
        steps=[
            Branch(terms=[ninsatsu("<=", 1)],
                   true_branch=[ComboStep("ComboFinal", 3092, 10, extra_args=[0])]),
        ],
    )
    out = generate_act(seq)
    assert "    if arg0:GetNinsatsuNum() <= 1 then" in out


def test_speffect_and_compound_condition():
    seq = ComboSequence(
        name="cmp", trigger_type="act_entry", trigger_id=1,
        steps=[
            Branch(terms=[randam(50), speffect("TARGET_ENE_0", 110030)],
                   connective="and",
                   true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])]),
        ],
    )
    out = generate_act(seq)
    assert ("if arg0:GetRandam_Int(1, 100) <= 50 and "
            "arg0:HasSpecialEffectId(TARGET_ENE_0, 110030) then") in out


def test_negated_speffect():
    seq = ComboSequence(
        name="neg", trigger_type="act_entry", trigger_id=1,
        steps=[
            Branch(terms=[speffect("TARGET_SELF", 200004, negate=True)],
                   true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])]),
        ],
    )
    assert "if not arg0:HasSpecialEffectId(TARGET_SELF, 200004) then" in generate_act(seq)


def test_value_receiver_convention():
    from generator import generate_kengeki_move
    terms = [state(0, 1), ninsatsu("<=", 1), speffect("TARGET_SELF", 200050)]
    # Act & Kengeki-move read values via arg0
    for gen, ttype in ((generate_act, "act_entry"), (generate_kengeki_move, "kengeki_move")):
        seq = ComboSequence(name="x", trigger_type=ttype, trigger_id=1,
                            steps=[Branch(terms=terms,
                                          true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])])])
        out = gen(seq)
        assert "arg0:GetNumber(0) == 1" in out
        assert "arg0:GetNinsatsuNum() <= 1" in out
        assert "arg0:HasSpecialEffectId(TARGET_SELF, 200050)" in out
        assert "arg1:AddSubGoal" in out           # step object is arg1
    # Interrupt reads values via arg1, step object is arg2
    iseq = ComboSequence(name="i", trigger_type="special_effect", trigger_id=5031,
                         steps=[Branch(terms=terms,
                                       true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])])])
    iout = generate_interrupt_branch(iseq)
    assert "arg1:GetNumber(0) == 1" in iout
    assert "arg1:GetNinsatsuNum() <= 1" in iout
    assert "arg1:HasSpecialEffectId(TARGET_SELF, 200050)" in iout
    assert "arg2:AddSubGoal" in iout


def test_raw_branch_condition():
    seq = ComboSequence(
        name="rawcond", trigger_type="act_entry", trigger_id=9,
        steps=[
            Branch(terms=[raw("getDist >= 4.5")],
                   true_branch=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])]),
        ],
    )
    out = generate_act(seq)
    assert "    if getDist >= 4.5 then" in out


def test_needs_registration_matches_target_and_id():
    existing = "    arg1:AddObserveSpecialEffectAttribute(TARGET_SELF, 5025)\n"
    # exact pair already present -> no new line needed
    assert needs_registration(5025, "TARGET_SELF", existing) is False
    # same id, different target -> still needs its own registration
    assert needs_registration(5025, "TARGET_ENE_0", existing) is True
    # different id -> needs registration
    assert needs_registration(9999, "TARGET_SELF", existing) is True
