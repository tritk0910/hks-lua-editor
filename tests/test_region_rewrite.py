"""Editing a selector's ladder conditions, via a region rewrite that proves
itself first.

Before this, the tree let you edit a selector's condition, the Lua preview
showed the new condition, and the write silently did nothing.

Rewriting a region is only safe if the generator reproduces that region exactly
— otherwise it would also rewrite lines the user never touched. So the writer
checks that per file rather than trusting a hard-coded list of "safe" shapes,
and refuses (with a reason) when it can't prove it.
"""

import copy

import pytest

import writer
from models import ActActivator, KengekiActivator, RawLine, randam


@pytest.fixture
def act(parsed):
    return copy.deepcopy(
        next(a for a in parsed.activators if isinstance(a, ActActivator)))


@pytest.fixture
def kengeki(parsed):
    return copy.deepcopy(
        next(a for a in parsed.activators if isinstance(a, KengekiActivator)))


def _first_branch(activator):
    items = (activator.items if isinstance(activator, ActActivator)
             else activator.blocks[0].items)
    return next(i for i in items if hasattr(i, "terms"))


def _changed_lines(before, after):
    a, b = before.split("\n"), after.split("\n")
    if len(a) != len(b):
        return None
    return [i + 1 for i in range(len(a)) if a[i] != b[i]]


# --- the raw passthrough that makes the round-trip exact --------------------

def test_the_region_keeps_comments_and_unmodelled_statements(act):
    all_raw = []

    def walk(items):
        for it in items:
            if isinstance(it, RawLine):
                all_raw.append(it.text)
            elif hasattr(it, "true_branch"):
                walk(it.true_branch)
                walk(it.false_branch)

    walk(act.items)
    assert any("-- elseif getDist" in t for t in all_raw)
    assert any("arg1:SetNumber(2, 0)" in t for t in all_raw)


def test_activate_region_regenerates_byte_for_byte(text, act):
    """The whole point: render the parsed region and it IS the file."""
    first, last = writer._region_span(text, act)
    assert writer._render_region(act) == "\n".join(text.split("\n")[first - 1:last])


# --- the gate ---------------------------------------------------------------

def test_the_gate_passes_for_activate_and_is_honest_about_kengeki(text, act, kengeki):
    assert writer.region_is_faithful(text, act) is True
    # Kengeki_Activate is not reproduced exactly yet; the gate must say so
    # rather than let a rewrite loose on it.
    assert writer.region_is_faithful(text, kengeki) is False


@pytest.mark.parametrize("which", ["act", "kengeki"])
def test_an_untouched_selector_still_writes_nothing(text, act, kengeki, which):
    out, summary = writer.apply_activator(text, act if which == "act" else kengeki)
    assert out == text
    assert summary == []


# --- editing a condition ----------------------------------------------------

def test_editing_a_condition_changes_only_that_line(text, act):
    branch = _first_branch(act)
    line = branch.line
    branch.terms = [randam(42)]
    branch.connective = "and"
    out, summary = writer.apply_activator(text, act)

    assert out != text, "the edit must reach the file"
    assert any("structure" in s for s in summary)
    assert _changed_lines(text, out) == [line]
    assert "arg1:GetRandam_Int(1, 100) <= 42" in out.split("\n")[line - 1]


def test_editing_a_condition_keeps_the_lines_we_cannot_model(text, act):
    branch = _first_branch(act)
    branch.terms = [randam(42)]
    out, _ = writer.apply_activator(text, act)
    assert "-- elseif getDist >= 7" in out          # a comment inside the ladder
    assert "arg1:SetNumber(2, 0)" in out            # an un-modelled statement
    assert "act[1] = SetCoolTime(arg1, arg2, 3000, 15, act[1], 1)" in out
    assert "local1[49] = REGIST_FUNC(arg1, arg2, arg0.Act49)" in out


def test_a_structural_edit_is_refused_when_it_cannot_be_proven_safe(text, kengeki):
    """Refusing beats both alternatives: silently dropping the edit (what it
    used to do) and rewriting a region we can't reproduce (corruption)."""
    branch = _first_branch(kengeki)
    branch.terms = [randam(42)]
    out, summary = writer.apply_activator(text, kengeki)
    assert out == text
    assert "can't be written" in summary[0] and "Weight values still save" in summary[0]


# --- weights keep their own, proven path ------------------------------------

@pytest.mark.parametrize("which", ["act", "kengeki"])
def test_weight_edits_still_use_the_line_splice(text, act, kengeki, which):
    """A weight change must not be routed through the gated region rewrite —
    that would refuse for kengeki, where the splice works fine."""
    activator = act if which == "act" else kengeki
    weight = sorted(writer._activator_parts(activator)[1], key=lambda w: w.line)[0]
    weight.value = 777
    out, summary = writer.apply_activator(text, activator)
    assert _changed_lines(text, out) == [weight.line]
    assert "777" in summary[0] and "structure" not in summary[0]


def test_adding_a_weight_is_not_treated_as_a_structural_edit(text, kengeki):
    """Adding a weight must keep using the splice — routing it through the gate
    would refuse it here, where the splice works."""
    from models import Weight

    def list_holding_a_weight(items):
        if any(isinstance(i, Weight) and i.line for i in items):
            return items
        for it in items:
            if hasattr(it, "true_branch"):
                found = (list_holding_a_weight(it.true_branch)
                         or list_holding_a_weight(it.false_branch))
                if found:
                    return found
        return None

    holder = list_holding_a_weight(kengeki.blocks[0].items)
    holder.append(Weight(index=99, value=5))
    out, summary = writer.apply_activator(text, kengeki)
    assert out != text                       # the splice path handled it
    assert any("add kengeki[99]" in s for s in summary)
    assert not any("structure" in s for s in summary)
