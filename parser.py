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
    ActActivator,
    RawLine,
    BoolNode,
    Branch,
    ComboSequence,
    ComboStep,
    KengekiActivator,
    KengekiEffectBlock,
    Term,
    Weight,
)


@dataclass
class ParseWarning:
    """Something in the source this parser could not model.

    `line` (1-based, in the file) and `where` (the Lua function it came from)
    are what let the UI point at it — and, more importantly, tell you before you
    overwrite a combo that regenerating it will drop what we couldn't read.
    """

    message: str
    line: int | None = None
    where: str = ""
    #: True when the source text is NOT in the model, so regenerating the
    #: function would silently delete it. False for things kept verbatim (a
    #: condition we couldn't decompose is still emitted as raw Lua).
    lossy: bool = False

    def __str__(self) -> str:
        at = f"line {self.line}" if self.line else "?"
        return f"{at} — {self.where}: {self.message}" if self.where else f"{at} — {self.message}"


def _warn(warnings: list, message: str, lineno: int | None = None,
          lossy: bool = False) -> None:
    warnings.append(ParseWarning(message, lineno, lossy=lossy))


@dataclass
class ParseResult:
    sequences: list = field(default_factory=list)   # list[ComboSequence]
    activators: list = field(default_factory=list)  # list[KengekiActivator]
    warnings: list = field(default_factory=list)    # list[ParseWarning]


# --- text preprocessing ----------------------------------------------------

def _strip_comment(line: str) -> str:
    """Remove a trailing `-- ...` Lua comment (no string literals appear in the
    combo code we care about, so a plain split is safe)."""
    idx = line.find("--")
    return line[:idx] if idx != -1 else line


def _logical_lines(text: str, line_offset: int | None = None,
                   keep_comments: bool = False) -> list[tuple]:
    """Return (indent, text) for each *logical* line: comments stripped, blank
    lines dropped, and statements that span physical lines (unbalanced parens,
    e.g. a wrapped `:TimingSetNumber(...)`) joined into one.

    With `line_offset` given, each entry becomes (indent, text, lineno) where
    lineno is 1-based and counted from `line_offset` — i.e. pass the line the
    `text` starts on within the file to get file line numbers. The writer needs
    these to edit one specific assignment (see models.Weight.line).
    """
    out: list[tuple] = []
    buf = ""
    indent = 0
    depth = 0
    start_line = 0
    for n, raw in enumerate(text.splitlines()):
        stripped_comment = _strip_comment(raw)
        if not buf:
            if not stripped_comment.strip():
                # a comment-only line strips to nothing; a region rewrite has to
                # put it back, so hand it out verbatim when asked
                if keep_comments and raw.strip().startswith("--"):
                    out.append((len(raw) - len(raw.lstrip()), raw.strip(),
                                (line_offset or 1) + n))
                continue
            indent = len(stripped_comment) - len(stripped_comment.lstrip())
            buf = stripped_comment.strip()
            start_line = n
        else:
            buf += " " + stripped_comment.strip()
        depth += stripped_comment.count("(") - stripped_comment.count(")")
        if depth <= 0:
            out.append((indent, buf) if line_offset is None
                       else (indent, buf, line_offset + start_line))
            buf = ""
            depth = 0
    if buf:
        out.append((indent, buf) if line_offset is None
                   else (indent, buf, line_offset + start_line))
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
    for entry in lines:                    # (indent, text[, lineno])
        text = entry[1]
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

# Boilerplate the combo generators reconstruct themselves — keeping it as a raw
# line would double it (or fight the `approach` field / inlined args). Anything
# NOT matched here and not modelled (SetNumber, SetTimer, return true,
# ClearSubGoal, ...) is kept verbatim so it survives a rewrite, shows in the
# tree, and — crucially — keeps its position (ClearSubGoal isn't always first).
_COMBO_BOILERPLATE = re.compile(
    r"^(local\s+local\d+\s*=|Approach_Act_Flex\(|GetWellSpace_Odds\s*="
    r"|return GetWellSpace_Odds$)")


def _keep_combo_raw(text: str) -> bool:
    return _COMBO_BOILERPLATE.match(text) is None


