"""Make the top-level modules importable from tests/ without packaging.

Also forces Qt's offscreen platform before anything imports Qt, so the UI tests
run headless (no windows pop up, works in CI).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
