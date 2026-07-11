"""Parse an existing Sekiro `.lua` (HKS) behavior file into `ComboSequence`
objects — the reverse of `generator.py`.

UI-agnostic. Tolerant by design: the real file is far richer than the model
(runtime `localN` expressions, compound conditions, chained `:TimingSetNumber`
calls). We parse the combo-relevant subset and record everything we cannot
faithfully model in `ParseResult.warnings` instead of guessing or crashing.

Anchored to `710300_battle.lua`; see the plan for line references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models import (
    Branch,
    ComboSequence,
    ComboStep,
    KengekiActivator,
    KengekiEffectBlock,
    KengekiWeight,
    Term,
)


@dataclass
class ParseResult:
    sequences: list = field(default_factory=list)   # list[ComboSequence]
    activators: list = field(default_factory=list)  # list[KengekiActivator]
    warnings: list = field(default_factory=list)    # list[str]


# --- text preprocessing ----------------------------------------------------

def _strip_comment(line: str) -> str:
    """Remove a trailing `-- ...` Lua comment (no string literals appear in the
    combo code we care about, so a plain split is safe)."""
    idx = line.find("--")
    return line[:idx] if idx != -1 else line


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Return (indent, text) for each *logical* line: comments stripped, blank
    lines dropped, and statements that span physical lines (unbalanced parens,
    e.g. a wrapped `:TimingSetNumber(...)`) joined into one.
    """
    out: list[tuple[int, str]] = []
    buf = ""
    indent = 0
    depth = 0
    for raw in text.splitlines():
        stripped_comment = _strip_comment(raw)
        if not buf:
            if not stripped_comment.strip():
                continue
            indent = len(stripped_comment) - len(stripped_comment.lstrip())
            buf = stripped_comment.strip()
        else:
            buf += " " + stripped_comment.strip()
        depth += stripped_comment.count("(") - stripped_comment.count(")")
        if depth <= 0:
            out.append((indent, buf))
            buf = ""
            depth = 0
    if buf:
        out.append((indent, buf))
    return out


# --- argument helpers ------------------------------------------------------

def _split_top_level(inside: str) -> list[str]:
    """Split a call's arg string on top-level commas, respecting nested parens."""
    args, depth, cur = [], 0, ""
    for ch in inside:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def _balanced_call(s: str, open_idx: int) -> tuple[str, int]:
    """Given `s` and the index of a '(', return (inside, index_after_close)."""
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return s[open_idx + 1:i], i + 1
    raise ValueError("unbalanced parens")


_INT_RE = re.compile(r"^-?\d+$")
_LOCAL_RE = re.compile(r"^local\d+$")
_RANDAM_RE = re.compile(r"GetRandam_Int\(\s*1\s*,\s*100\s*\)")


def _resolve(token: str, locals_: dict):
    """Resolve a single arg token via the per-function local table.

    Returns an int for integer literals / int-valued locals, otherwise a
    self-contained expression string.
    """
    token = token.strip()
    if _INT_RE.match(token):
        return int(token)
    if _LOCAL_RE.match(token) and token in locals_:
        val = locals_[token]
        if isinstance(val, dict):        # random marker -> keep symbolic
            return token
        return val
    return token


def _build_local_table(lines: list[tuple[int, str]]) -> dict:
    """Scan `local localN = <rhs>` within one function body. Last write wins.

    int literal -> int; `GetRandam_Int(1,100)` -> {"kind": "random"} marker;
    anything else -> the raw expression string.
    """
    table: dict = {}
    for _indent, text in lines:
        m = re.match(r"^local\s+(local\d+)\s*=\s*(.+)$", text)
        if not m:
            continue
        name, rhs = m.group(1), m.group(2).strip()
        if _INT_RE.match(rhs):
            table[name] = int(rhs)
        elif _RANDAM_RE.search(rhs):
            table[name] = {"kind": "random"}
        else:
            table[name] = rhs
    return table


# --- statement / step parsing ---------------------------------------------

