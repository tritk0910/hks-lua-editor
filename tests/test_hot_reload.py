"""Hot reload: sync a document when its .lua changes on disk (opt-in).

Driven through `_file_changed_settled` / `_on_dir_changed` directly so the tests
don't depend on the OS file-watcher's timing, only on the reload logic.
"""

import shutil

import pytest
from PySide6.QtWidgets import QMessageBox


@pytest.fixture
def watched(window, tmp_path):
    """window watching one loaded file; returns (window, path, base_text)."""
    from conftest import REF, _require_ref
    _require_ref()
    path = tmp_path / "enemy.lua"
    shutil.copy2(REF, path)
    base = path.read_text(encoding="utf-8", errors="ignore")
    window._set_watching(True)
    assert window._load_path(str(path))
    return window, str(path), base


def _names(window):
    return {getattr(c, "name", None) for c in window.combos}


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --- the watch list ---------------------------------------------------------

def test_watching_covers_the_file_and_its_directory(watched):
    window, path, _base = watched
    assert path in window._watcher.files()
    assert any(path.startswith(d) for d in window._watcher.directories())


def test_toggling_off_stops_watching(watched):
    window, _path, _base = watched
    window._set_watching(False)
    assert window._watcher.files() == []
    assert window._watcher.directories() == []


def test_nothing_is_watched_until_enabled(window, tmp_path):
    from conftest import REF, _require_ref
    _require_ref()
    p = tmp_path / "x.lua"
    shutil.copy2(REF, p)
    window._load_path(str(p))                 # watching still off by default
    assert window._watcher.files() == []


# --- clean document: silent reload ------------------------------------------

def test_a_clean_document_reloads_silently(watched):
    window, path, base = watched
    _write(path, base.replace("Goal.Act05", "Goal.Act88"))
    window._file_changed_settled(path)
    assert "Act88" in _names(window)
    assert "Act05" not in _names(window)


def test_reload_keeps_the_selected_combo(watched):
    window, path, base = watched
    window.seq = next(c for c in window.combos if getattr(c, "name", None) == "Act04")
    _write(path, base.replace("Goal.Act05", "Goal.Act88"))   # Act04 untouched
    window._file_changed_settled(path)
    assert (window.doc.current.trigger_type, window.doc.current.trigger_id) == ("act_entry", 4)


def test_our_own_write_does_not_trigger_a_reload(watched):
    window, path, base = watched
    # after a write the doc.text matches disk, so the watcher event is a no-op
    window.doc.text = base
    _write(path, base)
    before = _names(window)
    window._file_changed_settled(path)
    assert _names(window) == before


def test_transient_garbage_on_disk_keeps_the_tab(watched):
    window, path, _base = watched
    before = _names(window)
    _write(path, "half a fi")                 # mid-save junk, parses to nothing
    window._file_changed_settled(path)
    assert _names(window) == before           # tab untouched


# --- dirty document: prompt -------------------------------------------------

def test_dirty_document_keeps_edits_when_you_choose_keep(watched, monkeypatch):
    window, path, base = watched
    combo = next(c for c in window.combos if getattr(c, "name", None) == "Act04")
    combo.name = "MY EDIT"                     # unsaved edit -> dirty
    assert window._document_dirty(window.doc)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Cancel))   # Keep
    _write(path, base.replace("Goal.Act06", "Goal.Act99"))
    window._file_changed_settled(path)
    assert "MY EDIT" in _names(window)
    assert "Act99" not in _names(window)


def test_dirty_document_reloads_when_you_choose_reload(watched, monkeypatch):
    window, path, base = watched
    next(c for c in window.combos if getattr(c, "name", None) == "Act04").name = "MY EDIT"
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Reset))    # Reload
    _write(path, base.replace("Goal.Act06", "Goal.Act99"))
    window._file_changed_settled(path)
    assert "Act99" in _names(window)
    assert "MY EDIT" not in _names(window)


# --- delete / rename --------------------------------------------------------

def test_deleting_the_file_marks_the_tab_but_keeps_the_work(watched):
    import os
    window, path, _base = watched
    before = _names(window)
    os.remove(path)
    window._on_dir_changed(str(__import__("pathlib").Path(path).parent))
    assert window.doc.missing is True
    assert _names(window) == before                    # combos kept in memory
    assert window.file_tabs.tabText(0).startswith("⚠")


def test_recreating_the_file_clears_the_marker_and_resyncs(watched):
    import os
    window, path, base = watched
    d = str(__import__("pathlib").Path(path).parent)
    os.remove(path)
    window._on_dir_changed(d)
    assert window.doc.missing
    _write(path, base.replace("Goal.Act05", "Goal.Act88"))
    window._on_dir_changed(d)
    assert window.doc.missing is False
    assert not window.file_tabs.tabText(0).startswith("⚠")
    assert "Act88" in _names(window)
