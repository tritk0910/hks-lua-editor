"""Turn a `ComboSequence` into valid Sekiro Lua (HKS) text.

UI-agnostic: this module only knows the data model (`models.py`) and Lua
syntax. It never imports any UI toolkit. Everything here returns Lua as
plain strings — it does NOT splice into a whole `.lua` file (that is a later
slice). All syntax choices are anchored to the reference file
`710300_battle.lua`; see the plan / CLAUDE.md for line references.
"""

from __future__ import annotations

import re

from models import (
    BoolNode,
    Branch,
    ComboSequence,
    ComboStep,
    KengekiActivator,
    KengekiWeight,
    unchain_branch,
)

INDENT = "    "  # 4 spaces, matching the reference file


# --- small helpers ---------------------------------------------------------

def goal_type_to_lua(short: str) -> str:
    """Map a model goal_type ("ComboFinal") to its Lua constant.

    If the caller already passed a fully-qualified constant (starts with
    "GOAL_"), leave it untouched so unusual/rare types still work.
    """
    if short.startswith("GOAL_"):
        return short
    return f"GOAL_COMMON_{short}"


def _fmt_arg(value) -> str:
    """Render a single positional arg. Ints/strings are passed through as-is;
    strings are assumed to be Lua expressions/identifiers (e.g. "local8"),
    NOT quoted string literals — that matches how args appear in the file.
    """
    return str(value)


# --- rendering a single step ----------------------------------------------

def render_step(step: ComboStep, receiver: str, indent: str = "") -> str:
    """One `AddSubGoal` line.

    `receiver` is "arg1" (Act combos) or "arg2" (Interrupt combos).

    Real signature order (from 710300_battle.lua, e.g. line 274):
        AddSubGoal(GOAL_TYPE, priority, animID, target, distance, ...extras)
    Note this differs from ComboStep's field order (anim_id before priority).
    `distance` is the 5th positional arg (a distance/range gate, not a
    duration); 9999 means "no limit".
    """
    args = [
        goal_type_to_lua(step.goal_type),
        _fmt_arg(step.priority),
        _fmt_arg(step.anim_id),
        _fmt_arg(step.target),
        _fmt_arg(step.distance),
    ]
    args.extend(_fmt_arg(a) for a in step.extra_args)
    return f"{indent}{receiver}:AddSubGoal({', '.join(args)})"


# --- rendering a branch split ----------------------------------------------

def _value_receiver(ctx: str) -> str:
    """Which arg reads params/values. Act & Kengeki-move use arg0 (arg1 is the
    step object); Interrupt & Kengeki_Activate use arg1 (arg2 is the step)."""
    return "arg0" if ctx == "act" else "arg1"


def _term_lua(term, ctx: str) -> str:
    """The Lua for a single condition Term. The value receiver depends on ctx
    (see _value_receiver); randam in the interrupt uses the `randam` local."""
    val = _value_receiver(ctx)
    if term.kind == "randam":
        core = (f"randam <= {term.threshold}" if ctx == "interrupt"
                else f"{val}:GetRandam_Int(1, 100) <= {term.threshold}")
    elif term.kind == "state":
        core = f"{val}:GetNumber({term.state_index}) == {term.state_value}"
    elif term.kind == "ninsatsu":
        core = f"{val}:GetNinsatsuNum() {term.operator or '<='} {term.threshold}"
    elif term.kind == "speffect":
        core = f"{val}:HasSpecialEffectId({term.target}, {term.effect_id})"
    elif term.kind == "raw":
        core = term.raw or ""
    else:
        raise ValueError(f"unknown term kind: {term.kind!r}")
    return f"not {core}" if term.negate else core


def _cond_item_lua(item, ctx: str) -> str:
    """Render one condition element — a Term or a parenthesised BoolNode group."""
    if isinstance(item, BoolNode):
        inner = f" {item.op} ".join(_cond_item_lua(c, ctx) for c in item.terms)
        return f"not ({inner})" if item.negate else f"({inner})"
    return _term_lua(item, ctx)


def _branch_condition(branch: Branch, ctx: str) -> str:
    """The Lua boolean expression for a branch's `if` — terms (possibly nested
    BoolNode groups) joined by the branch connective (`and`/`or`)."""
    return f" {branch.connective} ".join(_cond_item_lua(t, ctx) for t in branch.terms)


def render_items(items, receiver: str, ctx: str, indent: str = "") -> str:
    """Render an ordered list of ComboStep | Branch into Lua lines.

    Recurses into branch true/false lists, deepening the indent by one level.
    Returns a string with no trailing newline.
    """
    lines: list[str] = []
    for item in items:
        if isinstance(item, ComboStep):
            lines.append(render_step(item, receiver, indent))
        elif isinstance(item, Branch):
            # ladder: emit if / elseif (from_elseif arms) / else — matches the
            # tree + visualizer so generated Lua reads the same as the diagram.
            arms, else_items = unchain_branch(item, items)
            for k, (arm, _lst) in enumerate(arms):
                kw = "if" if k == 0 else "elseif"
                lines.append(f"{indent}{kw} {_branch_condition(arm, ctx)} then")
                body = render_items(arm.true_branch, receiver, ctx, indent + INDENT)
                if body:
                    lines.append(body)
            if else_items:
                lines.append(f"{indent}else")
                lines.append(render_items(else_items, receiver, ctx, indent + INDENT))
            lines.append(f"{indent}end")
        else:
            raise TypeError(f"combo item must be ComboStep or Branch, got {type(item)!r}")
    return "\n".join(lines)


# --- full-combo generators -------------------------------------------------

