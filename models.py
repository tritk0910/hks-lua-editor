"""Core data model for the Sekiro Combo Builder.

UI-agnostic on purpose: nothing in here may import PyQt / PySide or know
anything about how a combo is displayed or edited. The parser and generator
build on these types; the UI only ever constructs and reads them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComboStep:
    """One `AddSubGoal` call — a single animation step in a combo chain.

    Numeric fields are typed `int | str` because a real `.lua` file often
    passes a runtime expression instead of a literal, e.g.
    `distance="3.5 - arg0:GetMapHitRadius(TARGET_SELF)"`. The parser resolves
    such values to a self-contained expression string; the generator just
    stringifies whatever is stored.
    """

    goal_type: str          # "ComboFinal" | "ComboRepeat" | "ComboAttackTunableSpin"
                            # | "AttackImmediateAction" | "EndureAttack" | ...
    anim_id: int | str      # e.g. 3049
    priority: int | str     # e.g. 10, 5, 15
    distance: int | str = 9999  # 5th AddSubGoal arg (after target): usually a
                                # distance/range gate; 9999 == "no limit".
                                # (NOT a duration — see 710300_battle.lua.)
    target: str = "TARGET_ENE_0"
    extra_args: list = field(default_factory=list)  # raw leftover positional args


@dataclass
class Term:
    """One primitive condition. A Branch's condition is a list of these joined
    by a connective (`and`/`or`).

    kind:
      - "randam":   `arg0:GetRandam_Int(1,100) <= threshold` (game spelling).
      - "state":    `arg1:GetNumber(state_index) == state_value`.
      - "ninsatsu": `arg1:GetNinsatsuNum() <operator> threshold` (deathblow
        count / boss phase; e.g. `<= 1`, `>= 2`).
      - "speffect": `arg1:HasSpecialEffectId(target, effect_id)` — whether
        self/enemy currently has a special effect.
      - "raw":      `raw` verbatim Lua.
    `negate` prepends `not `.
    """

    kind: str                       # "randam" | "state" | "ninsatsu" | "speffect" | "raw"
    negate: bool = False
    threshold: int = 0              # randam percent, or ninsatsu compare value
    operator: str = "<="            # ninsatsu comparison: <=, >=, ==, <, >
    state_index: int | None = None
    state_value: int | None = None
    target: str = "TARGET_ENE_0"    # speffect: TARGET_SELF | TARGET_ENE_0
    effect_id: int | None = None    # speffect
    raw: str | None = None          # kind == "raw"


@dataclass
class BoolNode:
    """A parenthesised sub-group of a condition: `(child <op> child ...)`.

    A `Branch.terms` list holds `Term | BoolNode` joined by `Branch.connective`;
    a BoolNode nests further with its own `op`, enabling `(A or B) and C`.
    """

    op: str                                       # "and" | "or"
    terms: list = field(default_factory=list)     # list[Term | BoolNode]
    negate: bool = False                          # `not (...)`


def and_(*terms) -> "BoolNode":
    return BoolNode(op="and", terms=list(terms))


def or_(*terms) -> "BoolNode":
    return BoolNode(op="or", terms=list(terms))


def randam(threshold: int, negate: bool = False) -> "Term":
    return Term(kind="randam", negate=negate, threshold=threshold)


def state(index: int, value: int, negate: bool = False) -> "Term":
    return Term(kind="state", negate=negate, state_index=index, state_value=value)


def ninsatsu(operator: str, value: int, negate: bool = False) -> "Term":
    return Term(kind="ninsatsu", negate=negate, operator=operator, threshold=value)


def speffect(target: str, effect_id: int, negate: bool = False) -> "Term":
    return Term(kind="speffect", negate=negate, target=target, effect_id=effect_id)


def raw(text: str, negate: bool = False) -> "Term":
    return Term(kind="raw", negate=negate, raw=text)


@dataclass
class Branch:
    """A split in the combo. Its condition is `terms` joined by `connective`.

    `true_branch` / `false_branch` hold nested steps and branches, so combos
    can nest arbitrarily deep. `from_elseif` marks a branch reached via a real
    `elseif` (ladder) vs a nested `else { if }` (display-only distinction).
    """

    terms: list = field(default_factory=list)   # list[Term]
    connective: str = "and"                      # "and" | "or"
    from_elseif: bool = False
    true_branch: list = field(default_factory=list)   # list[ComboStep | Branch]
    false_branch: list = field(default_factory=list)


@dataclass
class ComboSequence:
    """A whole combo. Same shape for both Act combos and Interrupt combos;
    only `trigger_type` and where the generator inserts the Lua differ.
    """

    name: str            # user-given label, for the tool's own reference
    trigger_type: str    # "special_effect" | "act_entry" | "kengeki_move"
    trigger_id: int      # special effect ID, Act number, or Kengeki move number
    steps: list = field(default_factory=list)  # list[ComboStep | Branch], in order
    approach: list | None = None  # 7 Approach_Act_Flex params (local0..local6),
                                  # int or resolved expression string; None if absent


# --- Move selectors: Goal.Activate and Goal.Kengeki_Activate ----------------
# A different structure from combos: instead of AddSubGoal chains, these assign
# weights (`act[index] = value` / `kengeki[index] = value`) under a condition
# tree. The condition tree reuses `Branch` (its true/false lists then hold
# Weight leaves instead of ComboStep). Kengeki's chain is keyed by the value of
# `ReturnKengekiSpecialEffect`; Activate's arms are arbitrary conditions.
#
# `owned_lines` is the set of file lines the parser actually took its weights
# from, and it is what the writer is allowed to touch. It matters because these
# parsers do NOT cover every weight line in the function — Kengeki_Activate has
# standalone `if ... then kengeki[x] = 0 end` veto blocks after the local0
# chain that it skips. Without an explicit ownership set, the writer would read
# "line has a weight but the model doesn't" as "the user deleted it" and
# silently delete those lines.

@dataclass
class Weight:
    """One `act[index] = value` / `kengeki[index] = value` assignment — how
    likely that move is to be picked.

    `line` is the 1-based line in the source FILE this was parsed from, and is
    what lets the writer edit exactly this assignment: the same weight can
    appear more than once (e.g. `act[21] = 100` on two different lines), so
    matching by position or value would target the wrong one. None means the
    weight is new (added in the editor) and has no line yet.
    """

    index: int
    value: int | str
    line: int | None = None


KengekiWeight = Weight    # the original name, kept for existing callers


@dataclass
class KengekiEffectBlock:
    """One `local0 == <effect_id>` branch of Goal.Kengeki_Activate."""

    effect_id: int       # e.g. 200200 — a ReturnKengekiSpecialEffect value
    items: list = field(default_factory=list)  # list[Weight | Branch]


@dataclass
class KengekiActivator:
    """The whole Goal.Kengeki_Activate selector: an ordered if/elseif chain."""

    blocks: list = field(default_factory=list)  # list[KengekiEffectBlock]
    owned_lines: set = field(default_factory=set)


@dataclass
class ActActivator:
    """The `act[i] = weight` region of Goal.Activate: the main if/elseif ladder
    plus the standalone `if ... then act[x] = 0 end` veto blocks after it.

    Unlike Kengeki_Activate there is no effect-id keying — the arms are plain
    conditions, so this is just an ordered list at the top level.
    """

    items: list = field(default_factory=list)  # list[Weight | Branch]
    owned_lines: set = field(default_factory=set)


#: the selector tables — weight editors, not AddSubGoal combos
ACTIVATOR_TYPES = (KengekiActivator, ActActivator)


def is_activator(obj) -> bool:
    return isinstance(obj, ACTIVATOR_TYPES)


def unchain_branch(branch: Branch, parent_list: list):
    """Flatten an if/elseif/else chain for display (ladder view).

    A chain `if A then .. else (if B then .. else ..)` is stored as
    `Branch(A, false=[Branch(B, ...)])`. This walks that nesting and returns:
      - arms: list of (arm_branch, containing_list) — one per if/elseif
      - else_items: the final else body (may be empty)
    so the whole ladder can be rendered at one indent level. Only a
    false_branch that is EXACTLY one Branch flagged `from_elseif` is treated as
    an elseif continuation. A nested `else { if ... }` (from_elseif == False)
    is NOT flattened — it stays as an else body one level deeper, matching how
    the source actually nested it.
    """
    arms = [(branch, parent_list)]
    cur = branch
    while (len(cur.false_branch) == 1
           and isinstance(cur.false_branch[0], Branch)
           and cur.false_branch[0].from_elseif):
        arms.append((cur.false_branch[0], cur.false_branch))
        cur = cur.false_branch[0]
    return arms, cur.false_branch
