"""The Open Recent list (persisted with QSettings).

Mixed into MainWindow; owns nothing but the `_recent_menu` it rebuilds.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox


class RecentFilesMixin:
    # --- recent files (Open Recent, persisted via QSettings) --------------

    def _settings(self) -> QSettings:
        return QSettings("HKSLuaEditor", "HKSLuaEditor")

    def _load_recents(self) -> list:
        # QSettings may hand back a bare str for a 1-element list — normalize.
        val = self._settings().value("recentFiles", [])
        if isinstance(val, str):
            return [val]
        return list(val or [])

    def _save_recents(self, paths: list):
        self._settings().setValue("recentFiles", list(paths))

    def _add_recent(self, path: str):
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        recents = [p for p in self._load_recents() if os.path.normcase(p) != key]
        recents.insert(0, path)
        self._save_recents(recents[:10])
        self._rebuild_recent_menu()

    def _open_recent(self, path: str):
        if not os.path.exists(path):
            QMessageBox.warning(self, "File not found",
                                f"This file no longer exists:\n{path}")
            key = os.path.normcase(path)
            self._save_recents([p for p in self._load_recents()
                                if os.path.normcase(p) != key])
            self._rebuild_recent_menu()
            return
        self._load_path(path)

    def _clear_recents(self):
        self._save_recents([])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        menu = self._recent_menu
        menu.clear()
        recents = self._load_recents()
        if not recents:
            empty = menu.addAction("No recent files")
            empty.setEnabled(False)
            return
        for path in recents:
            # name — dir  (shown as one label; no global shortcut)
            label = f"{os.path.basename(path)}   —   {os.path.dirname(path)}"
            a = menu.addAction(label)
            a.triggered.connect(lambda _checked=False, p=path: self._open_recent(p))
        menu.addSeparator()
        menu.addAction("Clear Recently Opened", self._clear_recents)
