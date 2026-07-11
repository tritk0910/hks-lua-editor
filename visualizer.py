"""Render a `ComboSequence` as a simple text/ASCII diagram.

UI-agnostic: returns a plain string so it can be printed to a terminal, put
in a Qt label, or asserted on in tests. Graphical rendering (a real canvas
with drawn diamonds/arrows) is a later UI concern; this is the readable
sanity-check view.

Layout: steps are `[animID goal_type]` nodes chained with a downward arrow.
A Branch renders as a `<diamond>` with two labelled sub-trees (true / false),
each indented one level under the diamond.
"""

from __future__ import annotations

from models import Branch, ComboSequence, ComboStep

INDENT = "  "  # 2 spaces per nesting level, kept tight for readability


def _branch_label(branch: Branch) -> str:
    """Short human label shown on the diamond."""
    if branch.kind == "random_percent":
        return f"random {branch.threshold}%"
    if branch.kind == "state_check":
        return f"GetNumber({branch.state_index}) == {branch.state_value}"
    return branch.kind


def _true_false_labels(branch: Branch) -> tuple[str, str]:
    """The two arrow labels for a branch's true/false paths."""
    if branch.kind == "random_percent":
        # <= threshold takes the true path; the rest takes false
        return f"<= {branch.threshold}%", f"> {branch.threshold}%"
    return "true", "false"


def _render_items(items, depth: int, lines: list[str]) -> None:
    pad = INDENT * depth
    for item in items:
        if isinstance(item, ComboStep):
            lines.append(f"{pad}[{item.anim_id} {item.goal_type}]")
            lines.append(f"{pad}  |")
        elif isinstance(item, Branch):
            true_lbl, false_lbl = _true_false_labels(item)
            lines.append(f"{pad}<{_branch_label(item)}>")
            lines.append(f"{pad}|-- {true_lbl}:")
            _render_items(item.true_branch, depth + 1, lines)
            lines.append(f"{pad}`-- {false_lbl}:")
            if item.false_branch:
                _render_items(item.false_branch, depth + 1, lines)
            else:
                lines.append(f"{pad}{INDENT}(nothing)")
        else:
            raise TypeError(f"combo item must be ComboStep or Branch, got {type(item)!r}")


def visualize(seq: ComboSequence) -> str:
    """Return an indented text diagram of the whole combo."""
    if seq.trigger_type == "act_entry":
        trigger = f"Act{seq.trigger_id:02d}"
    else:
        trigger = f"SpecialEffect {seq.trigger_id}"
    lines = [f"{seq.name}  ({trigger})", "  |"]
    _render_items(seq.steps, depth=0, lines=lines)
    return "\n".join(lines)
