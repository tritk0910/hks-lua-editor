"""Tests for visualizer.py — node text and branch labels appear."""

from models import Branch, ComboSequence, ComboStep
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


def test_visualize_random_branch_labels():
    seq = ComboSequence(
        name="kick", trigger_type="special_effect", trigger_id=5031,
        steps=[
            Branch(
                kind="random_percent", threshold=50,
                true_branch=[ComboStep("ComboFinal", 3049, 10)],
                false_branch=[ComboStep("ComboFinal", 3041, 10)],
            ),
        ],
    )
    out = visualize(seq)
    assert "kick  (SpecialEffect 5031)" in out
    assert "<random 50%>" in out
    assert "<= 50%" in out
    assert "> 50%" in out
    assert "[3049 ComboFinal]" in out
    assert "[3041 ComboFinal]" in out


def test_visualize_state_check_label():
    seq = ComboSequence(
        name="stateful", trigger_type="special_effect", trigger_id=3710071,
        steps=[
            Branch(
                kind="state_check", threshold=0, state_index=12, state_value=0,
                true_branch=[ComboStep("ComboRepeat", 3006, 5)],
            ),
        ],
    )
    out = visualize(seq)
    assert "<GetNumber(12) == 0>" in out
    assert "(nothing)" in out  # empty false branch
