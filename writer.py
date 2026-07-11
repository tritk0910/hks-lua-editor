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
import shutil
from datetime import datetime

import generator
from models import ComboSequence, KengekiActivator
from parser import iter_function_spans


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

def apply_sequence(text: str, seq) -> tuple[str, list[str]]:
    """Return (new_text, summary) after splicing `seq` into `text`."""
    if isinstance(seq, KengekiActivator):
        return text, ["Kengeki_Activate write is not supported yet (view only)."]
    if not isinstance(seq, ComboSequence):
        return text, [f"cannot write {type(seq).__name__}"]

    summary: list[str] = []
    if seq.trigger_type == "act_entry":
        name = f"Act{seq.trigger_id:02d}"
        verb = "Replace" if function_exists(text, name) else "Insert"
        text = replace_or_insert_function(text, name, generator.generate_act(seq))
        summary.append(f"{verb} Goal.{name}")
    elif seq.trigger_type == "kengeki_move":
        name = f"Kengeki{seq.trigger_id:02d}"
        verb = "Replace" if function_exists(text, name) else "Insert"
        text = replace_or_insert_function(text, name, generator.generate_kengeki_move(seq))
        summary.append(f"{verb} Goal.{name}")
    elif seq.trigger_type == "special_effect":
        if generator.needs_registration(seq.trigger_id, "TARGET_SELF", text):
            text = ensure_registration(text, seq.trigger_id, "TARGET_SELF")
            summary.append(f"Add registration for {seq.trigger_id}")
        existed = _existing_branch_span(text, seq.trigger_id) is not None
        text = upsert_interrupt_branch(
            text, seq.trigger_id, generator.generate_interrupt_branch(seq))
        summary.append(f"{'Replace' if existed else 'Insert'} interrupt "
                       f"branch {seq.trigger_id}")
    else:
        summary.append(f"unknown trigger_type: {seq.trigger_type}")
    return text, summary


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
