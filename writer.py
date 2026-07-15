"""Splice generated Lua back into an existing `.lua` (HKS) behavior file.

Deliberately does TARGETED text surgery — it replaces/inserts only the region
for the combo being written and leaves the rest of the file byte-for-byte
untouched. It never re-generates the whole file from the parsed model (the
parser is tolerant and drops un-modelled bits, so a full round-trip would lose
them). Always back up before overwriting.

UI-agnostic: depends only on parser (offsets), generator (snippets), and the
data model.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime

import generator
from models import (
    ActActivator, Branch, ComboSequence, ComboStep, Weight, is_activator,
)
from parser import iter_function_spans

# Which enclosing Goal function holds the REGIST_FUNC / SetCoolTime block for a
# given combo family, and the default table variable names used there.
_FAMILY_ENCLOSING = {"Act": "Activate", "Kengeki": "Kengeki_Activate"}
_FAMILY_REGIST_VAR = {"Act": "local1", "Kengeki": "local2"}
_FAMILY_COOL_VAR = {"Act": "act", "Kengeki": "kengeki"}


def _line_starts(text: str):
    """Return (list_of_line_offsets, list_of_lines) with newline stripped."""
    lines = text.split("\n")
    offsets, off = [], 0
    for ln in lines:
        offsets.append(off)
        off += len(ln) + 1
    return offsets, lines


# --- whole-function replace / insert --------------------------------------

def _function_family_anchor(text: str, name: str) -> int:
    """Offset at which to insert a new function `name`.

    Prefer just after the last function of the same family (Act*/Kengeki*),
    else before Goal.Interrupt, else EOF.
    """
    prefix = "Kengeki" if name.startswith("Kengeki") else ("Act" if name.startswith("Act") else "")
    spans = list(iter_function_spans(text))
    last_family_end = None
    interrupt_start = None
    for fname, start, end in spans:
        if prefix and fname.startswith(prefix):
            last_family_end = end
        if fname == "Interrupt" and interrupt_start is None:
            interrupt_start = start
    if last_family_end is not None:
        return last_family_end
    if interrupt_start is not None:
        return interrupt_start
    return len(text)


def replace_or_insert_function(text: str, name: str, func_text: str) -> str:
    """Replace `Goal.<name>` with func_text, or insert it if absent."""
    for fname, start, end in iter_function_spans(text):
        if fname == name:
            return text[:start] + func_text + "\n\n" + text[end:]
    anchor = _function_family_anchor(text, name)
    return text[:anchor] + func_text + "\n\n" + text[anchor:]


def function_exists(text: str, name: str) -> bool:
    return any(fname == name for fname, _s, _e in iter_function_spans(text))


# --- special-effect registration ------------------------------------------

def ensure_registration(text: str, effect_id: int, target: str = "TARGET_SELF") -> str:
    """Add the AddObserveSpecialEffectAttribute line in Goal.Activate if the
    (target, effect_id) pair is not already registered."""
    if not generator.needs_registration(effect_id, target, text):
        return text
    line = generator.registration_line(effect_id, target)
    # locate Goal.Activate span
    span = next(((s, e) for n, s, e in iter_function_spans(text) if n == "Activate"), None)
    if span is None:
        return text  # no Activate to register in; leave untouched
    start, end = span
    region = text[start:end]
    marker = "AddObserveSpecialEffectAttribute"
    pos = region.rfind(marker)
    if pos != -1:
        eol = region.find("\n", pos)
        eol = end if eol == -1 else start + eol
        return text[:eol] + "\n" + line + text[eol:]
    # no existing registration: insert after the function header line
    header_eol = text.find("\n", start)
    return text[:header_eol] + "\n" + line + text[header_eol:]


# --- REGIST_FUNC + SetCoolTime for new Act/Kengeki ------------------------

def _enclosing_span(text: str, family: str):
    """(start, end) of the Goal function that holds the family's REGIST block."""
    name = _FAMILY_ENCLOSING[family]
    return next(((s, e) for n, s, e in iter_function_spans(text) if n == name), None)


def _span_lines(text: str, span):
    """Yield (line_index, offset, line) for each line whose start is in span."""
    start, end = span
    offsets, lines = _line_starts(text)
    for i, off in enumerate(offsets):
        if start <= off < end:
            yield i, off, lines[i]