def _parse_addsubgoal(text: str, locals_: dict, warnings: list,
                      lineno: int | None = None) -> ComboStep:
    """Parse one `argX:AddSubGoal(...)` (with any trailing `:Timing...` dropped)."""
    open_idx = text.index("AddSubGoal(") + len("AddSubGoal")
    inside, after = _balanced_call(text, open_idx)
    if after < len(text) and text[after:].lstrip().startswith(":"):
        # NOTE: this is the dangerous one — regenerating this combo will not
        # bring the chained call back, so the UI warns before overwriting it.
        _warn(warnings, f"dropped chained call after AddSubGoal: {text[after:].strip()}",
              lineno, lossy=True)
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


def _tokenize_cond(s: str):
    """Tokenize a Lua condition into and/or/not keywords, grouping parens, and
    term text. A `(` that immediately follows an identifier is a function-call
    paren (part of a term), not a grouping paren."""
    toks, term, i, n = [], "", 0, len(s)

    def flush():
        nonlocal term
        if term.strip():
            toks.append(("term", term.strip()))
        term = ""

    while i < n:
        c = s[i]
        if c == "(":
            prev = term.rstrip()[-1:] if term.rstrip() else (s[i - 1] if i else "")
            if prev and (prev.isalnum() or prev == "_"):   # function-call paren
                depth = 0
                while i < n:
                    term += s[i]
                    if s[i] == "(":
                        depth += 1
                    elif s[i] == ")":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    i += 1
                continue
            flush(); toks.append(("(", "(")); i += 1; continue
        if c == ")":
            flush(); toks.append((")", ")")); i += 1; continue
        m = re.match(r"(and|or|not)(?=\s|\(|$)", s[i:])
        if m and (i == 0 or s[i - 1] in " \t("):
            flush(); toks.append((m.group(1), m.group(1))); i += len(m.group(1)); continue
        term += c; i += 1
    flush()
    return toks


class _CondParser:
    """Recursive-descent parser: expr = or; or = and ('or' and)*;
    and = unary ('and' unary)*; unary = 'not'* atom; atom = '(' expr ')' | term."""

    def __init__(self, toks, locals_):
        self.toks, self.i, self.locals = toks, 0, locals_

    def _peek(self):
        return self.toks[self.i][0] if self.i < len(self.toks) else None

    def _parse(self, op, sub):
        parts = [sub()]
        while self._peek() == op:
            self.i += 1
            parts.append(sub())
        return parts[0] if len(parts) == 1 else BoolNode(op=op, terms=parts)

    def parse_expr(self):
        return self._parse("or", lambda: self._parse("and", self._unary))

    def _unary(self):
        neg = False
        while self._peek() == "not":
            self.i += 1
            neg = not neg
        atom = self._atom()
        if neg:
            atom.negate = not atom.negate
        return atom

    def _atom(self):
        tok = self._peek()
        if tok == "(":
            self.i += 1
            node = self.parse_expr()
            if self._peek() == ")":
                self.i += 1
            return node
        if tok == "term":
            text = self.toks[self.i][1]
            self.i += 1
            return _classify_term(text, self.locals)
        return Term(kind="raw", raw="")


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


def _has_raw(item) -> bool:
    if isinstance(item, BoolNode):
        return any(_has_raw(c) for c in item.terms)
    return item.kind == "raw"


def _classify_condition(cond: str, locals_: dict, warnings: list,
                        lineno: int | None = None) -> Branch:
    """Turn a Lua `if` condition into a Branch whose `terms` may nest BoolNode
    groups, e.g. `(A or B) and C` -> terms=[BoolNode(or,[A,B]), C]."""
    cond = cond.strip()
    root = _CondParser(_tokenize_cond(cond), locals_).parse_expr()
    if isinstance(root, BoolNode) and not root.negate:
        branch = Branch(terms=list(root.terms), connective=root.op)
    else:
        branch = Branch(terms=[root])   # single term or a negated group
    if _has_raw(branch.terms[0] if len(branch.terms) == 1 else BoolNode("and", branch.terms)):
        _warn(warnings, f"condition has un-modelled parts kept raw: {cond}", lineno)
    return branch


def _addsubgoal_leaf(text, locals_, warnings, lineno=None):
    """Default leaf parser: an `argX:AddSubGoal(...)` line -> ComboStep."""
    if "AddSubGoal(" in text:
        return _parse_addsubgoal(text, locals_, warnings, lineno)
    return None


