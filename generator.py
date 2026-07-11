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
    Branch,
    ComboSequence,
    ComboStep,
    KengekiActivator,
    KengekiWeight,
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

def _branch_condition(branch: Branch, ctx: str) -> str:
    """The Lua boolean expression for a branch's `if`.

    ctx is "act" or "interrupt" and only affects the random idiom:
      - act:       arg0:GetRandam_Int(1, 100) <= N
      - interrupt: randam <= N   (randam is a local in Goal.Interrupt)
    state_check is the same in both contexts: arg1:GetNumber(idx) == value
    """
    if branch.kind == "randam_percent":
        if ctx == "interrupt":
            return f"randam <= {branch.threshold}"
        return f"arg0:GetRandam_Int(1, 100) <= {branch.threshold}"
    if branch.kind == "state_check":
        return f"arg1:GetNumber({branch.state_index}) == {branch.state_value}"
    if branch.kind == "ninsatsu":
        op = branch.operator or "<="
        return f"arg1:GetNinsatsuNum() {op} {branch.threshold}"
    if branch.kind == "raw":
        return branch.raw_condition
    raise ValueError(f"unknown branch kind: {branch.kind!r}")


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
            cond = _branch_condition(item, ctx)
            lines.append(f"{indent}if {cond} then")
            lines.append(render_items(item.true_branch, receiver, ctx, indent + INDENT))
            if item.false_branch:
                lines.append(f"{indent}else")
                lines.append(render_items(item.false_branch, receiver, ctx, indent + INDENT))
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
            cond = _branch_condition(item, ctx="act")  # kengeki has no random idiom
            lines.append(f"{indent}if {cond} then")
            lines.append(_render_kengeki_items(item.true_branch, indent + INDENT))
            if item.false_branch:
                lines.append(f"{indent}else")
                lines.append(_render_kengeki_items(item.false_branch, indent + INDENT))
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
