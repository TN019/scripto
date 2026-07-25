"""`scripto` entry point — the desktop GUI (PySide6).

The GUI is a plain in-process Qt app: the Dock/taskbar entry *is* this
process, so the macOS launcher bundle provides the icon and name with no
runtime branding. Window icon falls back to the repo asset when present.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .core.logs import setup_logging


def _window_icon():
    from PySide6.QtGui import QIcon

    from .core.update import repo_root

    root = repo_root()
    icon_path = (root / "assets/icon.png") if root else None
    if icon_path is not None and icon_path.is_file():
        return QIcon(str(icon_path))
    return None


def main() -> int:
    setup_logging()

    from .gui_qt.main_window import MainWindow, ScriptoApp

    app = ScriptoApp(sys.argv)
    icon = _window_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    window = MainWindow()
    app.main_window = window
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
