"""Parse warnings: where they came from, and whether rewriting loses them.

The point of these is the `lossy` flag. A dropped `:TimingSetNumber(...)` is not
in the model, so regenerating that combo deletes it from the user's file — that
has to be visible before the write, not a number in a status bar.
"""

from models import ComboSequence


def _combo(parsed, name):
    return next(s for s in parsed.sequences if s.name == name)


def _dropped_calls(warnings):
    return [w for w in warnings if "chained call" in w.message]


# --- what a warning carries -------------------------------------------------

def test_every_warning_points_at_a_real_file_line(parsed, text):
    lines = text.split("\n")
    assert parsed.warnings
    for w in parsed.warnings:
        assert w.line, f"no line on {w.message}"
        assert 1 <= w.line <= len(lines)


def test_a_dropped_chained_call_points_at_the_line_that_has_it(parsed, text):
    """Not just 'has a line' — the line must really hold the call we dropped."""
    lines = text.split("\n")
    for w in _dropped_calls(parsed.warnings)[:5]:
        source = lines[w.line - 1]
        assert "AddSubGoal(" in source
        # the reference wraps some of these, so the chain may be on the next line
        assert ":Timing" in source + lines[w.line]


def test_warnings_name_the_function_they_came_from(parsed):
    act14 = _combo(parsed, "Act14")
    assert act14.warnings
    assert all(w.where == "Act14" for w in act14.warnings)


# --- lossy is the flag that matters -----------------------------------------

def test_a_dropped_call_is_lossy_but_a_raw_condition_is_not(parsed):
    """A condition we can't decompose is still emitted verbatim, so it survives
    a rewrite; a dropped chained call does not."""
    dropped = _dropped_calls(parsed.warnings)
    raw = [w for w in parsed.warnings if "kept raw" in w.message]
    assert dropped and raw
    assert all(w.lossy for w in dropped)
    assert not any(w.lossy for w in raw)


def test_a_skipped_if_block_is_lossy(parsed):
    skipped = [w for w in parsed.warnings if "skipped non-combo if" in w.message]
    assert skipped and all(w.lossy for w in skipped)


def test_rewriting_a_flagged_combo_really_does_drop_the_call(parsed, text):
    """Proves the flag isn't just a label: the call is in the file now, and is
    simply not in the Lua we would write over it."""
    from generator import generate_act
    act14 = _combo(parsed, "Act14")
    warning = _dropped_calls(act14.warnings)[0]
    assert "TimingSetNumber" in warning.message
    # the source still has it (the file wraps these, so look around the line —
    # the warning text itself is whitespace-normalised and won't match verbatim)
    lines = text.split("\n")
    assert ":Timing" in "".join(lines[warning.line - 1:warning.line + 1])
    # and it is nowhere in the function we would replace it with
    assert ":Timing" not in generate_act(act14)


def test_combos_the_parser_fully_understood_carry_no_warnings(parsed):
    assert _combo(parsed, "Act04").warnings == []


def test_a_combo_built_in_the_editor_has_no_warnings():
    assert ComboSequence(name="new", trigger_type="act_entry", trigger_id=1).warnings == []


# --- the UI surface ---------------------------------------------------------

def test_the_warnings_tab_lists_them_and_can_jump(window, ref_lua, monkeypatch):
    assert window._load_path(ref_lua)
    window.seq = _combo_in(window, "Act14")
    window.refresh()

    labels = [window.warning_view.item(i).text()
              for i in range(window.warning_view.count())]
    assert any("Act14" in t for t in labels)
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
    window.seq = _combo_in(window, "Act14")
    window.seq.steps[0].anim_id = 4321          # make it an actual change
    window._write_to_file()
    assert dialogs.questions, "no confirmation was shown"
    prompt = dialogs.questions[-1]
    assert "could not read" in prompt and "DROP" in prompt
    assert ":Timing" in prompt


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
