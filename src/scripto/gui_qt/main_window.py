"""MainWindow: sidebar navigation over three pages, plus app lifecycle.

Lifecycle rules ported from the flet layer, minus the workarounds:
- Closing the window hides it (Dock/taskbar icon stays, jobs keep running);
  clicking the Dock icon or launching again brings it back. ⌘Q / Ctrl+Q
  quits for real. Being a normal in-process Qt window, this is just
  ``closeEvent → hide()`` — no AppKit, no viewer bundle.
- A 4 Hz QTimer drains the viewmodel; pages consume the deltas.
- Switching the UI language rebuilds the window content (remount).
"""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..gui.viewmodel import GuiViewModel
from ..i18n import I18n
from . import theme
from .history_page import HistoryPage
from .run_page import RunPage
from .settings_page import SettingsPage, WizardDialog
from .widgets import Toast

TICK_MS = 250


class MainWindow(QMainWindow):
    _invoke = Signal(object)

    def __init__(self, vm: GuiViewModel | None = None):
        super().__init__()
        self.vm = vm or GuiViewModel()
        self.i18n = I18n(lambda: self.vm.get_config().get("language", ""))
        self._really_quit = False
        self._last_snapshot: tuple = ()
        self._last_log_len = 0
        self.palette_tokens = theme.LIGHT

        # Worker threads hand results back through this queued signal.
        self._invoke.connect(lambda fn: fn())

        self.setWindowTitle("Scripto")
        config = self.vm.get_config()
        self.resize(
            int(config.get("gui_window_width", 1000) or 1000),
            int(config.get("gui_window_height", 760) or 760),
        )
        self.setMinimumSize(760, 560)

        quit_action = QAction(self.t("gui.close"), self)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.quit_app)
        self.addAction(quit_action)

        self.apply_theme()
        self.build_ui()

        self._ticker = QTimer(self)
        self._ticker.setInterval(TICK_MS)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

        self._resize_saver = QTimer(self)
        self._resize_saver.setSingleShot(True)
        self._resize_saver.setInterval(800)
        self._resize_saver.timeout.connect(self._save_window_size)

        # Follow live OS light/dark switches while theme is "system".
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            lambda _scheme: self.apply_theme()
        )

        if self.vm.is_first_run():
            QTimer.singleShot(0, lambda: WizardDialog(self).exec())
        self.run_thread(self._startup_doctor)

    # ------------------------------------------------------------------ #
    # Cross-thread + i18n helpers (pages use these)
    # ------------------------------------------------------------------ #

    def t(self, key: str, **kwargs) -> str:
        return self.i18n.t(key, **kwargs)

    def lang_label(self, code: str) -> str:
        label = self.t(f"tlang_{code}")
        return code if label == f"tlang_{code}" else label

    def run_in_main(self, fn) -> None:
        self._invoke.emit(fn)

    def run_thread(self, fn) -> None:
        threading.Thread(target=fn, name="scripto-gui-worker", daemon=True).start()

    def toast(self, message: str, ok: bool = True) -> None:
        self._toast.show_message(message, ok=ok)

    def start_ollama(self, on_done) -> None:
        """Launch the local Ollama server and wait until it answers.

        ``on_done(ok, message)`` runs on the UI thread. Shared by the run
        page and the model manager, so \"Ollama is not running\" is always
        one click away from fixed instead of a trip to the terminal.
        """
        from ..core import paths
        from ..translate.ollama import start_server

        client = self.vm.ollama_client()

        def job() -> None:
            launched, detail = start_server(paths.log_dir() / "ollama.log")
            if not launched:
                ok, message = False, self.t("gui.ollama_start_failed", reason=detail)
            elif client.wait_reachable():
                ok, message = True, self.t("gui.ollama_started")
            else:
                ok, message = False, self.t("gui.ollama_start_timeout")
            self.run_in_main(lambda: on_done(ok, message))

        self.run_thread(job)

    # ------------------------------------------------------------------ #
    # Mount / remount
    # ------------------------------------------------------------------ #

    def build_ui(self) -> None:
        t = self.t

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(184)
        title = QLabel("Scripto")
        title.setObjectName("AppTitle")

        self.nav_buttons: list[QToolButton] = []
        nav_box = QVBoxLayout(sidebar)
        nav_box.setContentsMargins(12, 14, 12, 14)
        nav_box.setSpacing(4)
        nav_box.addWidget(title)
        nav_box.addSpacing(10)

        self.stack = QStackedWidget()
        self.run_page = RunPage(self)
        self.history_page = HistoryPage(self)
        self.settings_page = SettingsPage(self)
        pages = [
            (t("gui.tab_run"), self.run_page),
            (t("gui.tab_history"), self.history_page),
            (t("gui.tab_settings"), self.settings_page),
        ]
        for index, (label, page) in enumerate(pages):
            self.stack.addWidget(page)
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.setProperty("nav", "true")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setMinimumWidth(158)
            btn.clicked.connect(lambda _=False, i=index: self._nav_to(i))
            nav_box.addWidget(btn)
            self.nav_buttons.append(btn)
        nav_box.addStretch(1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._toast = Toast(central)
        self._nav_to(0)
        self.run_page.rebuild_rows()

    def remount(self) -> None:
        self._last_snapshot = ()
        self._last_log_len = 0
        self.build_ui()

    def _nav_to(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        if index == 1:
            self.history_page.refresh()

    # ------------------------------------------------------------------ #
    # Theme
    # ------------------------------------------------------------------ #

    def apply_theme(self) -> None:
        from PySide6.QtGui import QColor, QPalette

        app = QApplication.instance()
        mode = self.vm.get_config().get("theme", "system")
        hints = app.styleHints()
        system_dark = hints.colorScheme() == Qt.ColorScheme.Dark
        tokens = self.palette_tokens = theme.palette_for(mode, system_dark)

        # QSS covers our widgets; the QPalette covers everything native the
        # QSS does not reach (scroll viewports, popup frames, menus).
        palette = QPalette()
        roles = {
            QPalette.ColorRole.Window: tokens.window,
            QPalette.ColorRole.Base: tokens.surface,
            QPalette.ColorRole.AlternateBase: tokens.sunken,
            QPalette.ColorRole.Text: tokens.text,
            QPalette.ColorRole.WindowText: tokens.text,
            QPalette.ColorRole.ButtonText: tokens.text,
            QPalette.ColorRole.Button: tokens.surface,
            QPalette.ColorRole.Highlight: tokens.accent,
            QPalette.ColorRole.HighlightedText: tokens.accent_text,
            QPalette.ColorRole.PlaceholderText: tokens.subtext,
            QPalette.ColorRole.ToolTipBase: tokens.surface,
            QPalette.ColorRole.ToolTipText: tokens.text,
        }
        for role, color in roles.items():
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(theme.build_qss(tokens))

    # ------------------------------------------------------------------ #
    # Ticker
    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        try:
            result = self.vm.drain()
        except Exception:
            return

        snap = result.snapshot
        key = (snap.running, snap.done, snap.total, snap.current_name,
               snap.current_status, int(snap.eta_sec or 0))
        rows_changed = bool(result.changed_rows)
        snapshot_changed = key != self._last_snapshot
        log_changed = len(result.log_lines) != self._last_log_len

        if rows_changed or snapshot_changed or result.finished:
            self._last_snapshot = key
            self.run_page.apply_drain(result)
        if log_changed:
            self._last_log_len = len(result.log_lines)
            self.run_page.refresh_log(result.log_lines)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _startup_doctor(self) -> None:
        from ..core.doctor import doctor_ok, run_doctor

        try:
            results = run_doctor(self.vm.get_config())
        except Exception:
            return
        if not doctor_ok(results):
            self.run_in_main(
                lambda: self.toast(self.t("gui.doctor_startup_failed"), ok=False)
            )

    def quit_app(self) -> None:
        self._really_quit = True
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Red-X hides the window so a running batch survives a casual close;
        # quit_app (⌘Q / updater restart) closes for real. QuitOnLastWindow-
        # Closed is off, so hiding never terminates the process.
        if self._really_quit:
            event.accept()
            return
        event.ignore()
        if sys.platform == "darwin":
            self.hide()
        else:
            # No Dock to bring it back elsewhere — minimize instead.
            self.showMinimized()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_resize_saver"):
            self._resize_saver.start()

    def _save_window_size(self) -> None:
        try:
            self.vm.update_settings(
                gui_window_width=int(self.width()),
                gui_window_height=int(self.height()),
            )
        except Exception:
            pass

class ScriptoApp(QApplication):
    """QApplication that re-shows the hidden window on Dock-icon clicks."""

    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.setStyle("Fusion")  # one predictable base look on every platform
        self.setQuitOnLastWindowClosed(False)
        self.setApplicationName("Scripto")
        self.setApplicationDisplayName("Scripto")
        self.main_window: MainWindow | None = None

    def event(self, e) -> bool:  # noqa: N802
        # macOS delivers ApplicationActivate when the Dock icon is clicked.
        if (
            e.type() == QEvent.Type.ApplicationActivate
            and self.main_window is not None
            and not self.main_window.isVisible()
        ):
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()
        return super().event(e)