def _parse_addsubgoal(text: str, locals_: dict, warnings: list) -> ComboStep:
    """Parse one `argX:AddSubGoal(...)` (with any trailing `:Timing...` dropped)."""
    open_idx = text.index("AddSubGoal(") + len("AddSubGoal")
    inside, after = _balanced_call(text, open_idx)
    if after < len(text) and text[after:].lstrip().startswith(":"):
        warnings.append(f"dropped chained call after AddSubGoal: {text[after:].strip()}")
    args = _split_top_level(inside)
    goal_type = args[0].strip()
    if goal_type.startswith("GOAL_COMMON_"):
        goal_type = goal_type[len("GOAL_COMMON_"):]
    priority = _resolve(args[1], locals_) if len(args) > 1 else 0
    anim_id = _resolve(args[2], locals_) if len(args) > 2 else 0
    target = args[3].strip() if len(args) > 3 else "TARGET_ENE_0"
    distance = _resolve(args[4], locals_) if len(args) > 4 else 9999
    extra = [_resolve(a, locals_) for a in args[5:]]
    return ComboStep(goal_type=goal_type, anim_id=anim_id, priority=priority,
                     distance=distance, target=target, extra_args=extra)


def _split_top_level_bool(cond: str):
    """Split a condition on top-level ` and `/` or ` (respecting parens).

    Returns (connective, parts). connective is "and"/"or", None if there is no
    top-level connective, or "__mixed__" if both appear (can't model cleanly).
    """
    parts, cur, depth, conns = [], "", 0, []
    i, n = 0, len(cond)
    while i < n:
        c = cond[i]
        if c == "(":
            depth += 1; cur += c; i += 1
        elif c == ")":
            depth -= 1; cur += c; i += 1
        elif depth == 0 and cond[i:i + 5] == " and ":
            parts.append(cur); conns.append("and"); cur = ""; i += 5
        elif depth == 0 and cond[i:i + 4] == " or ":
            parts.append(cur); conns.append("or"); cur = ""; i += 4
        else:
            cur += c; i += 1
    parts.append(cur)
    if not conns:
        return None, [cond]
    if len(set(conns)) > 1:
        return "__mixed__", [cond]
    return conns[0], [p.strip() for p in parts]


def _classify_term(cond: str, locals_: dict) -> Term:
    """Classify one primitive condition into a Term (kind "raw" if unknown)."""
    original = cond.strip()
    body = original
    negate = False
    if body.startswith("not "):
        negate, body = True, body[4:].strip()
    m = re.match(r"^arg\d:HasSpecialEffectId\((TARGET_\w+),\s*(\d+)\)$", body)
    if m:
        return Term(kind="speffect", negate=negate,
                    target=m.group(1), effect_id=int(m.group(2)))
    if _RANDAM_RE.search(body):
        t = re.search(r"<=\s*(\d+)", body)
        if t:
            return Term(kind="randam", negate=negate, threshold=int(t.group(1)))
    m = re.match(r"^randam\s*<=\s*(\d+)$", body)
    if m:
        return Term(kind="randam", negate=negate, threshold=int(m.group(1)))
    m = re.match(r"^(local\d+)\s*<=\s*(\d+)$", body)
    if m and isinstance(locals_.get(m.group(1)), dict):
        return Term(kind="randam", negate=negate, threshold=int(m.group(2)))
    m = re.match(r"^arg\d:GetNumber\((\d+)\)\s*==\s*(-?\d+)$", body)
    if m:
        return Term(kind="state", negate=negate,
                    state_index=int(m.group(1)), state_value=int(m.group(2)))
    m = re.match(r"^(?:arg\d:GetNinsatsuNum\(\)|ninsatsu)\s*(<=|>=|==|<|>)\s*(\d+)$", body)
    if m:
        return Term(kind="ninsatsu", negate=negate,
                    operator=m.group(1), threshold=int(m.group(2)))
    return Term(kind="raw", raw=original)


def _classify_condition(cond: str, locals_: dict, warnings: list) -> Branch:
    """Turn a Lua `if` condition into a Branch of one or more Terms."""
    cond = cond.strip()
    conn, parts = _split_top_level_bool(cond)
    if conn == "__mixed__" or (conn is not None and any(p.startswith("(") for p in parts)):
        warnings.append(f"un-modelled condition kept raw: {cond}")
        return Branch(terms=[Term(kind="raw", raw=cond)])
    if conn is None:
        term = _classify_term(cond, locals_)
        if term.kind == "raw":
            warnings.append(f"un-modelled condition kept raw: {cond}")
        return Branch(terms=[term])
    terms = [_classify_term(p, locals_) for p in parts]
    if any(t.kind == "raw" for t in terms):
        warnings.append(f"partially-modelled condition: {cond}")
    return Branch(terms=terms, connective=conn)


