"""Small shared widgets: cards, elided labels, toasts, thread marshaling."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QWidget


def card(parent: QWidget | None = None) -> QFrame:
    frame = QFrame(parent)
    frame.setProperty("card", "true")
    return frame


def subtext(text: str = "", parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", "subtext")
    return label


class ElidedLabel(QLabel):
    """Single-line label that elides in the middle instead of growing."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(60)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt casing)
        self._full = text
        self._apply()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(
            metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width())
        )


class Toast(QLabel):
    """Transient status pill, bottom-centered over the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, message: str, *, ok: bool = True) -> None:
        self.setText(message)
        self.setStyleSheet(
            f"background: {'#15803d' if ok else '#dc2626'};"
        )
        self.adjustSize()
        parent = self.parentWidget()
        self.move(
            (parent.width() - self.width()) // 2,
            parent.height() - self.height() - 24,
        )
        self.setGraphicsEffect(None)  # reset any fade left from a prior toast
        self.raise_()
        self.show()
        self._timer.start(3000)


def reveal_in_file_manager(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
