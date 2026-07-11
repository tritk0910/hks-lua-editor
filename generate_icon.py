"""Generate a placeholder app icon at assets/icon.png (256x256).

Run:  python generate_icon.py
Replace assets/icon.png with your own image any time — app.py picks it up.
This uses only Qt (already a dependency), no Pillow needed.
"""

import os
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication


def make_icon(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # dark rounded background
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0.0, QColor("#2b2b2b"))
    bg.setColorAt(1.0, QColor("#141414"))
    p.setBrush(QBrush(bg))
    p.setPen(Qt.NoPen)
    r = size * 0.16
    p.drawRoundedRect(0, 0, size, size, r, r)

    # Sekiro red brush dot
    p.setBrush(QColor("#c0392b"))
    p.drawEllipse(QPointF(size * 0.34, size * 0.36), size * 0.16, size * 0.16)

    # katana blade (light diagonal) with a small guard
    blade = QPen(QColor("#e8e8e8"), size * 0.045, Qt.SolidLine, Qt.RoundCap)
    p.setPen(blade)
    p.drawLine(QPointF(size * 0.22, size * 0.80), QPointF(size * 0.82, size * 0.20))
    guard = QPen(QColor("#8a6d3b"), size * 0.05, Qt.SolidLine, Qt.RoundCap)
    p.setPen(guard)
    p.drawLine(QPointF(size * 0.30, size * 0.62), QPointF(size * 0.42, size * 0.74))

    p.end()
    return pm


def main():
    app = QApplication(sys.argv)  # noqa: F841 - needed for QPixmap painting
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(here, "assets")
    os.makedirs(assets, exist_ok=True)
    out = os.path.join(assets, "icon.png")
    make_icon(256).save(out, "PNG")
    print("wrote", out)


if __name__ == "__main__":
    main()
