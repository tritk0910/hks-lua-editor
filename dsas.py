"""Import a combo from DS Animation Studio's combo viewer text.

DSAS lists a combo as one animation per line, e.g.:

    EnemyComboAtk 3000
    EnemyComboAtk 3001
    EnemyComboAtk 3002

The opener is a spinning attack (ComboAttackTunableSpin) and the rest are
ComboRepeat. If the target combo already has a spin opener, `first_is_spin`
should be False so the imported opener is a plain ComboRepeat too.

UI-agnostic: returns model objects only.
"""

from __future__ import annotations

import re

from models import Branch, ComboStep, unchain_branch


def parse_dsas_combo(text: str, first_is_spin: bool = True) -> list:
    """Return a list of ComboStep from pasted DSAS combo-viewer text.

    Each non-empty line contributes a step using the last number on the line as
    the anim id; lines with no number are ignored.
    """
    steps: list = []
    for line in text.splitlines():
        nums = re.findall(r"\d+", line)
        if not nums:
            continue
        anim = int(nums[-1])
        is_first = not steps
        goal_type = "ComboAttackTunableSpin" if (is_first and first_is_spin) else "ComboRepeat"
        steps.append(ComboStep(goal_type, anim, 10,
                               target="TARGET_ENE_0", distance=9999, extra_args=[0, 0]))
    return steps


def export_dsas(items, choices=None) -> str:
    """Serialise a combo back to DSAS combo-viewer text (`EnemyComboAtk <anim>`).

    At each Branch, `choices[id(branch_head)]` selects which arm to follow:
    an int arm index (0 = if, 1 = first elseif, ...) or the string "else".
    Missing choices default to the `if` arm (index 0).
    """
    choices = choices or {}
    lines: list = []
    _walk_dsas(items, choices, lines)
    return "\n".join(lines)


def _walk_dsas(items, choices, lines):
    for it in items:
        if isinstance(it, ComboStep):
            lines.append(f"EnemyComboAtk {it.anim_id}")
        elif isinstance(it, Branch):
            arms, else_items = unchain_branch(it, items)
            key = choices.get(id(it), 0)
            if key == "else":
                _walk_dsas(else_items, choices, lines)
            else:
                idx = key if isinstance(key, int) and 0 <= key < len(arms) else 0
                _walk_dsas(arms[idx][0].true_branch, choices, lines)