def _weight_leaf(table: str):
    """Build a leaf parser for `<table>[index] = value` weight assignments.

    `SetCoolTime` lines share that shape (`act[1] = SetCoolTime(...)`) but are
    cooldowns, not weights — they must not be picked up.
    """
    pattern = re.compile(rf"^{table}\[(\d+)\]\s*=\s*(.+)$")

    def leaf(text, locals_, warnings, lineno=None):
        m = pattern.match(text)
        if m and "SetCoolTime(" not in text:
            return Weight(index=int(m.group(1)),
                          value=_resolve(m.group(2), locals_), line=lineno)
        return None

    return leaf


_kengeki_leaf = _weight_leaf("kengeki")
_act_leaf = _weight_leaf("act")


def _parse_block(lines, i, base_indent, locals_, warnings, leaf=_addsubgoal_leaf,
                 keep_raw: bool = False):
    """Recursively parse logical lines into list[<leaf> | Branch].

    `leaf(text, locals_, warnings, lineno)` returns a leaf item for a
    recognised statement line, else None. Returns (items, next_index). Stops at
    a dedent below base_indent or at an enclosing `else` / `elseif` / `end`.

    `lines` entries are (indent, text) or (indent, text, lineno) — see
    _logical_lines. lineno is handed to the leaf (None when not tracked).
    """
    items = []
    while i < len(lines):
        entry = lines[i]
        indent, text = entry[0], entry[1]
        if indent < base_indent:
            break
        head = text.split(None, 1)[0] if text else ""
        if head in ("else", "elseif", "end"):
            break
        if text.startswith("if ") and text.endswith(" then"):
            branch, i = _parse_if(lines, i, indent, locals_, warnings, leaf=leaf,
                                  keep_raw=keep_raw)
            if branch is not None:
                items.append(branch)
            continue
        lineno = entry[2] if len(entry) > 2 else None
        item = leaf(text, locals_, warnings, lineno)
        if item is None and keep_raw and (keep_raw is True or keep_raw(text)):
            # not a leaf and not control flow: keep it verbatim rather than drop
            # it, so the region can be regenerated without losing the line.
            # `keep_raw` may be a predicate — combos keep only non-boilerplate
            # (the generator re-emits ClearSubGoal / the act tail itself).
            item = RawLine(text=" " * indent + text, line=lineno)
        if item is not None:
            items.append(item)
        i += 1
    return items, i


def _parse_if(lines, i, indent, locals_, warnings, _from_elseif=False,
              leaf=_addsubgoal_leaf, keep_raw: bool = False):
    """Parse `if <cond> then ... [elseif/else ...] end` at `indent`.

    Returns (Branch | None, next_index). An `if/elseif` chain becomes nested
    Branches down the false side. Returns None (and warns) for an `if` that
    contains no leaf items — e.g. Act23's param-computing ifs.
    """
    line = lines[i][1]
    lineno = lines[i][2] if len(lines[i]) > 2 else None
    kw = "elseif " if _from_elseif else "if "
    cond = line[len(kw):-len(" then")].strip()
    branch = _classify_condition(cond, locals_, warnings, lineno)
    branch.line = lineno
    branch.from_elseif = _from_elseif   # distinguishes real elseif from else{if}
    true_items, j = _parse_block(lines, i + 1, indent + 4, locals_, warnings,
                                 leaf=leaf, keep_raw=keep_raw)
    branch.true_branch = true_items
    false_items = []
    if j < len(lines) and lines[j][0] == indent:
        nxt = lines[j][1]
        if nxt.startswith("elseif ") and nxt.endswith(" then"):
            nested, j = _parse_if(lines, j, indent, locals_, warnings,
                                  _from_elseif=True, leaf=leaf, keep_raw=keep_raw)
            if nested is not None:
                false_items = [nested]
        elif nxt.split(None, 1)[0] == "else":
            false_items, j = _parse_block(lines, j + 1, indent + 4, locals_,
                                          warnings, leaf=leaf, keep_raw=keep_raw)
    if not _from_elseif:
        if j < len(lines) and lines[j][0] == indent and lines[j][1].strip() == "end":
            j += 1
    branch.false_branch = false_items
    if not branch.true_branch and not branch.false_branch:
        # the whole block is absent from the model -> a rewrite loses it
        _warn(warnings, f"skipped non-combo if: {cond}", lineno, lossy=True)
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
    for entry in lines:                    # (indent, text[, lineno])
        text = entry[1]
        if text.startswith("Approach_Act_Flex("):
            inside, _ = _balanced_call(text, text.index("("))
            args = _split_top_level(inside)
            # args[0], args[1] are arg0, arg1; the 7 params follow
            return [_resolve(a, locals_) for a in args[2:]]
    return None


