"""Undo / redo / revert.

This had no tests at all, which is how it came to be silently broken for the
weight selectors: _push_undo was gated on _is_combo() from the days when those
were view-only, so nothing was ever recorded for them.
"""

import pytest

import writer
from models import ActActivator, ComboSequence, ComboStep, KengekiActivator


def _weights(activator):
    return writer._activator_parts(activator)[1]


@pytest.fixture(params=[ActActivator, KengekiActivator],
                ids=["activate", "kengeki_activate"])
def selector(request, window, ref_lua):
    """The window showing one of the two weight selectors, loaded from file."""
    assert window._load_path(ref_lua)
    window.seq = next(c for c in window.combos if isinstance(c, request.param))
    window._sync_form_from_seq()
    window.refresh()
    return window


def _first_weight_row(window):
    for item in window._iter_tree_items():
        data = window._payload_of(item)
        if data and data["kind"] == "weight":
            return item, data["obj"]
    raise AssertionError("no weight row")


# --- combos (never had a test either) ---------------------------------------

def test_undo_and_redo_an_added_step(window):
    # must live in `combos`: _replace_current looks the current one up there
    window.seq = window._tag(ComboSequence(name="c", trigger_type="act_entry",
                                           trigger_id=1, steps=[]))
    window.combos.append(window.seq)
    window.refresh()

    window._push_undo()
    window.seq.steps.append(ComboStep("ComboFinal", 3000, 10))
    window.refresh()
    assert len(window.seq.steps) == 1

    window._undo()
    assert window.seq.steps == []
    window._redo()
    assert len(window.seq.steps) == 1


def test_history_does_not_leak_between_combos(window):
    a = window._tag(ComboSequence(name="a", trigger_type="act_entry", trigger_id=1,
                                  steps=[]))
    b = window._tag(ComboSequence(name="b", trigger_type="act_entry", trigger_id=2,
                                  steps=[]))
    window.combos += [a, b]
    window.seq = a
    window._push_undo()
    a.steps.append(ComboStep("ComboFinal", 1, 10))

    window.seq = b                      # b has no history of its own
    window._undo()
    assert b.steps == []
    assert len(a.steps) == 1            # a was not touched


# --- selectors: the reported bug --------------------------------------------

def test_undo_and_redo_a_removed_weight(selector):
    window = selector
    before = len(_weights(window.seq))
    row, _ = _first_weight_row(window)
    window.tree.setCurrentItem(row)
    window._remove_selected()
    assert len(_weights(window.seq)) == before - 1

    window._undo()
    assert len(_weights(window.seq)) == before
    window._redo()
    assert len(_weights(window.seq)) == before - 1
    window._undo()
    assert len(_weights(window.seq)) == before


def test_undo_restores_a_weights_line_not_just_the_count(selector, ref_lua):
    """A weight without its source line can't be written back to the right
    place, so counting alone would let a broken restore pass."""
    window = selector
    _row, weight = _first_weight_row(window)
    index, line = weight.index, weight.line
    window.tree.setCurrentItem(_row)
    window._remove_selected()
    window._undo()

    restored = [w for w in _weights(window.seq)
                if w.index == index and w.line == line]
    assert restored, f"{index} came back without its line {line}"
    # the real proof: writing the restored model changes nothing on disk
    text = open(ref_lua, encoding="utf-8").read()
    out, summary = writer.apply_activator(text, window.seq)
    assert out == text and summary == []


def test_undo_an_edited_weight_value(selector):
    window = selector
    row, weight = _first_weight_row(window)
    old = weight.value

    # setText fires itemChanged, which commits the edit — calling
    # _on_item_changed by hand as well would push a second undo snapshot taken
    # *after* the change, and undo would then restore the new value
    row.setText(1, "777")
    assert _weights(window.seq)[0].value == 777

    window._undo()
    assert _weights(window.seq)[0].value == old


def test_revert_restores_a_selector_to_its_loaded_state(selector, dialogs):
    window = selector
    before = len(_weights(window.seq))
    row, _ = _first_weight_row(window)
    window.tree.setCurrentItem(row)
    window._remove_selected()
    assert len(_weights(window.seq)) == before - 1

    window._revert_changes()            # dialogs fixture answers Yes
    assert len(_weights(window.seq)) == before
