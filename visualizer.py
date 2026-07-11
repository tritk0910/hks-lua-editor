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
    Branch,
    ComboSequence,
    ComboStep,
    KengekiActivator,
    KengekiWeight,
    unchain_branch,
)

INDENT = "    "  # 4 spaces per nesting level


def condition_text(branch: Branch) -> str:
    """Human-readable condition for a branch's if/elseif."""
    if branch.kind == "randam_percent":
        return f"randam <= {branch.threshold}"
    if branch.kind == "state_check":
        return f"GetNumber({branch.state_index}) == {branch.state_value}"
    if branch.kind == "ninsatsu":
        return f"ninsatsu {branch.operator or '<='} {branch.threshold}"
    if branch.kind == "raw":
        return branch.raw_condition or "raw"
    return branch.kind


def _step_leaf(step: ComboStep) -> str:
    return f"[{step.anim_id} {step.goal_type}]"


def _weight_leaf(weight: KengekiWeight) -> str:
    return f"[kengeki {weight.index} = {weight.value}]"


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
    return "\n".join(lines)
