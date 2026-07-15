"""Tests for the weight selectors (Goal.Activate / Goal.Kengeki_Activate).

The guarantee these pin down: editing a weight table only ever rewrites the
weight lines it owns. The regions contain comments and statements the model
can't represent, so anything that regenerated them would quietly delete those.
"""

import copy
import re

import pytest

import writer
from models import ActActivator, KengekiActivator, Weight

# things inside the regions that only survive because we splice single lines
FRAGILE = [
    "        -- elseif getDist >= 7 and arg1:GetNumber(7) == 1 and arg1:GetNumber(10) == 0 then",
    "        --     act[49] = 50",
    "        arg1:SetNumber(2, 0)",                       # not a weight, not a branch
    "    act[1] = SetCoolTime(arg1, arg2, 3000, 15, act[1], 1)",
    "    local1[49] = REGIST_FUNC(arg1, arg2, arg0.Act49)",
]


@pytest.fixture
def act(parsed):
    return copy.deepcopy(
        next(a for a in parsed.activators if isinstance(a, ActActivator)))


@pytest.fixture
def kengeki(parsed):
    return copy.deepcopy(
        next(a for a in parsed.activators if isinstance(a, KengekiActivator)))


def _weight_at(activator, line):
    return next(w for w in writer._activator_parts(activator)[1] if w.line == line)


# --- parsing ----------------------------------------------------------------

def test_activate_weights_are_parsed_with_file_line_numbers(act):
    weights = writer._flatten_weights(act.items)
    assert len(weights) == 70
    assert all(w.line is not None for w in weights)
    # act[21] = 1 is the first weight, on line 63 of the reference
    assert (weights[0].index, weights[0].value, weights[0].line) == (21, 1, 63)


def test_both_selectors_are_parsed(parsed):
    kinds = {type(a).__name__ for a in parsed.activators}
    assert kinds == {"ActActivator", "KengekiActivator"}


def test_owned_lines_exclude_cooldown_lines(kengeki, act, text):
    """`kengeki[1] = SetCoolTime(...)` has the shape of a weight but is a
    cooldown. Claiming it would let a write mangle the cooldown block."""
    lines = text.split("\n")
    for activator, table in ((kengeki, "kengeki"), (act, "act")):
        cooldown = next(n for n, line in enumerate(lines, 1)
                        if line.strip().startswith(f"{table}[1] = SetCoolTime("))
        assert cooldown not in activator.owned_lines


def test_kengeki_veto_blocks_are_parsed_and_owned(kengeki, text):
    """The `if ... then kengeki[x] = 0 end` blocks after the local0 chain are
    weights too, so they are editable rather than merely left alone."""
    assert len(kengeki.extra_items) == 3
    veto_line = next(n for n, line in enumerate(text.split("\n"), 1)
                     if line.strip() == "kengeki[3] = 0")
    assert veto_line in kengeki.owned_lines
    assert any(w.line == veto_line
               for w in writer._flatten_weights(kengeki.extra_items))


def test_editing_a_kengeki_veto_weight_touches_only_that_line(kengeki, text):
    veto_line = next(n for n, line in enumerate(text.split("\n"), 1)
                     if line.strip() == "kengeki[3] = 0")
    _weight_at(kengeki, veto_line).value = 7
    out, summary = writer.apply_activator(text, kengeki)
    before, after = text.split("\n"), out.split("\n")
    assert [i + 1 for i in range(len(before)) if before[i] != after[i]] == [veto_line]
    assert after[veto_line - 1].strip() == "kengeki[3] = 7"
    assert summary == [f"kengeki[3] = 7 (was 0, line {veto_line})"]


# --- writing: the core guarantee -------------------------------------------

@pytest.mark.parametrize("which", ["act", "kengeki"])
def test_unedited_write_is_byte_for_byte_identical(text, act, kengeki, which):
    activator = act if which == "act" else kengeki
    out, summary = writer.apply_activator(text, activator)
    assert out == text
    assert summary == []


@pytest.mark.parametrize("which", ["act", "kengeki"])
def test_editing_a_weight_leaves_everything_else_intact(text, act, kengeki, which):
    activator = act if which == "act" else kengeki
    line = min(activator.owned_lines)
    _weight_at(activator, line).value = 999
    out, _ = writer.apply_activator(text, activator)
    before, after = text.split("\n"), out.split("\n")
    assert len(before) == len(after)
    assert [i + 1 for i in range(len(before)) if before[i] != after[i]] == [line]
    for fragile in FRAGILE:
        assert fragile in out


