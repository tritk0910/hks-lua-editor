"""Small shared helpers for the UI layer (no Qt widgets, no window state)."""

from __future__ import annotations

from models import ActActivator, KengekiActivator

TRIGGER_TYPES = ["act_entry", "special_effect", "kengeki_move"]


def _parse_val(text: str):
    """Int if the text looks like one, else the trimmed string (an expression)."""
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return text


def _count_weights(items) -> int:
    """How many weight assignments are in an activator's item tree."""
    from models import Branch, Weight
    n = 0
    for it in items:
        if isinstance(it, Weight):
            n += 1
        elif isinstance(it, Branch):
            n += _count_weights(it.true_branch) + _count_weights(it.false_branch)
    return n


def _index_of(lst, obj) -> int:
    """Index of `obj` in `lst` by IDENTITY, not equality. Dataclass instances
    compare equal by value, so a duplicated step/branch would make list.index()
    return the wrong (first-equal) position — this finds the actual object."""
    for i, x in enumerate(lst):
        if x is obj:
            return i
    return -1


def _combo_label(item) -> str:
    if isinstance(item, KengekiActivator):
        return f"Kengeki_Activate ({len(item.blocks)} blocks)"
    if isinstance(item, ActActivator):
        return f"Activate — act weights ({_count_weights(item.items)})"
    kinds = {"act_entry": "Act", "special_effect": "Interrupt",
             "kengeki_move": "Kengeki"}
    kind = kinds.get(item.trigger_type, item.trigger_type)
    return f"{kind} {item.trigger_id} — {item.name}"
