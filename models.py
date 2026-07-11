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
    """A split in the combo: a random-percent roll, a state check, or a raw
    (un-modelled) Lua condition preserved verbatim.

    `true_branch` / `false_branch` hold nested steps and branches, so combos
    can nest arbitrarily deep.
    """

    kind: str                       # "random_percent" | "state_check" | "raw"
    threshold: int = 0              # e.g. 50, for random_percent
    state_index: int | None = None  # for GetNumber(N) == value checks
    state_value: int | None = None
    raw_condition: str | None = None  # verbatim Lua for kind == "raw"
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
