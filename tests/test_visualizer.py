"""Tests for visualizer.py — ladder diagram output."""

from models import (
    Branch, ComboSequence, ComboStep,
    randam, state, ninsatsu, speffect,
)
from visualizer import visualize


def test_visualize_flat_chain():
    seq = ComboSequence(
        name="simple", trigger_type="act_entry", trigger_id=4,
        steps=[
            ComboStep("ComboAttackTunableSpin", 3009, 10),
            ComboStep("ComboFinal", 3007, 10),
        ],
    )
    out = visualize(seq)
    assert "simple  (Act04)" in out
    assert "[3009 ComboAttackTunableSpin]" in out
    assert "[3007 ComboFinal]" in out


def test_visualize_randam_ladder():
    seq = ComboSequence(
        name="kick", trigger_type="special_effect", trigger_id=5031,
        steps=[
            Branch(
                terms=[randam(50)],
                true_branch=[ComboStep("ComboFinal", 3049, 10)],
                false_branch=[ComboStep("ComboFinal", 3041, 10)],
            ),
        ],
    )
    out = visualize(seq)
    assert "kick  (SpecialEffect 5031)" in out
    assert "if randam <= 50" in out
    assert "else" in out
    assert "[3049 ComboFinal]" in out
    assert "[3041 ComboFinal]" in out


def test_visualize_elseif_chain_same_level():
    # A real elseif (from_elseif=True) must sit at the SAME indent as its if
    seq = ComboSequence(
        name="chain", trigger_type="act_entry", trigger_id=1,
        steps=[
            Branch(
                terms=[randam(50)],
                true_branch=[ComboStep("ComboFinal", 3001, 10)],
                false_branch=[Branch(
                    terms=[randam(33)], from_elseif=True,
                    true_branch=[ComboStep("ComboFinal", 3002, 10)],
                    false_branch=[ComboStep("ComboFinal", 3003, 10)],
                )],
            ),
        ],
    )
    lines = visualize(seq).splitlines()
    if_line = next(l for l in lines if "if randam <= 50" in l)
    elseif_line = next(l for l in lines if "elseif randam <= 33" in l)
    indent = lambda s: len(s) - len(s.lstrip())
    assert indent(if_line) == indent(elseif_line)  # same level, not nested in false


def test_visualize_nested_else_if_stays_deeper():
    # a nested `else { if }` (from_elseif=False) must NOT flatten — the inner
    # if sits one level deeper than the outer if (the Kengeki37 bug).
    seq = ComboSequence(
        name="nested", trigger_type="act_entry", trigger_id=1,
        steps=[
            Branch(
                terms=[randam(50)],
                true_branch=[ComboStep("ComboFinal", 3001, 10)],
                false_branch=[Branch(   # from_elseif defaults to False
                    terms=[randam(33)],
                    true_branch=[ComboStep("ComboFinal", 3002, 10)],
                    false_branch=[ComboStep("ComboFinal", 3003, 10)],
                )],
            ),
        ],
    )
    lines = visualize(seq).splitlines()
    outer = next(l for l in lines if "if randam <= 50" in l)
    inner = next(l for l in lines if "randam <= 33" in l)
    indent = lambda s: len(s) - len(s.lstrip())
    assert indent(inner) > indent(outer)          # deeper, inside the else
    assert "elseif randam <= 33" not in visualize(seq)  # shown as `if`, not elseif
    assert any(l.strip() == "else" for l in lines)  # there is an else wrapper


def test_visualize_state_and_ninsatsu_labels():
    seq = ComboSequence(
        name="stateful", trigger_type="special_effect", trigger_id=3710071,
        steps=[
            Branch(terms=[state(12, 0)],
                   true_branch=[ComboStep("ComboRepeat", 3006, 5)]),
            Branch(terms=[ninsatsu("<=", 1)],
                   true_branch=[ComboStep("ComboFinal", 3092, 10)]),
            Branch(terms=[randam(50), speffect("TARGET_SELF", 200050)], connective="and",
                   true_branch=[ComboStep("ComboFinal", 3010, 10)]),
        ],
    )
    out = visualize(seq)
    assert "if GetNumber(12) == 0" in out
    assert "if ninsatsu <= 1" in out
    assert "if randam <= 50 and HasSpEffect(SELF, 200050)" in out
