"""Qt view layer smoke tests (offscreen): window builds, pages navigate,
close hides instead of quitting, language switch remounts, drain updates
rows. Runs headless via the offscreen platform plugin — no display needed.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from scripto.core.config import ConfigService
from scripto.core.events import StatusEvent
from scripto.core.history import HistoryStore
from scripto.gui.viewmodel import GuiViewModel
from scripto.gui_qt import theme
from scripto.gui_qt.main_window import MainWindow, ScriptoApp


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = ScriptoApp([])
    return app


def make_window(tmp_path, qapp, **settings) -> MainWindow:
    vm = GuiViewModel(
        config_service=ConfigService(tmp_path / "config.json"),
        history=HistoryStore(tmp_path / "history.json"),
    )
    vm.update_settings(language="en", **settings)  # not first-run: no wizard
    window = MainWindow(vm)
    if isinstance(qapp, ScriptoApp):
        qapp.main_window = window
    return window


def test_window_builds_and_navigates(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    assert window.stack.count() == 3
    assert window.stack.currentIndex() == 0
    window._nav_to(2)
    assert window.stack.currentIndex() == 2
    assert window.nav_buttons[2].isChecked()
    window._nav_to(1)  # history page refresh must not blow up when empty
    assert window.stack.currentIndex() == 1


def test_close_hides_window_and_keeps_app_alive(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    window.show()
    window.close()
    if sys.platform == "darwin":
        assert not window.isVisible()
    assert not window._really_quit  # the process would keep running


def test_language_switch_remounts_in_place(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    english = window.nav_buttons[0].text()
    window.vm.update_settings(language="zh")
    window.remount()
    assert window.nav_buttons[0].text() != english
    assert window.nav_buttons[0].text() == window.t("gui.tab_run")


def test_theme_modes_pick_palettes(tmp_path, qapp):
    window = make_window(tmp_path, qapp, theme="dark")
    window.apply_theme()
    assert window.palette_tokens == theme.DARK
    window.vm.update_settings(theme="light")
    window.apply_theme()
    assert window.palette_tokens == theme.LIGHT


def test_drain_tick_updates_rows_and_log(tmp_path, qapp):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    window = make_window(tmp_path, qapp)
    window.vm.scan_inputs(str(media))
    window.run_page.rebuild_rows()
    assert set(window.run_page.row_widgets) == {1}

    window.vm._id_map = {1: 1}
    window.vm.bus.emit(StatusEvent(subject="job:1", status="transcribing"))
    window._tick()
    label = window.run_page.row_widgets[1].status_label.text()
    assert label == window.t("status.transcribing")
    assert "transcribing" in window.run_page.log_view.toPlainText()


def test_transcribe_language_lives_on_the_run_page(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    combo = window.run_page.tlang_combo
    assert combo.currentData() == "auto"
    combo.setCurrentIndex(combo.findData("ja"))
    assert window.vm.get_config()["transcribe_language"] == "ja"
    # The start-Ollama affordance exists and stays hidden until needed.
    assert not window.run_page.ollama_btn.isVisible()


def test_history_viewer_renders_srt_as_transcript(tmp_path, qapp):
    from scripto.core.history import HistoryEntry
    from scripto.gui_qt.history_page import _ViewerDialog

    src = tmp_path / "talk.mp4"
    src.write_bytes(b"x")
    srt = tmp_path / "talk.en.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,200\nhello there\n\n"
        "2\n00:00:03,600 --> 00:00:06,900\nsecond line\n",
        encoding="utf-8",
    )
    window = make_window(tmp_path, qapp)
    window.vm.history.append(HistoryEntry(
        source=str(src), outputs=[{"lang": "en", "format": "srt", "path": str(srt)}],
        model="tiny", engine="mlx", status="done",
    ))
    dialog = _ViewerDialog(window.history_page, window.vm.history_groups()[0])
    text = dialog.body.toPlainText()
    assert "hello there" in text and "second line" in text
    assert "-->" not in text and ",000" not in text  # raw cue syntax is gone
    assert "00:00:01 → 00:00:03" in text
    # No ghost buttons: exactly one generation exists after two rebuilds.
    from PySide6.QtWidgets import QPushButton

    labels = [b.text() for b in dialog.findChildren(QPushButton)]
    assert labels.count(window.lang_label("en")) == 1