def _first_spin_anim(seq: ComboSequence):
    """Anim id of the first ComboAttackTunableSpin step (recursing branches);
    fall back to the first step's anim; None if the combo is empty."""
    first = [None]

    def walk(items):
        for it in items:
            if isinstance(it, ComboStep):
                if first[0] is None:
                    first[0] = it.anim_id
                if it.goal_type == "ComboAttackTunableSpin":
                    return it.anim_id
            elif isinstance(it, Branch):
                for body in (it.true_branch, it.false_branch):
                    hit = walk(body)
                    if hit is not None:
                        return hit
        return None

    spin = walk(seq.steps)
    return spin if spin is not None else first[0]


def _indexed_line_insert(text, span, pattern, num, make_line):
    """Insert a line among the `span` lines matching `pattern` (capturing
    (indent, var, index)), keeping numeric order. `make_line(indent, var)`
    builds the new line from the detected indent/table-variable. Returns
    (text, added): added is True (inserted), False (num already present), or
    None (no matching line found — caller should fall back)."""
    pat = re.compile(pattern)
    matches = []   # (offset, line, indent, var, idx)
    for _i, off, line in _span_lines(text, span):
        m = pat.match(line)
        if m:
            matches.append((off, line, m.group(1), m.group(2), int(m.group(3))))
    if not matches:
        return text, None
    if any(idx == num for *_r, idx in matches):
        return text, False
    indent, var = matches[0][2], matches[0][3]
    new_line = make_line(indent, var)
    for off, line, _ind, _var, idx in matches:
        if idx > num:
            return text[:off] + new_line + "\n" + text[off:], True
    off, line, *_ = matches[-1]
    pos = off + len(line)
    return text[:pos] + "\n" + new_line + text[pos:], True


def _fallback_insert_before_end(text, span, new_line):
    """Insert `new_line` just before the enclosing function's final `end`."""
    last_end = None
    for _i, off, line in _span_lines(text, span):
        if line.strip() == "end":
            last_end = off
    if last_end is None:
        return text, False
    return text[:last_end] + new_line + "\n" + text[last_end:], True


def ensure_regist_func(text: str, family: str, num: int) -> tuple[str, bool]:
    """Add `<var>[num] = REGIST_FUNC(arg1, arg2, arg0.<Family>NN)` in the
    enclosing Goal function, in numeric order. No-op if already present."""
    span = _enclosing_span(text, family)
    if span is None:
        return text, False
    make = lambda indent, var: (
        f"{indent}{var}[{num}] = REGIST_FUNC(arg1, arg2, arg0.{family}{num:02d})")
    pattern = rf"^(\s*)(\w+)\[(\d+)\] = REGIST_FUNC\(arg1, arg2, arg0\.{family}\d+\)\s*$"
    new_text, added = _indexed_line_insert(text, span, pattern, num, make)
    if added is None:
        # F: no REGIST block for this family -> defaults, insert before `end`
        return _fallback_insert_before_end(
            text, span, make("    ", _FAMILY_REGIST_VAR[family]))
    return new_text, bool(added)


def ensure_cooltime(text: str, family: str, num: int, spin_anim, seconds: int
                    ) -> tuple[str, bool]:
    """Add `<var>[num] = SetCoolTime(arg1, arg2, <spin_anim>, <seconds>,
    <var>[num], 1)` in numeric order. No-op if already present."""
    span = _enclosing_span(text, family)
    if span is None or spin_anim is None:
        return text, False
    make = lambda indent, var: (
        f"{indent}{var}[{num}] = SetCoolTime(arg1, arg2, {spin_anim}, "
        f"{seconds}, {var}[{num}], 1)")
    pattern = r"^(\s*)(\w+)\[(\d+)\] = SetCoolTime\("
    new_text, added = _indexed_line_insert(text, span, pattern, num, make)
    if added is not None:
        return new_text, bool(added)
    # F: no SetCoolTime block -> defaults, insert right before the family REGIST
    line = make("    ", _FAMILY_COOL_VAR[family])
    regist = re.compile(
        rf"^\s*\w+\[\d+\] = REGIST_FUNC\(arg1, arg2, arg0\.{family}\d+\)\s*$")
    for _i, off, ln in _span_lines(text, span):
        if regist.match(ln):
            return text[:off] + line + "\n" + text[off:], True
    return _fallback_insert_before_end(text, span, line)


# --- interrupt elseif chain -----------------------------------------------

