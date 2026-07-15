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


TEE, ELBOW, PIPE, GAP = "├─ ", "└─ ", "│  ", "   "


def _step_leaf(step: ComboStep):
    if step.goal_type == "ComboAttackTunableSpin":
        note = "◀ spin"
    elif step.goal_type == "ComboFinal":
        note = "◀ final"
    else:
        note = None
    return f"[{step.anim_id}]  {step.goal_type}", note


def _weight_leaf(weight: Weight):
    return f"kengeki[{weight.index}] = {weight.value}", None


def _act_weight_leaf(weight: Weight):
    return f"act[{weight.index}] = {weight.value}", None


def _randam_percent(arm: Branch):
    """The `randam <= N` threshold of an arm tested on nothing else, else None."""
    if len(arm.terms) != 1:
        return None
    term = arm.terms[0]
    if isinstance(term, BoolNode) or getattr(term, "kind", None) != "randam":
        return None
    return None if term.negate else term.threshold


def _rows(items, leaf_fn):
    """One level as display rows: (label, children | None, note)."""
    out = []
    for item in items:
        if isinstance(item, Branch):
            arms, else_items = unchain_branch(item, items)
            pct = _randam_percent(arms[0][0])   # unchain yields (arm, its list)
            for k, (arm, _lst) in enumerate(arms):
                # Only the leading `if` gets a chance label. An elseif's odds
                # depend on whether it re-rolls or reuses the roll the `if`
                # already failed — the model doesn't record which, so any number
                # here would be a guess.
                note = f"{pct}%" if k == 0 and pct is not None else None
                out.append((f"{'if' if k == 0 else 'elseif'} {condition_text(arm)}",
                            arm.true_branch, note))
            if else_items:
                note = f"{100 - pct}%" if pct is not None and len(arms) == 1 else None
                out.append(("else", else_items, note))
        else:
            text, note = leaf_fn(item)
            out.append((text, None, note))
    return out


def _render_rows(rows, prefix: str, out: list, leaf_fn, top: bool = False) -> None:
    for i, (label, children, note) in enumerate(rows):
        last = i == len(rows) - 1
        if top and children:
            out.append(("│", None))     # a breather before each top-level block
        out.append((prefix + (ELBOW if last else TEE) + label, note))
        if children is not None:
            _render_rows(_rows(children, leaf_fn), prefix + (GAP if last else PIPE),
                         out, leaf_fn)


def _render_ladder(items, lines: list[str], leaf_fn) -> None:
    """Render list[<leaf> | Branch] as a ladder under `lines[0]` (the header)."""
    rows = []
    _render_rows(_rows(items, leaf_fn), "", rows, leaf_fn, top=True)
    lines.extend(_align(rows))


def _align(rows) -> list[str]:
    """Put the notes in one column, without trailing space on unnoted rows."""
    width = max((len(text) for text, note in rows if note), default=0)
    return [f"{text.ljust(width)}  {note}" if note else text for text, note in rows]


def visualize(seq: ComboSequence) -> str:
    """Return a ladder text diagram of the whole combo."""
    if seq.trigger_type == "act_entry":
        trigger = f"Act{seq.trigger_id:02d}"
    elif seq.trigger_type == "kengeki_move":
        trigger = f"Kengeki{seq.trigger_id:02d}"
    else:
        trigger = f"SpecialEffect {seq.trigger_id}"
    lines = [f"{seq.name}  ({trigger})"]
    _render_ladder(seq.steps, lines, _step_leaf)
    return "\n".join(lines)


def visualize_kengeki(activator: KengekiActivator) -> str:
    """Ladder diagram of a Kengeki_Activate selector."""
    top = [(f"effect {block.effect_id}", block.items, None)
           for block in activator.blocks]
    if activator.extra_items:
        top.append(("after all effects", activator.extra_items, None))
    rows = []
    _render_rows(top, "", rows, _weight_leaf, top=True)
    return "\n".join(["Kengeki_Activate"] + _align(rows))


def visualize_act_activator(activator: ActActivator) -> str:
    """Ladder diagram of Goal.Activate's act-weight region."""
    lines = ["Activate — act weights"]
    _render_ladder(activator.items, lines, _act_weight_leaf)
    return "\n".join(lines)
