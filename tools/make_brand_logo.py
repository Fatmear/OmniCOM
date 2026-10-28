"""Render the exact title-bar BrandMark to logo.png and rebuild every icon asset.

The title bar draws the brand with QPainter (gradient tile + white arc + baseline).
Saving that same painting means the window icon, the .ico and the tile all come
from one source of truth instead of a hand-made bitmap.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

SIZE = 1024


def draw_brand(size: int) -> QImage:
    """Same geometry as ``BrandMark.paintEvent``, scaled to ``size``."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    rect = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#10A794"))
    gradient.setColorAt(0.55, QColor("#4884F0"))
    gradient.setColorAt(1.0, QColor("#7455F5"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(rect, size * 0.28, size * 0.28)

    pen = QPen(QColor(255, 255, 255, 235), max(1.6, size * 0.075))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    inset = size * 0.27
    painter.drawArc(QRectF(inset, inset, size - inset * 2, size - inset * 2),
                    30 * 16, 240 * 16)
    baseline = size * 0.7
    painter.drawLine(QPointF(size * 0.3, baseline), QPointF(size * 0.7, baseline))
    painter.end()
    return image


def main() -> int:
    app = QApplication.instance() or QApplication([])
    image = draw_brand(SIZE)
    target = os.path.join(ROOT, "logo.png")
    if not image.save(target, "PNG"):
        print("failed to save", target)
        return 1
    print(f"wrote {target} ({SIZE}x{SIZE})")

    alpha = image.convertToFormat(QImage.Format_ARGB32)
    corner = alpha.pixelColor(0, 0)
    centre = alpha.pixelColor(SIZE // 2, SIZE // 2)
    print(f"corner alpha={corner.alpha()} centre alpha={centre.alpha()}")
    print("brand tile:", image.size())
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