def _addsubgoal_leaf(text, locals_, warnings):
    """Default leaf parser: an `argX:AddSubGoal(...)` line -> ComboStep."""
    if "AddSubGoal(" in text:
        return _parse_addsubgoal(text, locals_, warnings)
    return None


def _kengeki_leaf(text, locals_, warnings):
    """Leaf parser for Kengeki_Activate: `kengeki[index] = value` -> KengekiWeight."""
    m = re.match(r"^kengeki\[(\d+)\]\s*=\s*(.+)$", text)
    if m:
        return KengekiWeight(index=int(m.group(1)),
                             value=_resolve(m.group(2), locals_))
    return None


def _parse_block(lines, i, base_indent, locals_, warnings, leaf=_addsubgoal_leaf):
    """Recursively parse logical lines into list[<leaf> | Branch].

    `leaf(text, locals_, warnings)` returns a leaf item for a recognised
    statement line, else None. Returns (items, next_index). Stops at a dedent
    below base_indent or at an enclosing `else` / `elseif` / `end`.
    """
    items = []
    while i < len(lines):
        indent, text = lines[i]
        if indent < base_indent:
            break
        head = text.split(None, 1)[0] if text else ""
        if head in ("else", "elseif", "end"):
            break
        if text.startswith("if ") and text.endswith(" then"):
            branch, i = _parse_if(lines, i, indent, locals_, warnings, leaf=leaf)
            if branch is not None:
                items.append(branch)
            continue
        item = leaf(text, locals_, warnings)
        if item is not None:
            items.append(item)
        i += 1
    return items, i


def _parse_if(lines, i, indent, locals_, warnings, _from_elseif=False, leaf=_addsubgoal_leaf):
    """Parse `if <cond> then ... [elseif/else ...] end` at `indent`.

    Returns (Branch | None, next_index). An `if/elseif` chain becomes nested
    Branches down the false side. Returns None (and warns) for an `if` that
    contains no leaf items — e.g. Act23's param-computing ifs.
    """
    line = lines[i][1]
    kw = "elseif " if _from_elseif else "if "
    cond = line[len(kw):-len(" then")].strip()
    branch = _classify_condition(cond, locals_, warnings)
    branch.from_elseif = _from_elseif   # distinguishes real elseif from else{if}
    true_items, j = _parse_block(lines, i + 1, indent + 4, locals_, warnings, leaf=leaf)
    branch.true_branch = true_items
    false_items = []
    if j < len(lines) and lines[j][0] == indent:
        nxt = lines[j][1]
        if nxt.startswith("elseif ") and nxt.endswith(" then"):
            nested, j = _parse_if(lines, j, indent, locals_, warnings,
                                  _from_elseif=True, leaf=leaf)
            if nested is not None:
                false_items = [nested]
        elif nxt.split(None, 1)[0] == "else":
            false_items, j = _parse_block(lines, j + 1, indent + 4, locals_, warnings, leaf=leaf)
    if not _from_elseif:
        if j < len(lines) and lines[j][0] == indent and lines[j][1].strip() == "end":
            j += 1
    branch.false_branch = false_items
    if not branch.true_branch and not branch.false_branch:
        warnings.append(f"skipped non-combo if: {cond}")
        return None, j
    return branch, j


# --- function / block discovery -------------------------------------------

