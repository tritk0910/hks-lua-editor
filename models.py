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
class Branch:
    """A split in the combo.

    kind:
      - "randam_percent": `arg0:GetRandam_Int(1,100) <= threshold` (the game
        spells it "Randam"). `threshold` is the percent.
      - "state_check": `arg1:GetNumber(state_index) == state_value`.
      - "ninsatsu": `arg1:GetNinsatsuNum() <operator> threshold` — ninsatsu is
        the boss's deathblow count / phase (e.g. `<= 1`, `>= 2`). `operator`
        holds the comparison, `threshold` the value.
      - "raw": `raw_condition` verbatim Lua.

    `true_branch` / `false_branch` hold nested steps and branches, so combos
    can nest arbitrarily deep.
    """

    kind: str                       # "randam_percent" | "state_check" | "ninsatsu" | "raw"
    threshold: int = 0              # percent (randam) or compare value (ninsatsu)
    state_index: int | None = None  # for GetNumber(N) == value checks
    state_value: int | None = None
    operator: str | None = None     # comparison for ninsatsu: "<=", ">=", "==", "<", ">"
    raw_condition: str | None = None  # verbatim Lua for kind == "raw"
    from_elseif: bool = False       # True if this branch was reached via `elseif`
                                    # (part of a ladder) rather than a nested
                                    # `else { if }`. Display-only distinction.
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


# --- Kengeki (sword-clash) selector: Goal.Kengeki_Activate ------------------
# A different structure from combos: instead of AddSubGoal chains, it assigns
# weights `kengeki[index] = value` under a condition tree, keyed by the value
# of `ReturnKengekiSpecialEffect`. The condition tree reuses `Branch` (its
# true/false lists then hold KengekiWeight leaves instead of ComboStep).

@dataclass
class KengekiWeight:
    """One `kengeki[index] = value` assignment (a move's selection weight)."""

    index: int
    value: int | str


@dataclass
class KengekiEffectBlock:
    """One `local0 == <effect_id>` branch of Goal.Kengeki_Activate."""

    effect_id: int       # e.g. 200200 — a ReturnKengekiSpecialEffect value
    items: list = field(default_factory=list)  # list[KengekiWeight | Branch]


@dataclass
class KengekiActivator:
    """The whole Goal.Kengeki_Activate selector: an ordered if/elseif chain."""

    blocks: list = field(default_factory=list)  # list[KengekiEffectBlock]


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
