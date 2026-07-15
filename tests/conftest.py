"""Shared fixtures for the UI tests (headless, isolated from the real machine).

Two things must be isolated or the tests would touch real state:
  * QSettings — MainWindow reads the recent-files list while building its menu,
    which would hit the real registry. `window` points it at a temp .ini first.
  * the reference .lua — tests that write/remove use a temp copy, never the
    repo's own 710300_battle.lua.
"""

import os
import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

import ui.main_window as main_window
import ui.mixins.recent_files as recent_files

REF = os.path.join(os.path.dirname(os.path.dirname(__file__)), "710300_battle.lua")


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