def generate_act(seq: ComboSequence) -> str:
    """A complete `Goal.ActNN` function for an act_entry combo.

    NN is zero-padded to two digits from seq.trigger_id (Act01, Act15, ...).
    """
    if seq.trigger_type != "act_entry":
        raise ValueError("generate_act expects trigger_type == 'act_entry'")
    name = f"Goal.Act{seq.trigger_id:02d}"
    lines = []
    if seq.approach is not None:
        params = ", ".join(_fmt_arg(p) for p in seq.approach)
        lines.append(f"{INDENT}Approach_Act_Flex(arg0, arg1, {params})")
    lines.append(render_items(seq.steps, receiver="arg1", ctx="act", indent=INDENT))
    body = "\n".join(lines)
    return (
        f"{name} = function(arg0, arg1, arg2)\n"
        f"{body}\n"
        f"{INDENT}GetWellSpace_Odds = 100\n"
        f"{INDENT}return GetWellSpace_Odds\n"
        f"end"
    )


def generate_interrupt_branch(seq: ComboSequence) -> str:
    """An `elseif interruptEffectIdentifier == <id> then` block for an
    Interrupt combo. Starts with `arg2:ClearSubGoal()`, uses the arg2 receiver.

    Emitted at 8-space base indent to match the nesting inside
    `Goal.Interrupt` (inside the `if IsInterupt(...)` block, e.g. line 911).
    """
    if seq.trigger_type != "special_effect":
        raise ValueError("generate_interrupt_branch expects trigger_type == 'special_effect'")
    base = INDENT * 2          # inside if IsInterupt(...) then
    inner = INDENT * 3         # body of this elseif branch
    body = render_items(seq.steps, receiver="arg2", ctx="interrupt", indent=inner)
    return (
        f"{base}elseif interruptEffectIdentifier == {seq.trigger_id} then\n"
        f"{inner}arg2:ClearSubGoal()\n"
        f"{body}"
    )


def generate_kengeki_move(seq: ComboSequence) -> str:
    """A complete `Goal.KengekiNN` move function for a kengeki_move combo.

    Structurally like an Act combo but: starts with `arg1:ClearSubGoal()`,
    uses the arg1 receiver, and has NO `GetWellSpace_Odds` wrapper. NN is
    zero-padded from seq.trigger_id (Kengeki01, Kengeki43, ...). See
    710300_battle.lua line 1573 (Goal.Kengeki01).
    """
    if seq.trigger_type != "kengeki_move":
        raise ValueError("generate_kengeki_move expects trigger_type == 'kengeki_move'")
    name = f"Goal.Kengeki{seq.trigger_id:02d}"
    body = render_items(seq.steps, receiver="arg1", ctx="act", indent=INDENT)
    return (
        f"{name} = function(arg0, arg1, arg2)\n"
        f"{INDENT}arg1:ClearSubGoal()\n"
        f"{body}\n"
        f"end"
    )


# --- Kengeki_Activate selector --------------------------------------------

def _render_kengeki_items(items, indent: str) -> str:
    """Render list[KengekiWeight | Branch] into Lua lines (mirrors render_items
    but the leaves are `kengeki[index] = value` assignments)."""
    lines = []
    for item in items:
        if isinstance(item, KengekiWeight):
            lines.append(f"{indent}kengeki[{item.index}] = {item.value}")
        elif isinstance(item, Branch):
            # Kengeki_Activate reads values via arg1 (arg2 is the step object)
            arms, else_items = unchain_branch(item, items)
            for k, (arm, _lst) in enumerate(arms):
                kw = "if" if k == 0 else "elseif"
                lines.append(f"{indent}{kw} {_branch_condition(arm, 'kengeki_activate')} then")
                body = _render_kengeki_items(arm.true_branch, indent + INDENT)
                if body:
                    lines.append(body)
            if else_items:
                lines.append(f"{indent}else")
                lines.append(_render_kengeki_items(else_items, indent + INDENT))
            lines.append(f"{indent}end")
        else:
            raise TypeError(f"kengeki item must be KengekiWeight or Branch, got {type(item)!r}")
    return "\n".join(lines)


def generate_kengeki_activate(activator: KengekiActivator) -> str:
    """The core `if/elseif local0 == <effect_id> then ... end` selector chain
    of Goal.Kengeki_Activate. Emits just the chain (the surrounding preamble
    and REGIST_FUNC tail are boilerplate the user keeps)."""
    parts = []
    for idx, block in enumerate(activator.blocks):
        kw = "if" if idx == 0 else "elseif"
        body = _render_kengeki_items(block.items, INDENT)
        parts.append(f"{kw} local0 == {block.effect_id} then\n{body}")
    if not parts:
        return ""
    return "\n".join(parts) + "\nend"


# --- special-effect registration ------------------------------------------

def registration_line(effect_id: int, target: str = "TARGET_SELF") -> str:
    """The `AddObserveSpecialEffectAttribute` line to register an effect ID.

    `target` is "TARGET_SELF" or "TARGET_ENE_0" (both appear in the file).
    Emitted at 4-space indent, as it sits inside Goal.Activate.
    """
    return f"{INDENT}arg1:AddObserveSpecialEffectAttribute({target}, {effect_id})"


def needs_registration(effect_id: int, target: str, existing_lua: str) -> bool:
    """True if (target, effect_id) is NOT already registered in existing_lua.

    Matches on BOTH target and id: the same effect_id registered with a
    different target (SELF vs ENE_0) counts as a different registration, so
    it still needs a new line. Whitespace inside the call is tolerated.
    """
    pattern = re.compile(
        r"AddObserveSpecialEffectAttribute\(\s*"
        + re.escape(target)
        + r"\s*,\s*"
        + str(int(effect_id))
        + r"\s*\)"
    )
    return pattern.search(existing_lua) is None
