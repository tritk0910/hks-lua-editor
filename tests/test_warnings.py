"""Parse warnings: where they came from, and whether rewriting loses them.

The point of these is the `lossy` flag: a `skipped non-combo if` is not in the
model, so regenerating that combo deletes it — that has to be visible before the
write. (A chained `:Timing` call used to be lossy too; it is now kept verbatim on
the step, see test_raw_lines / the round-trip test below.)
"""

from models import ComboSequence

# Act24 has a lossy `skipped non-combo if`; Act04 is understood completely.
LOSSY_COMBO = "Act24"


def _combo(parsed, name):
    return next(s for s in parsed.sequences if s.name == name)


def _skipped_ifs(warnings):
    return [w for w in warnings if "skipped non-combo if" in w.message]


# --- what a warning carries -------------------------------------------------

def test_every_warning_points_at_a_real_file_line(parsed, text):
    lines = text.split("\n")
    assert parsed.warnings
    for w in parsed.warnings:
        assert w.line, f"no line on {w.message}"
        assert 1 <= w.line <= len(lines)


def test_a_skipped_if_points_at_an_if_line(parsed, text):
    """Not just 'has a line' — the line must really hold the if we skipped."""
    lines = text.split("\n")
    hits = _skipped_ifs(parsed.warnings)
    assert hits
    for w in hits[:5]:
        assert lines[w.line - 1].strip().startswith(("if ", "elseif "))


def test_warnings_name_the_function_they_came_from(parsed):
    combo = _combo(parsed, LOSSY_COMBO)
    assert combo.warnings
    assert all(w.where == LOSSY_COMBO for w in combo.warnings)


# --- lossy is the flag that matters -----------------------------------------

def test_a_skipped_if_is_lossy_but_a_raw_condition_is_not(parsed):
    """A condition we can't decompose is still emitted verbatim, so it survives
    a rewrite; a skipped `if` block does not."""
    skipped = _skipped_ifs(parsed.warnings)
    raw = [w for w in parsed.warnings if "kept raw" in w.message]
    assert skipped and raw
    assert all(w.lossy for w in skipped)
    assert not any(w.lossy for w in raw)


def test_a_skipped_if_block_is_lossy(parsed):
    skipped = _skipped_ifs(parsed.warnings)
    assert skipped and all(w.lossy for w in skipped)


def test_rewriting_a_flagged_combo_really_does_drop_the_block(parsed, text):
    """Proves the flag isn't just a label: the skipped block is in the file but
    not in the Lua we would write over it."""
    from generator import generate_act
    combo = _combo(parsed, LOSSY_COMBO)
    warning = _skipped_ifs(combo.warnings)[0]
    src_if = text.split("\n")[warning.line - 1].strip()   # e.g. `if SpaceCheck(...`
    cond = src_if.split(" then")[0]
    assert cond and cond not in generate_act(combo)       # gone once rewritten


def test_a_chained_timing_call_now_round_trips(parsed):
    """The former lossy case: a chained :Timing call is kept on the step and
    re-emitted, so it is NOT dropped."""
    from generator import generate_kengeki_move
    from models import ComboStep

    def steps(items):
        for it in items:
            if isinstance(it, ComboStep):
                yield it
            elif hasattr(it, "true_branch"):
                yield from steps(it.true_branch)
                yield from steps(it.false_branch)

    ken = _combo(parsed, "Kengeki02")
    assert any(":Timing" in s.chained for s in steps(ken.steps))
    assert ":Timing" in generate_kengeki_move(ken)        # re-emitted, not lost


def test_combos_the_parser_fully_understood_carry_no_warnings(parsed):
    assert _combo(parsed, "Act04").warnings == []


def test_a_combo_built_in_the_editor_has_no_warnings():
    assert ComboSequence(name="new", trigger_type="act_entry", trigger_id=1).warnings == []


# --- the UI surface ---------------------------------------------------------

def test_the_warnings_tab_lists_them_and_can_jump(window, ref_lua, monkeypatch):
    assert window._load_path(ref_lua)
    window.seq = _combo_in(window, LOSSY_COMBO)
    window.refresh()

    labels = [window.warning_view.item(i).text()
              for i in range(window.warning_view.count())]
    assert any(LOSSY_COMBO in t for t in labels)
    assert f"({len(window._warnings)})" in window.tabs.tabText(2)

    jumped = []
    monkeypatch.setattr(type(window), "_open_at_line",
                        lambda self, path, line: jumped.append(line))
    row = next(window.warning_view.item(i) for i in range(window.warning_view.count())
               if window.warning_view.item(i).data(0x0100) is not None)
    window._open_warning(row)
    assert jumped and jumped[0] in [w.line for w in window._warnings]


def _combo_in(window, name):
    return next(c for c in window.combos if getattr(c, "name", None) == name)


def test_writing_a_lossy_combo_says_what_it_will_drop(window, ref_lua, dialogs,
                                                      monkeypatch):
    assert window._load_path(ref_lua)
    window.seq = _combo_in(window, LOSSY_COMBO)
    window.seq.steps[0].anim_id = 4321          # make it an actual change
    window._write_to_file()
    assert dialogs.questions, "no confirmation was shown"
    prompt = dialogs.questions[-1]
    assert "could not read" in prompt and "DROP" in prompt
    assert "skipped non-combo if" in prompt


def test_writing_a_clean_combo_does_not_cry_wolf(window, ref_lua, dialogs):
    assert window._load_path(ref_lua)
    window.seq = _combo_in(window, "Act04")
    window.seq.steps[0].anim_id = 4321
    window._write_to_file()
    assert dialogs.questions
    assert "DROP" not in dialogs.questions[-1]


def test_selectors_are_spliced_so_nothing_is_at_risk(window, ref_lua):
    from models import ActActivator
    assert window._load_path(ref_lua)
    window.seq = next(c for c in window.combos if isinstance(c, ActActivator))
    assert window._lossy_warnings() == []


def test_close_file_clears_the_warnings(window, ref_lua, dialogs):
    from PySide6.QtWidgets import QMessageBox
    assert window._load_path(ref_lua)
    assert window._warnings
    dialogs.answer = QMessageBox.Discard
    window._close_file()
    assert window._warnings == []
    assert window.warning_view.count() == 0