def test_edit_targets_the_right_line_when_the_weight_is_duplicated(text, act):
    """`act[21] = 100` sits on two lines; editing one must not touch the other."""
    dupes = [n for n, line in enumerate(text.split("\n"), 1)
             if line.strip() == "act[21] = 100"]
    assert len(dupes) == 2, "the reference should still have this duplicate"
    _weight_at(act, dupes[0]).value = 55
    after = writer.apply_activator(text, act)[0].split("\n")
    assert after[dupes[0] - 1].strip() == "act[21] = 55"
    assert after[dupes[1] - 1].strip() == "act[21] = 100"


def test_removing_a_weight_drops_exactly_its_line(text, act):
    line = min(act.owned_lines)

    def drop(items):
        for i, it in enumerate(items):
            if isinstance(it, Weight) and it.line == line:
                del items[i]
                return True
            if hasattr(it, "true_branch") and (drop(it.true_branch) or drop(it.false_branch)):
                return True
        return False

    assert drop(act.items)
    out, summary = writer.apply_activator(text, act)
    assert len(out.split("\n")) == len(text.split("\n")) - 1
    assert any("remove act[21]" in s for s in summary)


def test_added_weight_is_inserted_beside_its_block(text, act):
    """A new weight (line=None) lands after the last existing weight of the
    same block, so it stays under the same condition."""
    anchor = _weight_at(act, 63)

    def holder(items):
        for it in items:
            if it is anchor:
                return items
            if hasattr(it, "true_branch"):
                found = holder(it.true_branch) or holder(it.false_branch)
                if found:
                    return found
        return None

    holder(act.items).append(Weight(index=99, value=42))
    out, summary = writer.apply_activator(text, act)
    lines = out.split("\n")
    assert len(lines) == len(text.split("\n")) + 1
    assert any("add act[99] = 42" in s for s in summary)
    added = next(i for i, line in enumerate(lines) if line.strip() == "act[99] = 42")
    assert lines[added].startswith("            ")     # indent of its block
    assert lines[added - 1].strip() == "act[28] = 100"  # right after its sibling


# --- previews render the whole model ---------------------------------------

@pytest.mark.parametrize("render", ["lua", "diagram"])
def test_kengeki_preview_renders_every_weight_including_vetoes(kengeki, render):
    """Counting rather than spot-checking: the previews once iterated only
    `blocks`, so the 14 weights in `extra_items` were silently missing."""
    import generator
    import visualizer

    out = (generator.generate_kengeki_activate(kengeki) if render == "lua"
           else visualizer.visualize_kengeki(kengeki))
    rendered = len(re.findall(r"kengeki[\[ ]\d+[\]]? =", out))
    assert rendered == len(writer._activator_parts(kengeki)[1]) == 142
    assert "kengeki[3] = 0" in out or "kengeki 3 = 0" in out


def test_kengeki_lua_keeps_the_veto_conditions(kengeki):
    import generator
    lua = generator.generate_kengeki_activate(kengeki)
    assert "200051" in lua                     # the veto's condition
    chain_end = lua.index("\nend\n")           # vetoes come after the chain
    assert lua.index("200051") > chain_end


def test_kengeki_lua_renders_vetoes_even_without_any_blocks(kengeki):
    """The early `return ""` for an empty chain used to swallow them."""
    import generator
    from models import KengekiActivator
    only_vetoes = KengekiActivator(blocks=[], extra_items=kengeki.extra_items)
    assert "kengeki[3] = 0" in generator.generate_kengeki_activate(only_vetoes)


def test_write_is_refused_when_the_model_no_longer_matches_the_file(text, act):
    """Guards against splicing into a file that changed under us."""
    _weight_at(act, 63).index = 999
    out, summary = writer.apply_activator(text, act)
    assert out == text
    assert "reload the file" in summary[0]


def test_apply_sequence_routes_activators(text, act):
    """The generic entry point used by the UI no longer refuses selectors."""
    _weight_at(act, 63).value = 7
    out, summary = writer.apply_sequence(text, act)
    assert out != text
    assert any("act[21] = 7" in s for s in summary)