def iter_function_spans(text: str):
    """Yield (name, start, end) for each `Goal.X = function(...)` block.

    Each span runs from the function's `Goal.X = function` start to the next
    function's start (or EOF). Public so writer.py can splice by offset.
    """
    starts = [(m.start(), m.group(1))
              for m in re.finditer(r"^Goal\.(\w+)\s*=\s*function", text, re.M)]
    for idx, (pos, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(text)
        yield name, pos, end


def _iter_functions(text: str):
    """Yield (name, body_text) for each `Goal.X = function(...)` block."""
    for name, start, end in iter_function_spans(text):
        yield name, text[start:end]


def _parse_approach(lines, locals_):
    """Return the 7 resolved Approach_Act_Flex params, or None if absent."""
    for _indent, text in lines:
        if text.startswith("Approach_Act_Flex("):
            inside, _ = _balanced_call(text, text.index("("))
            args = _split_top_level(inside)
            # args[0], args[1] are arg0, arg1; the 7 params follow
            return [_resolve(a, locals_) for a in args[2:]]
    return None


def _parse_kengeki_move(name: str, body: str, warnings: list) -> ComboSequence:
    """Parse a `Goal.KengekiNN` move — like an Act but no approach, and the
    leading `arg1:ClearSubGoal()` is simply skipped as a non-combo statement."""
    num = int(re.match(r"Kengeki(\d+)", name).group(1))
    lines = _logical_lines(body)[1:]   # drop the header line
    locals_ = _build_local_table(lines)
    steps, _ = _parse_block(lines, 0, base_indent=4, locals_=locals_, warnings=warnings)
    return ComboSequence(name=name, trigger_type="kengeki_move", trigger_id=num,
                         steps=steps)


def _parse_act(name: str, body: str, warnings: list) -> ComboSequence:
    num = int(re.match(r"Act(\d+)", name).group(1))
    lines = _logical_lines(body)[1:]   # drop the `Goal.ActNN = function(...)` header
    locals_ = _build_local_table(lines)
    approach = _parse_approach(lines, locals_)
    steps, _ = _parse_block(lines, 0, base_indent=4, locals_=locals_, warnings=warnings)
    return ComboSequence(name=name, trigger_type="act_entry", trigger_id=num,
                         steps=steps, approach=approach)


def _parse_kengeki_activate(body: str, warnings: list) -> KengekiActivator:
    """Parse the `if/elseif local0 == <effect_id> then` chain of
    Goal.Kengeki_Activate into a KengekiActivator. The `== 0` guard block and
    the trailing REGIST_FUNC / Common_Kengeki_Activate boilerplate are ignored.
    """
    lines = _logical_lines(body)[1:]   # drop the header line
    locals_ = _build_local_table(lines)
    activator = KengekiActivator()
    i = 0
    while i < len(lines):
        indent, text = lines[i]
        m = re.match(r"^(?:if|elseif) local0 == (\d+) then$", text)
        if not m:
            i += 1
            continue
        eid = int(m.group(1))
        items, j = _parse_block(lines, i + 1, indent + 4, locals_, warnings,
                                leaf=_kengeki_leaf)
        if eid != 0:   # `== 0` is the "no kengeki effect" guard, not a block
            activator.blocks.append(KengekiEffectBlock(effect_id=eid, items=items))
        i = j
    return activator


def _parse_interrupt(body: str, warnings: list) -> list:
    """Parse each `elseif interruptEffectIdentifier == <id> then` branch."""
    lines = _logical_lines(body)
    locals_ = _build_local_table(lines)
    sequences = []
    i = 0
    while i < len(lines):
        indent, text = lines[i]
        m = re.match(r"^elseif interruptEffectIdentifier == (\d+) then$", text)
        if not m:
            # compound guard, e.g. `== ID and HasSpecialEffectId(...)` -> skip+warn
            g = re.match(r"^elseif interruptEffectIdentifier == (\d+) and ", text)
            if g:
                warnings.append(f"skipped compound interrupt guard for id {g.group(1)}")
            i += 1
            continue
        eid = int(m.group(1))
        steps, j = _parse_block(lines, i + 1, indent + 4, locals_, warnings)
        # strip a leading ClearSubGoal artefact if it slipped in (it never
        # becomes a ComboStep, but be safe)
        sequences.append(ComboSequence(name=f"Interrupt_{eid}",
                                       trigger_type="special_effect",
                                       trigger_id=eid, steps=steps))
        i = j
    return sequences


# --- public entry point ----------------------------------------------------

def parse_file(text: str) -> ParseResult:
    result = ParseResult()
    for name, body in _iter_functions(text):
        if re.match(r"Act\d+$", name):
            result.sequences.append(_parse_act(name, body, result.warnings))
        elif re.match(r"Kengeki\d+$", name):
            result.sequences.append(_parse_kengeki_move(name, body, result.warnings))
        elif name == "Kengeki_Activate":
            result.activators.append(_parse_kengeki_activate(body, result.warnings))
        elif name == "Interrupt":
            result.sequences.extend(_parse_interrupt(body, result.warnings))
    return result