def _interrupt_chain_end(text: str):
    """Char offset of the `end` line (indent 8) that closes the
    `if interruptEffectIdentifier == ...` chain, or None."""
    offsets, lines = _line_starts(text)
    start_idx = next((i for i, ln in enumerate(lines)
                      if ln.strip().startswith("if interruptEffectIdentifier ==")), None)
    if start_idx is None:
        return None
    depth = 0
    for i in range(start_idx, len(lines)):
        s = lines[i].strip()
        if (s.startswith("if ") and s.endswith("then")) or s.startswith("for ") or s.startswith("while "):
            depth += 1
        elif s == "end":
            depth -= 1
            if depth == 0:
                return offsets[i]
    return None


def _existing_branch_span(text: str, effect_id: int):
    """(start, end) char span of an existing pure `elseif interruptEffect...`
    branch for effect_id, or None. `end` is the start of the next
    elseif/else/end at the same indent."""
    offsets, lines = _line_starts(text)
    target = f"elseif interruptEffectIdentifier == {effect_id} then"
    for i, ln in enumerate(lines):
        if ln.strip() == target:
            indent = len(ln) - len(ln.lstrip())
            for j in range(i + 1, len(lines)):
                sj = lines[j]
                ind_j = len(sj) - len(sj.lstrip())
                strip_j = sj.strip()
                if ind_j <= indent and (strip_j.startswith("elseif ")
                                        or strip_j == "else" or strip_j == "end"):
                    return offsets[i], offsets[j]
            return offsets[i], len(text)
    return None


def upsert_interrupt_branch(text: str, effect_id: int, branch_text: str) -> str:
    """Insert (or replace) an `elseif interruptEffectIdentifier == id` block."""
    existing = _existing_branch_span(text, effect_id)
    if existing is not None:
        start, end = existing
        return text[:start] + branch_text + "\n" + text[end:]
    anchor = _interrupt_chain_end(text)
    if anchor is None:
        raise ValueError("could not locate the interrupt identifier chain to insert into")
    return text[:anchor] + branch_text + "\n" + text[anchor:]


# --- dispatch + file write ------------------------------------------------

def _apply_new_function(text, seq, family, num, func_text, cooldown, summary):
    """Insert/replace a Goal function; on a NEW function also add its
    REGIST_FUNC line and (if a cooldown is given) a SetCoolTime line."""
    name = f"{family}{num:02d}"
    is_new = not function_exists(text, name)
    text = replace_or_insert_function(text, name, func_text)
    summary.append(f"{'Insert' if is_new else 'Replace'} Goal.{name}")
    if is_new:
        text, added = ensure_regist_func(text, family, num)
        if added:
            summary.append(f"Register {family}{num:02d}")
        if cooldown is not None:
            spin = _first_spin_anim(seq)
            text, added = ensure_cooltime(text, family, num, spin, cooldown)
            if added:
                summary.append(f"Add cooldown {cooldown}s (anim {spin})")
    return text


def apply_sequence(text: str, seq, cooldown=None, target="TARGET_SELF"
                   ) -> tuple[str, list[str]]:
    """Return (new_text, summary) after splicing `seq` into `text`. For a NEW
    Act/Kengeki, also add its REGIST_FUNC line and — if `cooldown` (seconds) is
    given — a SetCoolTime line. For an interrupt, `target` is the observe target
    (TARGET_SELF or TARGET_ENE_0) used when registering the special effect."""
    if is_activator(seq):
        return apply_activator(text, seq)
    if not isinstance(seq, ComboSequence):
        return text, [f"cannot write {type(seq).__name__}"]

    summary: list[str] = []
    if seq.trigger_type == "act_entry":
        text = _apply_new_function(text, seq, "Act", seq.trigger_id,
                                   generator.generate_act(seq), cooldown, summary)
    elif seq.trigger_type == "kengeki_move":
        text = _apply_new_function(text, seq, "Kengeki", seq.trigger_id,
                                   generator.generate_kengeki_move(seq), cooldown, summary)
    elif seq.trigger_type == "special_effect":
        if generator.needs_registration(seq.trigger_id, target, text):
            text = ensure_registration(text, seq.trigger_id, target)
            summary.append(f"Add registration for {seq.trigger_id} ({target})")
        existed = _existing_branch_span(text, seq.trigger_id) is not None
        text = upsert_interrupt_branch(
            text, seq.trigger_id, generator.generate_interrupt_branch(seq))
        summary.append(f"{'Replace' if existed else 'Insert'} interrupt "
                       f"branch {seq.trigger_id}")
    else:
        summary.append(f"unknown trigger_type: {seq.trigger_type}")
    return text, summary


# --- selector weight tables (Goal.Activate / Goal.Kengeki_Activate) --------