def _parse_kengeki_move(name: str, body: str, warnings: list,
                        line_offset: int | None = None) -> ComboSequence:
    """Parse a `Goal.KengekiNN` move — like an Act but no approach, and the
    leading `arg1:ClearSubGoal()` is simply skipped as a non-combo statement."""
    num = int(re.match(r"Kengeki(\d+)", name).group(1))
    lines = _logical_lines(body, line_offset)[1:]   # drop the header line
    locals_ = _build_local_table(lines)
    mine: list = []
    steps, _ = _parse_block(lines, 0, base_indent=4, locals_=locals_, warnings=mine,
                            keep_raw=_keep_combo_raw)
    seq = ComboSequence(name=name, trigger_type="kengeki_move", trigger_id=num,
                        steps=steps)
    return _own_warnings(seq, name, mine, warnings)


def _parse_act(name: str, body: str, warnings: list,
               line_offset: int | None = None) -> ComboSequence:
    num = int(re.match(r"Act(\d+)", name).group(1))
    # drop the `Goal.ActNN = function(...)` header (line numbers stay absolute)
    lines = _logical_lines(body, line_offset)[1:]
    locals_ = _build_local_table(lines)
    approach = _parse_approach(lines, locals_)
    mine: list = []
    steps, _ = _parse_block(lines, 0, base_indent=4, locals_=locals_, warnings=mine,
                            keep_raw=_keep_combo_raw)
    seq = ComboSequence(name=name, trigger_type="act_entry", trigger_id=num,
                        steps=steps, approach=approach)
    return _own_warnings(seq, name, mine, warnings)


def _parse_kengeki_activate(body: str, warnings: list,
                            line_offset: int | None = None) -> KengekiActivator:
    """Parse the `if/elseif local0 == <effect_id> then` chain of
    Goal.Kengeki_Activate into a KengekiActivator. The `== 0` guard block and
    the trailing REGIST_FUNC / Common_Kengeki_Activate boilerplate are ignored.
    """
    lines = _logical_lines(body, line_offset, keep_comments=True)[1:]
    locals_ = _build_local_table(lines)
    activator = KengekiActivator()
    i = 0
    chain_end = 0
    while i < len(lines):
        indent, text = lines[i][0], lines[i][1]
        m = re.match(r"^(?:if|elseif) local0 == (\d+) then$", text)
        if not m:
            i += 1
            continue
        eid = int(m.group(1))
        items, j = _parse_block(lines, i + 1, indent + 4, locals_, warnings,
                                leaf=_kengeki_leaf, keep_raw=True)
        if eid != 0:   # `== 0` is the "no kengeki effect" guard, not a block
            activator.blocks.append(KengekiEffectBlock(effect_id=eid, items=items))
        i = chain_end = j

    # the veto blocks after the chain: same shape as Goal.Activate's, so read
    # them the same way — but only from past the chain, or the region scan would
    # just find the chain itself.
    start, end = weight_region(lines[chain_end:], "kengeki")
    if start != end:
        activator.extra_items, _ = _parse_block(
            lines[chain_end + start:chain_end + end], 0, 4, locals_, warnings,
            leaf=_kengeki_leaf, keep_raw=True)

    activator.owned_lines = _weight_lines(
        [it for b in activator.blocks for it in b.items] + activator.extra_items)
    return activator


def _weight_lines(items) -> set:
    """The file lines the weights in `items` came from (see Weight.line)."""
    lines = set()
    for it in items:
        if isinstance(it, Weight):
            if it.line is not None:
                lines.add(it.line)
        elif isinstance(it, Branch):
            lines |= _weight_lines(it.true_branch) | _weight_lines(it.false_branch)
    return lines


def _has_weight(lines, i, base_indent, table: str) -> bool:
    """True if the block starting at `lines[i]` contains a `<table>[n] = ...`
    assignment (ignoring SetCoolTime, which only looks like one)."""
    pat = re.compile(rf"^{table}\[\d+\]\s*=")
    for j in range(i, len(lines)):
        indent, text = lines[j][0], lines[j][1]
        if j > i and indent <= base_indent and not text.startswith(("elseif", "else", "end")):
            break
        if pat.match(text) and "SetCoolTime(" not in text:
            return True
    return False


