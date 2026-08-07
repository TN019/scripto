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


def test_paths_are_added_and_removed_one_by_one(tmp_path, qapp):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    window = make_window(tmp_path, qapp)
    page = window.run_page
    assert page.hint_label.isVisibleTo(page.paths_card)  # empty state shows the hint

    page._append_paths([str(media)])
    page._append_paths([str(media)])  # duplicates are ignored
    assert page.input_paths == [str(media)]
    assert not page.hint_label.isVisibleTo(page.paths_card)
    assert page.clear_btn.isVisibleTo(page.paths_card)

    page._remove_path(str(media))
    assert page.input_paths == []
    assert page.hint_label.isVisibleTo(page.paths_card)


def _seed_history(tmp_path, vm, stem: str) -> str:
    src = tmp_path / f"{stem}.mp4"
    src.write_bytes(b"x")
    srt = tmp_path / f"{stem}.en.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,200\nline one\n", encoding="utf-8"
    )
    from scripto.core.history import HistoryEntry

    vm.history.append(HistoryEntry(
        source=str(src), outputs=[{"lang": "en", "format": "srt", "path": str(srt)}],
        model="tiny", engine="mlx", status="done",
    ))
    return str(src)


def test_history_delete_single_and_batch(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    a = _seed_history(tmp_path, window.vm, "a")
    b = _seed_history(tmp_path, window.vm, "b")
    c = _seed_history(tmp_path, window.vm, "c")
    page = window.history_page
    page.refresh()

    page._delete_sources({a})  # single (the per-card ✕ path)
    assert {g.source for g in window.vm.history_groups()} == {b, c}

    page._toggle_selected(b, True)
    page._toggle_selected(c, True)
    assert page.delete_selected_btn.isVisibleTo(page)
    page._confirm_delete = lambda _n: True  # the batch path asks first
    page._delete_selected()
    assert window.vm.history_groups() == []


def test_history_viewer_edits_the_file_in_place(tmp_path, qapp):
    from scripto.gui_qt.history_page import _ViewerDialog

    window = make_window(tmp_path, qapp)
    _seed_history(tmp_path, window.vm, "talk")
    group = window.vm.history_groups()[0]
    dialog = _ViewerDialog(window.history_page, group)

    dialog._start_edit()
    assert dialog.editor.isVisibleTo(dialog)
    assert "line one" in dialog.editor.toPlainText()
    dialog.editor.setPlainText(
        "1\n00:00:01,000 --> 00:00:03,200\ncorrected line\n"
    )
    dialog._save_edit()

    saved = (tmp_path / "talk.en.srt").read_text(encoding="utf-8")
    assert "corrected line" in saved
    assert "corrected line" in dialog.body.toPlainText()  # re-rendered
    assert not dialog.editor.isVisibleTo(dialog)


def test_history_page_shows_translation_queue_status(tmp_path, qapp):
    from scripto.gui.viewmodel import TranslationJob

    window = make_window(tmp_path, qapp)
    src = _seed_history(tmp_path, window.vm, "talk")
    page = window.history_page
    page.refresh()

    job = TranslationJob(source=src, name="talk.mp4", srt_path="x",
                         target="zh", status="running", done=21, total=40)
    window.vm.translation_jobs.append(job)
    page.tick_translations()
    assert page.tq_strip.isVisibleTo(page)      # survives any dialog
    assert "52%" in page.tq_label.text()
    assert page._badges[src].isVisibleTo(page)  # per-card badge

    job.status = "done"
    page.tick_translations()
    assert page._seen_terminal == 1             # toast fired exactly once
    assert not page.tq_strip.isVisibleTo(page)


def test_failure_text_is_localized_when_core_supplied_a_key(tmp_path, qapp):
    """Core raises English + an i18n key; the row must show the user's language."""
    from scripto.gui.viewmodel import FileRow

    window = make_window(tmp_path, qapp)
    window.vm.update_settings(language="zh")
    page = window.run_page

    row = FileRow(
        id=1,
        path=tmp_path / "clip.mp4",
        status="failed",
        error="clip.mp4 is still downloading from iCloud after 600s.",
        error_key="errors.icloud_timeout",
        error_params=(("name", "clip.mp4"), ("seconds", "600")),
    )
    text = page.error_text(row)
    assert "iCloud" in text and "600" in text
    assert "still downloading" not in text      # not the English fallback

    # No key (a raw ffmpeg message): the English text shows through unchanged.
    plain = FileRow(id=2, path=tmp_path / "clip.mp4", status="failed",
                    error="extract: ffmpeg exploded")
    assert page.error_text(plain) == "extract: ffmpeg exploded"

    # A params/template mismatch must not blank the row.
    broken = FileRow(id=3, path=tmp_path / "clip.mp4", status="failed",
                     error="fallback text", error_key="errors.icloud_timeout")
    assert page.error_text(broken) == "fallback text"


def test_history_select_all_toggles_every_card(tmp_path, qapp):
    from PySide6.QtCore import Qt

    window = make_window(tmp_path, qapp)
    sources = [_seed_history(tmp_path, window.vm, s) for s in ("a", "b", "c")]
    page = window.history_page
    page.refresh()

    assert page.select_all.isVisibleTo(page)
    assert page.select_all.checkState() == Qt.CheckState.Unchecked
    assert not page.delete_selected_btn.isVisibleTo(page)

    page._toggle_all()                      # what a click on it does
    assert page._selected == set(sources)
    assert all(box.isChecked() for box in page._checkboxes.values())
    assert page.select_all.checkState() == Qt.CheckState.Checked
    assert "3" in page.delete_selected_btn.text()

    page._toggle_all()                      # clicking again clears
    assert page._selected == set()
    assert page.select_all.checkState() == Qt.CheckState.Unchecked
    assert not page.delete_selected_btn.isVisibleTo(page)

    # One card on its own leaves the master box in between.
    page._checkboxes[sources[0]].setChecked(True)
    assert page.select_all.checkState() == Qt.CheckState.PartiallyChecked
    # ...and select-all from a partial state selects the rest, not none.
    page._toggle_all()
    assert page._selected == set(sources)


def test_history_select_all_hides_when_there_is_nothing_to_select(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    page = window.history_page
    page.refresh()
    assert not page.select_all.isVisibleTo(page)


def test_bulk_history_delete_asks_first(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    sources = [_seed_history(tmp_path, window.vm, s) for s in ("a", "b", "c")]
    page = window.history_page
    page.refresh()

    asked = []
    page._confirm_delete = lambda n: asked.append(n) or False   # user cancels
    page._toggle_all()
    page._delete_selected()
    assert asked == [3]
    assert len(window.vm.history_groups()) == 3   # nothing was deleted

    page._confirm_delete = lambda n: asked.append(n) or True    # user confirms
    page._delete_selected()
    assert asked == [3, 3]
    assert window.vm.history_groups() == []

    # A single row keeps the old one-click behaviour — no dialog.
    _seed_history(tmp_path, window.vm, "d")
    page.refresh()
    page._confirm_delete = lambda n: asked.append(n) or False
    page._delete_sources({sources[0]})
    page._checkboxes and page._checkboxes[list(page._checkboxes)[0]].setChecked(True)
    page._delete_selected()
    assert asked == [3, 3]                       # never asked again
    assert window.vm.history_groups() == []
