"""Tests for visualizer.py — ladder diagram output."""

from models import (
    Branch, ComboSequence, ComboStep,
    randam, state, ninsatsu, speffect,
)
from visualizer import visualize


def _depth(line: str) -> int:
    """Nesting level of a row, read from its box-drawing prefix.

    Rows start with the prefix, not spaces, so len() - len(lstrip()) says
    nothing; each level is one 3-char run ("│  " / "   ") before the ├─ / └─.
    """
    marker = min((line.index(c) for c in "├└" if c in line), default=None)
    assert marker is not None, f"not a tree row: {line!r}"
    return marker // 3


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
    assert "[3009]  ComboAttackTunableSpin" in out
    assert "[3007]  ComboFinal" in out
    # the opener and the finisher are called out
    assert "◀ spin" in out and "◀ final" in out
    # a flat combo has no branches, so no spacer rows
    assert "│" not in out


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
    assert "[3049]  ComboFinal" in out
    assert "[3041]  ComboFinal" in out
    # a plain random if/else: both odds are unambiguous
    assert "50%" in out


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
    assert _depth(if_line) == _depth(elseif_line)  # same level, not nested in false


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
    assert _depth(inner) > _depth(outer)          # deeper, inside the else
    assert "elseif randam <= 33" not in visualize(seq)  # shown as `if`, not elseif
    assert any("─ else" in l for l in lines)      # there is an else wrapper row


def test_random_if_else_is_labelled_with_both_odds():
    seq = ComboSequence(
        name="c", trigger_type="act_entry", trigger_id=1,
        steps=[Branch(terms=[randam(30)],
                      true_branch=[ComboStep("ComboFinal", 1, 10)],
                      false_branch=[ComboStep("ComboFinal", 2, 10)])])
    lines = visualize(seq).splitlines()
    assert next(l for l in lines if "if randam <= 30" in l).endswith("30%")
    assert next(l for l in lines if "else" in l).endswith("70%")


def test_elseif_arms_are_not_labelled_with_odds():
    """An elseif's real odds depend on whether it re-rolls or reuses the roll the
    `if` already failed. The model doesn't record which, so we must not guess."""
    seq = ComboSequence(
        name="c", trigger_type="act_entry", trigger_id=1,
        steps=[Branch(terms=[randam(30)],
                      true_branch=[ComboStep("ComboFinal", 1, 10)],
                      false_branch=[Branch(terms=[randam(50)], from_elseif=True,
                                           true_branch=[ComboStep("ComboFinal", 2, 10)],
                                           false_branch=[ComboStep("ComboFinal", 3, 10)])])])
    lines = visualize(seq).splitlines()
    assert next(l for l in lines if "if randam <= 30" in l).endswith("30%")
    assert not next(l for l in lines if "elseif randam <= 50" in l).endswith("%")
    # nor the else, since it no longer simply complements the leading if
    assert not any(l.rstrip().endswith("70%") for l in lines)
    assert not any(l.rstrip().endswith("50%") for l in lines)


def test_non_random_conditions_get_no_odds():
    seq = ComboSequence(
        name="c", trigger_type="act_entry", trigger_id=1,
        steps=[Branch(terms=[state(7, 0)],
                      true_branch=[ComboStep("ComboFinal", 1, 10)],
                      false_branch=[ComboStep("ComboFinal", 2, 10)])])
    assert "%" not in visualize(seq)


def test_compound_random_condition_gets_no_odds():
    """`randam <= 50 and HasSpEffect(...)` is not a 50% chance."""
    seq = ComboSequence(
        name="c", trigger_type="act_entry", trigger_id=1,
        steps=[Branch(terms=[randam(50), speffect("TARGET_SELF", 200050)],
                      connective="and",
                      true_branch=[ComboStep("ComboFinal", 1, 10)],
                      false_branch=[ComboStep("ComboFinal", 2, 10)])])
    assert "%" not in visualize(seq)


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
