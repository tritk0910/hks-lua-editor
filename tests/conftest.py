"""Shared fixtures: the reference behavior file, and a headless MainWindow.

Isolation matters here, or the tests would touch real state:
  * QSettings — MainWindow reads the recent-files list while building its menu,
    which would hit the real registry. `window` points it at a temp .ini first.
  * the reference .lua — tests that write/remove use a temp copy, never the
    repo's own 710300_battle.lua.

The reference file is a copyrighted game file and is deliberately NOT committed
(see .gitignore), so tests that need it skip with an explanation when it is
absent rather than erroring out on a fresh clone.
"""

import os
import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

import ui.main_window as main_window
import ui.mixins.recent_files as recent_files
from parser import parse_file

REF = os.path.join(os.path.dirname(os.path.dirname(__file__)), "710300_battle.lua")

_NO_REF = (
    "710300_battle.lua is not present. It is a copyrighted FromSoftware file, so "
    "it is not committed to this repo — drop your own copy in the project root to "
    "run the tests that parse a real behavior file."
)


def _require_ref():
    if not os.path.exists(REF):
        pytest.skip(_NO_REF)


@pytest.fixture(scope="session")
def text():
    """Contents of the reference behavior file (read-only; shared)."""
    _require_ref()
    with open(REF, encoding="utf-8", errors="ignore") as f:
        return f.read()


@pytest.fixture(scope="module")
def parsed(text):
    """The parsed reference file — module-scoped so one module's edits to the
    tree can't leak into another's."""
    return parse_file(text)


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test process (Qt allows only one)."""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A fresh MainWindow whose QSettings are redirected to a temp .ini.

    Patched on ui.mixins.recent_files (where _settings actually looks QSettings
    up), and BEFORE constructing: __init__ -> _build_menu -> _rebuild_recent_menu
    -> _load_recents would otherwise read the real registry.
    """
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(recent_files, "QSettings",
                        lambda *a, **k: QSettings(ini, QSettings.IniFormat))
    return main_window.MainWindow()


@pytest.fixture
def ref_lua(tmp_path):
    """Path to a throwaway copy of the reference behavior file."""
    _require_ref()
    dst = tmp_path / "combat.lua"
    shutil.copy2(REF, dst)
    return str(dst)


@pytest.fixture
def dialogs(monkeypatch):
    """Stub the blocking dialogs; records what was shown.

    `.questions`/`.warnings`/`.infos` collect the message text of each call.
    Set `.answer` to choose what a prompt returns.

    Note `exec` is stubbed too, not just the static helpers: _confirm_discard
    builds a QMessageBox instance and calls box.exec(), which would block the
    test run forever waiting for a click.
    """
    class Stub:
        def __init__(self):
            self.questions, self.warnings, self.infos = [], [], []
            self.answer = QMessageBox.Yes

    stub = Stub()

    def _q(parent, title, text, *a, **k):
        stub.questions.append(text)
        return stub.answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_q))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda p, t, text="", *a, **k: stub.warnings.append(text)))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda p, t, text="", *a, **k: stub.infos.append(text)))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: stub.answer)
    return stub