def _flatten_weights(items) -> list:
    """Weights in document order (a branch's true side precedes its false side,
    which is how an if/elseif/else ladder reads top-to-bottom)."""
    out = []
    for it in items:
        if isinstance(it, Weight):
            out.append(it)
        elif isinstance(it, Branch):
            out.extend(_flatten_weights(it.true_branch))
            out.extend(_flatten_weights(it.false_branch))
    return out


def _activator_parts(activator):
    """(table_name, weight_items) for either activator type."""
    if isinstance(activator, ActActivator):
        return "act", _flatten_weights(activator.items)
    items = []
    for block in activator.blocks:
        items.extend(_flatten_weights(block.items))
    return "kengeki", items


def _scan_weight_lines(text: str, table: str) -> dict:
    """{file_line: (indent, index, value_text)} for every `<table>[i] = v`
    assignment. SetCoolTime lines share the shape but are cooldowns, not
    weights."""
    pat = re.compile(rf"^(\s*){table}\[(\d+)\]\s*=\s*(.+?)\s*$")
    found = {}
    for n, line in enumerate(text.split("\n"), 1):
        m = pat.match(line)
        if m and "SetCoolTime(" not in line:
            found[n] = (m.group(1), int(m.group(2)), m.group(3))
    return found


def apply_activator(text: str, activator) -> tuple[str, list[str]]:
    """Write an edited weight table back by splicing individual LINES.

    Deliberately not a re-generate: the selector regions hold comments and
    statements the model doesn't carry (e.g. `arg1:SetNumber(2, 0)`), and
    rendering the region from the model would silently delete them. Only lines
    for weights that actually changed are touched.

    Only lines in `activator.owned_lines` are ever touched: these parsers skip
    some weight assignments (Kengeki_Activate's trailing veto blocks), and
    treating "on disk but not in the model" as a deletion would wipe them.

    Refuses to write (returning a warning) if the model no longer lines up with
    the file, rather than risk editing the wrong line.
    """
    table, weights = _activator_parts(activator)
    owned = getattr(activator, "owned_lines", set())
    on_disk = {n: v for n, v in _scan_weight_lines(text, table).items()
               if n in owned}
    summary: list[str] = []

    # integrity: every tracked weight must still sit on its line, same index
    for w in weights:
        if w.line is None:
            continue
        if w.line not in on_disk:
            return text, [f"{table}[{w.index}]: line {w.line} is no longer a "
                          f"{table}[] assignment — reload the file before writing."]
        if on_disk[w.line][1] != w.index:
            return text, [f"line {w.line} holds {table}[{on_disk[w.line][1]}], "
                          f"expected {table}[{w.index}] — reload the file before writing."]

    # (line, rank, new_text_or_None) — rank orders ops landing on the same line
    ops: list[tuple[int, int, str | None]] = []
    tracked = {w.line for w in weights if w.line is not None}
    for w in weights:
        if w.line is None:
            continue
        indent, _idx, old_value = on_disk[w.line]
        if str(w.value) != old_value:
            ops.append((w.line, 1, f"{indent}{table}[{w.index}] = {w.value}"))
            summary.append(f"{table}[{w.index}] = {w.value} (was {old_value}, line {w.line})")
    for line in on_disk:
        if line not in tracked:
            ops.append((line, 1, None))   # removed in the editor
            summary.append(f"remove {table}[{on_disk[line][1]}] (line {line})")

    # new weights (line=None) go after the last existing weight of their block
    for after, weight in _plan_inserts(activator, table):
        if after is None:
            summary.append(f"cannot place new {table}[{weight.index}] — add it "
                           f"next to an existing weight")
            continue
        indent = on_disk.get(after, ("    ",))[0]
        ops.append((after, 0, f"{indent}{table}[{weight.index}] = {weight.value}"))
        summary.append(f"add {table}[{weight.index}] = {weight.value} (after line {after})")

    if not ops:
        return text, summary
    lines = text.split("\n")
    # apply bottom-up: editing/deleting/inserting low lines first would shift
    # every line number above them (rank 0 = insert, applied before a replace
    # that targets the same line)
    for line, rank, new_text in sorted(ops, reverse=True):
        if rank == 0:
            lines.insert(line, new_text)      # after `line` (1-based)
        elif new_text is None:
            del lines[line - 1]
        else:
            lines[line - 1] = new_text
    return "\n".join(lines), summary


