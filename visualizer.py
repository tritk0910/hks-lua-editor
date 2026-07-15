"""Render a `ComboSequence` (or Kengeki_Activate) as a text diagram.

UI-agnostic: returns a plain string so it can be printed to a terminal, put
in a Qt widget, or asserted on in tests.

Layout is a **ladder**: steps at a given level share the same indentation, and
an `if / elseif / else` chain is shown at ONE level (elseif is not buried
inside the previous branch's false side). Each arm's body is indented one
level under it.
"""

from __future__ import annotations

from models import (
    ActActivator,
    BoolNode,
    Branch,
    ComboSequence,
    ComboStep,
    KengekiActivator,
    Weight,
    unchain_branch,
)

INDENT = "    "  # 4 spaces per nesting level


def _term_text(term) -> str:
    if term.kind == "randam":
        core = f"randam <= {term.threshold}"
    elif term.kind == "state":
        core = f"GetNumber({term.state_index}) == {term.state_value}"
    elif term.kind == "ninsatsu":
        core = f"ninsatsu {term.operator or '<='} {term.threshold}"
    elif term.kind == "speffect":
        tgt = "SELF" if term.target == "TARGET_SELF" else "ENE"
        core = f"HasSpEffect({tgt}, {term.effect_id})"
    else:
        core = term.raw or "raw"
    return f"not {core}" if term.negate else core


def _cond_item_text(item) -> str:
    if isinstance(item, BoolNode):
        inner = f" {item.op} ".join(_cond_item_text(c) for c in item.terms)
        return f"not ({inner})" if item.negate else f"({inner})"
    return _term_text(item)


def condition_text(branch: Branch) -> str:
    """Human-readable condition for a branch's if/elseif (terms + nested groups)."""
    return f" {branch.connective} ".join(_cond_item_text(t) for t in branch.terms)


def _step_leaf(step: ComboStep) -> str:
    return f"[{step.anim_id} {step.goal_type}]"


def _weight_leaf(weight: Weight) -> str:
    return f"[kengeki {weight.index} = {weight.value}]"


def _act_weight_leaf(weight: Weight) -> str:
    return f"[act {weight.index} = {weight.value}]"


def _render_ladder(items, depth: int, lines: list[str], leaf_fn) -> None:
    """Render list[<leaf> | Branch] as a ladder. `leaf_fn` formats a leaf."""
    pad = INDENT * depth
    for item in items:
        if isinstance(item, Branch):
            arms, else_items = unchain_branch(item, items)
            for k, (arm, _lst) in enumerate(arms):
                kw = "if" if k == 0 else "elseif"
                lines.append(f"{pad}{kw} {condition_text(arm)}")
                _render_ladder(arm.true_branch, depth + 1, lines, leaf_fn)
            if else_items:
                lines.append(f"{pad}else")
                _render_ladder(else_items, depth + 1, lines, leaf_fn)
        else:
            lines.append(f"{pad}{leaf_fn(item)}")


def visualize(seq: ComboSequence) -> str:
    """Return a ladder text diagram of the whole combo."""
    if seq.trigger_type == "act_entry":
        trigger = f"Act{seq.trigger_id:02d}"
    elif seq.trigger_type == "kengeki_move":
        trigger = f"Kengeki{seq.trigger_id:02d}"
    else:
        trigger = f"SpecialEffect {seq.trigger_id}"
    lines = [f"{seq.name}  ({trigger})"]
    _render_ladder(seq.steps, depth=1, lines=lines, leaf_fn=_step_leaf)
    return "\n".join(lines)


def visualize_kengeki(activator: KengekiActivator) -> str:
    """Ladder diagram of a Kengeki_Activate selector."""
    lines = ["Kengeki_Activate"]
    for block in activator.blocks:
        lines.append(f"{INDENT}effect {block.effect_id}:")
        _render_ladder(block.items, depth=2, lines=lines, leaf_fn=_weight_leaf)
    if activator.extra_items:
        lines.append(f"{INDENT}after all effects:")
        _render_ladder(activator.extra_items, depth=2, lines=lines,
                       leaf_fn=_weight_leaf)
    return "\n".join(lines)


def visualize_act_activator(activator: ActActivator) -> str:
    """Ladder diagram of Goal.Activate's act-weight region."""
    lines = ["Activate — act weights"]
    _render_ladder(activator.items, depth=1, lines=lines, leaf_fn=_act_weight_leaf)
    return "\n".join(lines)
