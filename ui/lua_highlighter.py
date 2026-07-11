"""A small QSyntaxHighlighter for the generated Lua (HKS) preview.

Highlights Lua keywords, numbers, comments, and the ALL-CAPS game constants
(GOAL_*, TARGET_*, AI_*, ...). Colors are chosen to read on a dark theme.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QFont

_KEYWORDS = [
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while",
]


def _fmt(color: str, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


class LuaHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        kw = _fmt("#569CD6", bold=True)      # keywords - blue
        self._rules = [
            (QRegularExpression(r"\b(" + "|".join(_KEYWORDS) + r")\b"), kw),
            (QRegularExpression(r"\b[A-Z][A-Z0-9_]{2,}\b"), _fmt("#4EC9B0")),  # CONSTANTS - teal
            (QRegularExpression(r"\b\d+(\.\d+)?\b"), _fmt("#B5CEA8")),          # numbers - green
            (QRegularExpression(r"\bGoal\.\w+"), _fmt("#DCDCAA")),              # Goal.X - yellow
            (QRegularExpression(r":\w+"), _fmt("#DCDCAA")),                     # :method - yellow
        ]
        self._comment = _fmt("#6A9955")      # comments - green

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        # line comments win over everything from `--` to end of line
        idx = text.find("--")
        if idx != -1:
            self.setFormat(idx, len(text) - idx, self._comment)
