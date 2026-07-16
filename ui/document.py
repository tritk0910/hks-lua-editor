"""One open .lua file (or an unsaved scratch), as shown by one file tab.

Everything here is per-file. Keeping it together is what stops combos from one
file being written into another: before this, loading a second file merged its
combos into the same list while the write target moved to the new path, so
writing a combo from the first file spliced it into the second.

Undo history and the load-time snapshots deliberately stay on the window: they
are keyed by a combo's `_uid`, which is handed out by a single counter and so is
already unique across documents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Document:
    path: str | None = None            # None -> never saved ("untitled")
    text: str = ""                     # the file as loaded (Find in file)
    combos: list = field(default_factory=list)    # this file's combos/activators
    warnings: list = field(default_factory=list)  # parser.ParseWarning
    current: object = None             # the combo selected in this tab
    missing: bool = False              # path vanished on disk (kept in memory)

    @property
    def title(self) -> str:
        return os.path.basename(self.path) if self.path else "untitled"

    def is_pristine(self) -> bool:
        """An untouched scratch tab — nothing loaded and nothing built yet, so
        opening a file can reuse it instead of leaving an empty tab behind."""
        return self.path is None and not any(
            getattr(c, "steps", None) or getattr(c, "items", None)
            or getattr(c, "blocks", None) for c in self.combos)
