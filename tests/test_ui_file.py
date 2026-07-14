"""Regression tests for the window's file-facing behaviour: duplicate-blocking
when creating combos, Open Recent, keeping the in-memory file text in sync after
writing, finding special effects, and closing/deleting combos.
"""

import os

import pytest
from PySide6.QtWidgets import QDialog, QInputDialog

import ui.main_window as mw
from models import ComboSequence, ComboStep

NEW_EFFECT = 424242          # an id the reference file does not use


def _interrupt(eid=NEW_EFFECT):
    return ComboSequence(name="i", trigger_type="special_effect", trigger_id=eid,
                         steps=[ComboStep("ComboFinal", 3049, 10, extra_args=[0])])


def _loaded(window, ref_lua):
    assert window._load_path(ref_lua)
    return window


def _file_only(window, ref_lua):
    """Give the window the file's text WITHOUT parsing its combos into memory.

    Loading normally would also put Act04/Kengeki01 in `combos`, so the
    in-memory duplicate check would fire first and the file check would never be
    reached. This isolates the file branch of _combo_conflict.
    """
    with open(ref_lua, encoding="utf-8") as f:
        window._loaded_text = f.read()
    return window


def _stub_combo_dialog(monkeypatch, name, ttype, tid, accept=True):
    class Dlg:
        def __init__(self, *a, **k): pass
        def exec(self):
            return QDialog.Accepted if accept else QDialog.Rejected
        def result(self):
            return (name, ttype, tid)
    monkeypatch.setattr(mw, "ComboDialog", Dlg)


# --- duplicate blocking ------------------------------------------------------

@pytest.mark.parametrize("ttype, tid, conflicts", [
    ("act_entry", 4, True),          # Goal.Act04 exists
    ("act_entry", 77, False),
    ("kengeki_move", 1, True),       # Goal.Kengeki01 exists
    ("kengeki_move", 27, False),
    ("special_effect", 5031, True),  # has an interrupt branch
    ("special_effect", 5025, True),  # registered in Goal.Activate
    ("special_effect", NEW_EFFECT, False),
])
def test_combo_conflict_against_file(window, ref_lua, ttype, tid, conflicts):
    _file_only(window, ref_lua)
    reason = window._combo_conflict(ttype, tid)
    assert (reason is not None) is conflicts, reason
    if conflicts:
        assert "file" in reason      # reported as a file conflict, not an open one


def test_combo_conflict_against_open_combos(window):
    window.combos.append(ComboSequence(name="x", trigger_type="act_entry",
                                       trigger_id=77))
    assert window._combo_conflict("act_entry", 77) is not None


def test_new_combo_blocked_when_props_exist(window, ref_lua, dialogs, monkeypatch):
    _loaded(window, ref_lua)
    before = len(window.combos)
    # Act04 already exists -> warn and re-open the dialog; the user then cancels
    calls = {"n": 0}

    class Dlg:
        def __init__(self, *a, **k): pass
        def exec(self):
            calls["n"] += 1
            return QDialog.Accepted if calls["n"] == 1 else QDialog.Rejected
        def result(self):
            return ("c", "act_entry", 4)

    monkeypatch.setattr(mw, "ComboDialog", Dlg)
    window._new_combo()
    assert dialogs.warnings, "the user should have been told why it was blocked"
    assert calls["n"] == 2, "the dialog should re-open so props can be fixed"
    assert len(window.combos) == before      # nothing added


def test_new_combo_creates_and_locks_trigger_fields(window, monkeypatch):
    _stub_combo_dialog(monkeypatch, "fresh", "act_entry", 77)
    window._new_combo()
    assert window.seq.name == "fresh" and window.seq.trigger_id == 77
    # props are set once in the modal, then shown read-only
    assert window.trigger_type.isEnabled() is False
    assert window.trigger_id.isReadOnly() is True
    assert window.name_edit.isEnabled() is True


# --- recent files ------------------------------------------------------------

def test_recents_are_most_recent_first_deduped_and_capped(window):
    for p in ["C:/a.lua", "C:/b.lua", "C:/c.lua"]:
        window._add_recent(p)
    assert [os.path.basename(p) for p in window._load_recents()] == \
        ["c.lua", "b.lua", "a.lua"]
    window._add_recent("C:/a.lua")           # re-open jumps to front, no dupe
    assert [os.path.basename(p) for p in window._load_recents()] == \
        ["a.lua", "c.lua", "b.lua"]
    for i in range(15):
        window._add_recent(f"C:/f{i}.lua")
    assert len(window._load_recents()) == 10