def weight_region(lines, table: str = "act") -> tuple[int, int]:
    """(start, end) indices of a weight region: from the first top-level
    statement that assigns a weight, up to the cooldown block that follows it.

    Used for Goal.Activate's ladder and, from past the `local0` chain, for
    Kengeki_Activate's trailing veto blocks.

    The end MUST be pinned to the SetCoolTime block: _parse_block does not stop
    on its own and would otherwise run on into the REGIST_FUNC tail.
    """
    start, end = None, len(lines)
    for i, entry in enumerate(lines):
        indent, text = entry[0], entry[1]
        if "= SetCoolTime(" in text:
            end = i
            break
        if start is None and indent == 4 and _has_weight(lines, i, indent, table):
            start = i
    return (0, 0) if start is None else (start, end)


def _parse_activate(body: str, warnings: list,
                    line_offset: int | None = None) -> ActActivator:
    """Parse the `act[i] = <weight>` region of Goal.Activate.

    Unlike Kengeki_Activate there is no `local0 == id` keying — the arms are
    arbitrary conditions — so this is the plain statement list of the region
    (the main if/elseif ladder plus the standalone veto blocks after it).
    """
    lines = _logical_lines(body, line_offset, keep_comments=True)
    locals_ = _build_local_table(lines)
    start, end = weight_region(lines, "act")
    if start == end:
        return ActActivator()
    items, _ = _parse_block(lines[start:end], 0, 4, locals_, warnings,
                            leaf=_act_leaf, keep_raw=True)
    return ActActivator(items=items, owned_lines=_weight_lines(items))


def _parse_interrupt(body: str, warnings: list,
                     line_offset: int | None = None) -> list:
    """Parse each `elseif interruptEffectIdentifier == <id> then` branch."""
    lines = _logical_lines(body, line_offset)
    locals_ = _build_local_table(lines)
    sequences = []
    i = 0
    while i < len(lines):
        indent, text = lines[i][0], lines[i][1]
        lineno = lines[i][2] if len(lines[i]) > 2 else None
        m = re.match(r"^elseif interruptEffectIdentifier == (\d+) then$", text)
        if not m:
            # compound guard, e.g. `== ID and HasSpecialEffectId(...)` -> skip+warn
            g = re.match(r"^elseif interruptEffectIdentifier == (\d+) and ", text)
            if g:
                _warn(warnings, f"skipped compound interrupt guard for id {g.group(1)}",
                      lineno, lossy=True)
            i += 1
            continue
        eid = int(m.group(1))
        # each branch collects its own warnings, so the UI can tell you what
        # writing THIS combo would drop
        mine: list = []
        steps, j = _parse_block(lines, i + 1, indent + 4, locals_, mine,
                               keep_raw=_keep_combo_raw)
        seq = ComboSequence(name=f"Interrupt_{eid}", trigger_type="special_effect",
                            trigger_id=eid, steps=steps)
        sequences.append(_own_warnings(seq, "Interrupt", mine, warnings))
        i = j
    return sequences


def _own_warnings(seq: ComboSequence, where: str, mine: list, warnings: list):
    """Give `seq` the warnings raised while parsing it, and pass them up."""
    for w in mine:
        w.where = where
    seq.warnings = mine
    warnings.extend(mine)
    return seq


# --- public entry point ----------------------------------------------------

def parse_file(text: str) -> ParseResult:
    result = ParseResult()
    for name, start, _end in iter_function_spans(text):
        body = text[start:_end]
        # 1-based file line the body starts on, so weights carry file line
        # numbers the writer can splice by
        offset = text.count("\n", 0, start) + 1
        if re.match(r"Act\d+$", name):
            result.sequences.append(_parse_act(name, body, result.warnings, offset))
        elif re.match(r"Kengeki\d+$", name):
            result.sequences.append(
                _parse_kengeki_move(name, body, result.warnings, offset))
        elif name == "Kengeki_Activate":
            result.activators.append(
                _parse_kengeki_activate(body, result.warnings, offset))
        elif name == "Activate":
            result.activators.append(
                _parse_activate(body, result.warnings, offset))
        elif name == "Interrupt":
            result.sequences.extend(
                _parse_interrupt(body, result.warnings, offset))
    return result
