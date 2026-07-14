"""Entry point for the HKS Lua Editor desktop app.

Run:  python app.py

App icon: drop an image at assets/icon.png (or .ico) and it is picked up
automatically — see _load_icon below.
"""

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow

# On Windows, give the app its own taskbar identity so the window icon (not
# python.exe's) shows in the taskbar.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "hks.lua.editor")
    except Exception:
        pass

_ICON_CANDIDATES = ["assets/icon.png", "assets/icon.ico", "icon.png", "icon.ico"]


def _resource_base() -> str:
    """Directory to resolve bundled resources from. In a PyInstaller onefile
    build the app is unpacked to sys._MEIPASS; otherwise it's this file's dir."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _load_icon() -> QIcon:
    base = _resource_base()
    for rel in _ICON_CANDIDATES:
        path = os.path.join(base, rel)
        if os.path.exists(path):
            return QIcon(path)
    return QIcon()  # empty -> default icon


def main():
    app = QApplication(sys.argv)
    icon = _load_icon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