def test_recent_menu_lists_entries_and_shows_empty_state(window):
    window._save_recents([])
    window._rebuild_recent_menu()
    actions = window._recent_menu.actions()
    assert [a.text() for a in actions] == ["No recent files"]
    assert actions[0].isEnabled() is False

    window._add_recent("C:/a.lua")           # rebuilds the menu
    texts = [a.text() for a in window._recent_menu.actions() if not a.isSeparator()]
    assert any("a.lua" in t for t in texts)
    assert "Clear Recently Opened" in texts


def test_open_recent_drops_a_file_that_no_longer_exists(window, dialogs):
    missing = "C:/gone_forever_12345.lua"
    window._save_recents([missing])
    window._open_recent(missing)
    assert dialogs.warnings                  # told the user
    assert window._load_recents() == []      # and pruned it


def test_loading_records_the_file_as_recent(window, ref_lua):
    _loaded(window, ref_lua)
    assert os.path.normcase(window._load_recents()[0]) == \
        os.path.normcase(os.path.abspath(ref_lua))


# --- writing keeps the in-memory file text fresh -----------------------------

def test_write_refreshes_loaded_text_so_find_sees_it(window, ref_lua, dialogs,
                                                     monkeypatch):
    """The reported bug: after Write to file, searching for the special effect
    just added found nothing, because _loaded_text was the load-time snapshot."""
    _loaded(window, ref_lua)
    window.seq = _interrupt()
    window.combos.append(window.seq)
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("TARGET_SELF", True)))
    window._write_to_file()

    assert str(NEW_EFFECT) in window._loaded_text
    assert f"elseif interruptEffectIdentifier == {NEW_EFFECT} then" in window._loaded_text
    # and it really is on disk too
    assert str(NEW_EFFECT) in open(ref_lua, encoding="utf-8").read()


def test_remove_from_file_refreshes_loaded_text(window, ref_lua, dialogs,
                                                monkeypatch):
    _loaded(window, ref_lua)
    window.seq = _interrupt()
    window.combos.append(window.seq)
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("TARGET_SELF", True)))
    window._write_to_file()
    assert str(NEW_EFFECT) in window._loaded_text

    window._remove_from_file()
    assert str(NEW_EFFECT) not in window._loaded_text
    assert str(NEW_EFFECT) not in open(ref_lua, encoding="utf-8").read()


def test_remove_from_file_also_drops_the_combo_from_the_dropdown(
        window, ref_lua, dialogs, monkeypatch):
    _loaded(window, ref_lua)
    combo = _interrupt()
    window._tag(combo)
    window.combos.append(combo)
    window.seq = combo
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("TARGET_SELF", True)))
    window._write_to_file()
    window._remove_from_file()
    assert combo not in window.combos


# --- finding special effects -------------------------------------------------

def test_find_speffect_finds_an_interrupt_combo_in_memory(window):
    combo = _interrupt(5031)
    window.combos.append(combo)
    matches = window._find_speffect(5031)
    assert any(c is combo and node is None for c, node in matches)


def test_scan_file_finds_a_registration_only_effect(window, ref_lua):
    """5025 is registered in Goal.Activate but is not keyed by any combo, so the
    in-memory search alone would miss it."""
    _loaded(window, ref_lua)
    assert window._find_speffect(5025) == []
    hits = window._scan_file_speffect(5025)
    assert hits and any("AddObserveSpecialEffectAttribute" in line for _n, line in hits)


def test_scan_file_ignores_unrelated_numbers(window, ref_lua):
    _loaded(window, ref_lua)
    assert window._scan_file_speffect(NEW_EFFECT) == []


# --- close / delete ----------------------------------------------------------

def test_close_file_returns_to_the_default_state(window, ref_lua, dialogs):
    _loaded(window, ref_lua)
    dialogs.answer = mw.QMessageBox.Discard      # "Don't save"
    window._close_file()
    assert window.loaded_path is None
    assert window._loaded_text == ""
    assert len(window.combos) == 1 and window.seq.name == "my_combo"
    assert not window.seq.steps
    assert window._originals == {} and window._history == {}


def test_close_file_can_be_cancelled(window, ref_lua, dialogs):
    _loaded(window, ref_lua)
    dialogs.answer = mw.QMessageBox.Cancel
    window._close_file()
    assert window.loaded_path == ref_lua         # nothing was discarded


def test_delete_combo_removes_it_from_the_list(window, dialogs, monkeypatch):
    _stub_combo_dialog(monkeypatch, "doomed", "act_entry", 77)
    window._new_combo()
    doomed = window.seq
    window._delete_combo()
    assert doomed not in window.combos


def test_deleting_the_last_combo_restores_the_default(window, dialogs):
    while len(window.combos) > 1:
        window._delete_combo()
    window._delete_combo()
    assert len(window.combos) == 1
    assert window.seq.name == "my_combo" and not window.seq.steps
