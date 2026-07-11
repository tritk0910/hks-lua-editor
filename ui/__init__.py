"""PySide6 UI for the Sekiro Combo Builder.

The UI only constructs and reads the data model (`models.py`) and calls the
UI-agnostic core (`generator.py`, `visualizer.py`, `parser.py`). No parsing or
generation logic lives here, so this whole package can be swapped later.
"""