def _plan_inserts(activator, table: str) -> list:
    """[(after_line, weight)] for weights added in the editor (line is None).

    A new weight is placed after the last already-on-disk weight of the same
    block, so it lands inside the same condition. after_line is None when the
    block has none to anchor to.
    """
    out = []

    def walk(items):
        anchor = max((w.line for w in items
                      if isinstance(w, Weight) and w.line is not None), default=None)
        for it in items:
            if isinstance(it, Weight) and it.line is None:
                out.append((anchor, it))
            elif isinstance(it, Branch):
                walk(it.true_branch)
                walk(it.false_branch)

    if isinstance(activator, ActActivator):
        walk(activator.items)
    else:
        for block in activator.blocks:
            walk(block.items)
    return out


# --- remove from file -----------------------------------------------------

def _remove_first_line(text: str, pattern: str, span=None) -> tuple[str, bool]:
    """Delete the first line (with its newline) matching `pattern`. If `span`
    is given, only lines starting inside it are considered."""
    pat = re.compile(pattern)
    offsets, lines = _line_starts(text)
    for i, off in enumerate(offsets):
        if span is not None and not (span[0] <= off < span[1]):
            continue
        if pat.match(lines[i]):
            end = off + len(lines[i]) + 1   # include trailing newline
            return text[:off] + text[end:], True
    return text, False


def remove_function(text: str, name: str) -> tuple[str, list[str]]:
    """Delete Goal.<name> plus its REGIST_FUNC line and matching SetCoolTime
    line (same family/index). Returns (new_text, summary)."""
    summary: list[str] = []
    for fname, start, end in iter_function_spans(text):
        if fname == name:
            text = text[:start] + text[end:]
            summary.append(f"Remove Goal.{name}")
            break
    else:
        return text, summary   # nothing to remove
    family = "Kengeki" if name.startswith("Kengeki") else "Act"
    num = int(re.search(r"\d+", name).group())
    text, removed = _remove_first_line(
        text, rf"^\s*\w+\[\d+\] = REGIST_FUNC\(arg1, arg2, arg0\.{re.escape(name)}\)\s*$")
    if removed:
        summary.append(f"Remove REGIST_FUNC {name}")
    span = _enclosing_span(text, family)   # scope cooldown removal to right table
    text, removed = _remove_first_line(
        text, rf"^\s*\w+\[{num}\] = SetCoolTime\(", span)
    if removed:
        summary.append(f"Remove cooldown {name}")
    return text, summary


def remove_interrupt_branch(text: str, effect_id: int) -> tuple[str, list[str]]:
    """Delete an `elseif interruptEffectIdentifier == id` branch."""
    span = _existing_branch_span(text, effect_id)
    if span is None:
        return text, []
    start, end = span
    return text[:start] + text[end:], [f"Remove interrupt branch {effect_id}"]


def remove_registration(text: str, effect_id: int, target: str | None = None
                        ) -> tuple[str, list[str]]:
    """Delete `arg1:AddObserveSpecialEffectAttribute(<target>, id)` in
    Goal.Activate. target=None removes BOTH TARGET_SELF and TARGET_ENE_0."""
    span = _enclosing_span(text, "Act")   # Goal.Activate
    if span is None:
        return text, []
    targets = [target] if target else ["TARGET_SELF", "TARGET_ENE_0"]
    removed: list[str] = []
    for tgt in targets:
        pattern = (rf"^\s*arg1:AddObserveSpecialEffectAttribute\(\s*{tgt}\s*,"
                   rf"\s*{effect_id}\s*\)\s*$")
        text, hit = _remove_first_line(text, pattern, _enclosing_span(text, "Act"))
        if hit:
            removed.append(f"Remove registration {effect_id} ({tgt})")
    return text, removed


def _unique_backup_path(path: str) -> str:
    """A backup path that does not clobber an existing one: `<path>.bak`, else a
    timestamped `<path>.<YYYYmmdd-HHMMSS>.bak`, else a numbered fallback."""
    plain = path + ".bak"
    if not os.path.exists(plain):
        return plain
    stamped = f"{path}.{datetime.now():%Y%m%d-%H%M%S}.bak"
    if not os.path.exists(stamped):
        return stamped
    i = 1
    while os.path.exists(f"{stamped}.{i}"):
        i += 1
    return f"{stamped}.{i}"


def write_file(path: str, text: str, backup: bool = True) -> str | None:
    """Write `text` to `path` (UTF-8). If backup and the file exists, copy it to
    a NON-clobbering backup path first (never overwrites an existing backup).
    Returns the backup path (or None)."""
    backup_path = None
    if backup and os.path.exists(path):
        backup_path = _unique_backup_path(path)
        shutil.copy2(path, backup_path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return backup_path
