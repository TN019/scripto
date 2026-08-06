"""Painted transport icons for the player.

Emoji glyphs (⏪ ⏩ ⏸) render differently on every platform and never match
the app's own weight or color, so the transport controls draw their own:
a circular arrow with the skip amount inside, plus play/pause. Everything
is a vector path painted into a device-pixel-ratio-correct pixmap, so the
icons follow the theme's text color and stay crisp on retina displays.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Gap left at the top of the circle for the arrowhead, in degrees.
_ARC_START = 120
_ARC_SPAN = 300


def _canvas(size: int, ratio: float) -> QPixmap:
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def _painter(pixmap: QPixmap) -> QPainter:
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    return painter


def skip_icon(seconds: int, *, forward: bool, color: str,
              size: int = 24, ratio: float = 2.0) -> QIcon:
    """A circular arrow with ``seconds`` inside — ⟳10 / ⟲10."""
    pixmap = _canvas(size, ratio)
    painter = _painter(pixmap)
    tint = QColor(color)

    painter.save()
    if not forward:  # the back arrow is the forward one, mirrored
        painter.translate(size, 0)
        painter.scale(-1, 1)

    margin = size * 0.14
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    pen = QPen(tint)
    pen.setWidthF(size * 0.085)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(rect, _ARC_START * 16, _ARC_SPAN * 16)

    # Arrowhead at the arc's open end, aimed along the clockwise tangent.
    theta = math.radians(_ARC_START)
    radius = rect.width() / 2
    end_x = rect.center().x() + radius * math.cos(theta)
    end_y = rect.center().y() - radius * math.sin(theta)
    dir_x, dir_y = math.sin(theta), math.cos(theta)  # clockwise tangent
    perp_x, perp_y = -dir_y, dir_x
    # The head continues the stroke rather than straddling its end, so the
    # arrow reads as one line that grew a point.
    length, half = size * 0.21, size * 0.105
    base_x, base_y = end_x - dir_x * length * 0.25, end_y - dir_y * length * 0.25
    head = QPainterPath()
    head.moveTo(base_x + dir_x * length, base_y + dir_y * length)
    head.lineTo(base_x + perp_x * half, base_y + perp_y * half)
    head.lineTo(base_x - perp_x * half, base_y - perp_y * half)
    head.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(tint)
    painter.drawPath(head)
    painter.restore()

    font = QFont()
    font.setPixelSize(max(7, round(size * 0.40)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(tint)
    painter.drawText(
        rect.adjusted(0, size * 0.045, 0, size * 0.045),
        Qt.AlignmentFlag.AlignCenter,
        str(seconds),
    )
    painter.end()
    return QIcon(pixmap)


def play_icon(color: str, size: int = 24, ratio: float = 2.0) -> QIcon:
    pixmap = _canvas(size, ratio)
    painter = _painter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    triangle = QPainterPath()
    triangle.moveTo(QPointF(size * 0.30, size * 0.20))
    triangle.lineTo(QPointF(size * 0.80, size * 0.50))
    triangle.lineTo(QPointF(size * 0.30, size * 0.80))
    triangle.closeSubpath()
    painter.drawPath(triangle)
    painter.end()
    return QIcon(pixmap)


def pause_icon(color: str, size: int = 24, ratio: float = 2.0) -> QIcon:
    pixmap = _canvas(size, ratio)
    painter = _painter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    bar, gap = size * 0.15, size * 0.12
    top, height = size * 0.21, size * 0.58
    left = (size - (2 * bar + gap)) / 2
    radius = bar * 0.35
    painter.drawRoundedRect(QRectF(left, top, bar, height), radius, radius)
    painter.drawRoundedRect(
        QRectF(left + bar + gap, top, bar, height), radius, radius
    )
    painter.end()
    return QIcon(pixmap)
