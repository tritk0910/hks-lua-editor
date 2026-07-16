"""Several .lua files open at once, one tab each.

The point isn't only convenience. Loading a second file used to merge its combos
into the same list while the write target moved to the new path — so a combo
from the first file would be spliced into the second. Each file now owns its
combos.
"""

import shutil

import pytest

import writer
from models import ComboStep


@pytest.fixture
def two_files(tmp_path):
    """Two distinct .lua files: B has Act77 where A has Act04."""
    from conftest import REF, _require_ref
    _require_ref()
    a = tmp_path / "enemyA.lua"
    b = tmp_path / "enemyB.lua"
    shutil.copy2(REF, a)
    b.write_text(a.read_text(encoding="utf-8", errors="ignore").replace("Goal.Act04",
                                                                        "Goal.Act77"),
                 encoding="utf-8", newline="\n")
    return str(a), str(b)


def _titles(window):
    return [window.file_tabs.tabText(i) for i in range(window.file_tabs.count())]


def _combo(window, name):
    return next(c for c in window.combos if getattr(c, "name", None) == name)


# --- one tab per file -------------------------------------------------------

def test_each_file_gets_its_own_tab(window, two_files):
    a, b = two_files
    assert window._load_path(a)
    assert window._load_path(b)
    assert len(window.docs) == 2
    assert _titles(window) == ["enemyA.lua", "enemyB.lua"]
    assert window.file_tabs.currentIndex() == 1        # the new file is shown


def test_a_files_combos_stay_its_own(window, two_files):
    """The bug this replaces: 109 combos + 109 combos in one dropdown."""
    a, b = two_files
    window._load_path(a)
    count_a = len(window.combos)
    window._load_path(b)
    assert len(window.combos) == count_a              # B's tab holds only B's
    assert _combo(window, "Act77")                    # B has it
    with pytest.raises(StopIteration):
        _combo(window, "Act04")                       # and A's is not here


def test_switching_tabs_switches_the_whole_file(window, two_files):
    a, b = two_files
    window._load_path(a)
    window._load_path(b)
    window.file_tabs.setCurrentIndex(0)
    assert window.loaded_path == a
    assert _combo(window, "Act04")                    # A's combos are back
    assert window.combo_selector.count() == len(window.combos)
    window.file_tabs.setCurrentIndex(1)
    assert window.loaded_path == b


def test_a_combo_is_written_to_its_own_file(window, two_files):
    """Before, picking a combo loaded from A and writing sent it into B."""
    a, b = two_files
    window._load_path(a)
    window._load_path(b)
    window.file_tabs.setCurrentIndex(0)               # back to A
    combo = _combo(window, "Act04")
    window.seq = combo
    assert window.loaded_path == a                    # the write target follows the tab
    text_a = open(a, encoding="utf-8").read()
    combo.steps[0].anim_id = 4321
    out, _ = writer.apply_sequence(text_a, combo)
    assert "4321" in out


def test_opening_the_same_file_twice_just_switches_to_it(window, two_files):
    a, b = two_files
    window._load_path(a)
    window._load_path(b)
    assert window._load_path(a)                       # already open
    assert len(window.docs) == 2                      # no duplicate tab
    assert window.loaded_path == a
    assert window.file_tabs.currentIndex() == 0


def test_the_first_file_reuses_the_empty_scratch_tab(window, two_files):
    a, _b = two_files
    assert window.docs[0].is_pristine()
    window._load_path(a)
    assert len(window.docs) == 1                      # no leftover "untitled"
    assert _titles(window) == ["enemyA.lua"]


def test_a_scratch_tab_with_work_in_it_is_kept(window, two_files):
    a, _b = two_files
    window.seq.steps.append(ComboStep("ComboFinal", 3000, 10))   # started building
    window._load_path(a)
    assert len(window.docs) == 2
    assert _titles(window) == ["untitled", "enemyA.lua"]


# --- closing ----------------------------------------------------------------

def test_closing_a_tab_leaves_the_others_alone(window, two_files, dialogs):
    a, b = two_files
    window._load_path(a)
    window._load_path(b)
    window._close_document(1)                         # close B
    assert len(window.docs) == 1
    assert window.loaded_path == a
    assert _combo(window, "Act04")


def test_closing_the_last_tab_leaves_an_untitled_one(window, two_files, dialogs):
    a, _b = two_files
    window._load_path(a)
    window._close_file()
    assert len(window.docs) == 1
    assert window.loaded_path is None
    assert window.seq.name == "my_combo"
    assert _titles(window) == ["untitled"]


def test_closing_a_tab_can_be_cancelled(window, two_files, dialogs):
    from PySide6.QtWidgets import QMessageBox
    a, _b = two_files
    window._load_path(a)
    dialogs.answer = QMessageBox.Cancel
    window._close_file()
    assert len(window.docs) == 1
    assert window.loaded_path == a                    # still open


def test_closing_a_tab_forgets_its_undo_history(window, two_files, dialogs):
    a, _b = two_files
    window._load_path(a)
    uid = window.seq._uid
    window._push_undo()
    assert uid in window._history
    window._close_file()
    assert uid not in window._history
    assert uid not in window._originals


# --- per-file state that used to be global ---------------------------------

def test_warnings_and_text_follow_the_tab(window, two_files):
    a, b = two_files
    window._load_path(a)
    window._load_path(b)
    assert window._warnings and window._loaded_text
    window.file_tabs.setCurrentIndex(0)
    assert "Goal.Act04" in window._loaded_text        # A's text, not B's
    window.file_tabs.setCurrentIndex(1)
    assert "Goal.Act77" in window._loaded_text
    assert "Goal.Act04" not in window._loaded_text


def test_undo_keeps_working_across_tabs(window, two_files, dialogs):
    a, b = two_files
    window._load_path(a)
    combo_a = _combo(window, "Act04")
    window.seq = combo_a
    window._push_undo()
    combo_a.steps.append(ComboStep("ComboFinal", 999, 10))
    window._load_path(b)                              # go away...
    window.file_tabs.setCurrentIndex(0)               # ...and come back
    window.seq = combo_a
    window._undo()
    assert not any(s.anim_id == 999 for s in window.seq.steps)
