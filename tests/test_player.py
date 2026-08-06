"""Player cue logic: timestamp parsing, cue lookup, time formatting.

The dialog tests below drive the widget offscreen: subtitle slots really
hide when a track is switched off, and a burst of skip clicks collapses
into a single backend seek instead of one per click.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtMultimedia")

from scripto.gui_qt.player import build_cues, cue_at, format_ms, timestamp_ms

SRT = (
    "1\n00:00:01,000 --> 00:00:03,200\nfirst line\n\n"
    "2\n00:00:03,600 --> 00:01:06,900\nsecond line\n"
)


def test_timestamp_ms_parses_comma_and_dot():
    assert timestamp_ms("00:00:01,000") == 1000
    assert timestamp_ms("00:01:02.345") == 62345
    assert timestamp_ms("01:00:00,001") == 3600001
    assert timestamp_ms("garbage") == 0


def test_cue_lookup_inside_between_and_outside():
    cues = build_cues(SRT)
    assert cue_at(cues, 0) == ""            # before the first cue
    assert cue_at(cues, 1000) == "first line"
    assert cue_at(cues, 3199) == "first line"
    assert cue_at(cues, 3400) == ""         # the gap between cues
    assert cue_at(cues, 60000) == "second line"
    assert cue_at(cues, 70000) == ""        # after the last cue


def test_format_ms_hours_and_minutes():
    assert format_ms(0) == "0:00"
    assert format_ms(65_000) == "1:05"
    assert format_ms(3_600_000) == "1:00:00"


def test_split_text_keeps_words_whole():
    from scripto.gui_qt.player import split_text

    text = "alpha beta gamma delta epsilon"
    chunks = split_text(text, max_chars=12)
    assert chunks == ["alpha beta", "gamma delta", "epsilon"]
    assert all(len(c) <= 12 for c in chunks)
    assert " ".join(chunks) == text  # nothing lost, no word broken


def test_split_text_hard_cuts_spaceless_cjk():
    from scripto.gui_qt.player import split_text

    text = "这是一段没有空格的很长的中文字幕内容"
    chunks = split_text(text, max_chars=8)
    assert all(len(c) <= 8 for c in chunks)
    assert "".join(chunks) == text


DURATION_MS = 600_000


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    """A player over a stub source, with a known duration and no real seeks."""
    from scripto.gui_qt.player import PlayerDialog

    (tmp_path / "en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:09,000\nhello\n", encoding="utf-8"
    )
    (tmp_path / "zh.srt").write_text(
        "1\n00:00:01,000 --> 00:00:09,000\n你好\n", encoding="utf-8"
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")

    dlg = PlayerDialog(
        None, None, str(video),
        {"EN": str(tmp_path / "en.srt"), "ZH": str(tmp_path / "zh.srt")},
    )
    monkeypatch.setattr(dlg.player, "duration", lambda: DURATION_MS)
    dlg.slider.setRange(0, DURATION_MS)
    yield dlg
    dlg.close()


def test_clearing_both_tracks_hides_the_subtitles(dialog):
    dialog._update_subtitles(2000)
    assert [item.isVisible() for item in dialog.sub_items] == [True, True]

    for combo in dialog.sub_combos:
        combo.setCurrentIndex(0)  # "no subtitle" in both slots
    assert [item.isVisible() for item in dialog.sub_items] == [False, False]
    assert [b.isVisible() for b in dialog.sub_backdrops] == [False, False]

    dialog.sub_combos[1].setCurrentIndex(2)  # and back on again
    dialog._update_subtitles(2000)
    assert [item.isVisible() for item in dialog.sub_items] == [False, True]


def test_skip_burst_collapses_into_one_seek(dialog):
    seeks: list[int] = []
    dialog.player.setPosition = lambda ms: seeks.append(ms)

    for _ in range(10):
        dialog._skip(10_000)
    # Nothing has reached the backend yet, but the UI is already there.
    assert seeks == []
    assert dialog.slider.value() == 100_000
    assert dialog.time_label.text().startswith("1:40 /")

    dialog._commit_seek()
    assert seeks == [100_000]


def test_skips_clamp_to_the_media_bounds(dialog):
    seeks: list[int] = []
    dialog.player.setPosition = lambda ms: seeks.append(ms)

    for _ in range(100):
        dialog._skip(10_000)
    dialog._commit_seek()
    assert seeks[-1] == DURATION_MS

    for _ in range(100):
        dialog._skip(-10_000)
    dialog._commit_seek()
    assert seeks[-1] == 0


def test_stale_positions_do_not_snap_the_slider_back(dialog):
    dialog.player.setPosition = lambda ms: None

    dialog._queue_seek(300_000)
    dialog._on_position(12_345)             # pre-seek position, still arriving
    assert dialog.slider.value() == 300_000

    dialog._commit_seek()
    dialog._on_position(300_100)            # backend lands on the target
    assert dialog._seek_target is None
    dialog._on_position(301_000)            # normal tracking resumes
    assert dialog.slider.value() == 301_000


def test_seek_target_recovers_if_the_backend_never_lands(dialog):
    dialog.player.setPosition = lambda ms: None
    dialog._queue_seek(300_000)
    dialog._commit_seek()
    dialog._settle_seek()  # the backstop timer's slot
    assert dialog._seek_target is None


def test_clicking_the_bar_seeks_to_that_point(dialog):
    dialog.slider.resize(400, 20)
    assert dialog.slider.value_at(200) == pytest.approx(DURATION_MS / 2, abs=5_000)
    assert dialog.slider.value_at(0) == 0
    assert dialog.slider.value_at(400) == DURATION_MS


def test_long_cue_splits_time_evenly():
    from scripto.gui_qt.player import build_cues, cue_at

    long_text = " ".join(f"word{i:02d}" for i in range(30))  # 209 chars
    srt = f"1\n00:00:10,000 --> 00:00:16,000\n{long_text}\n"
    cues = build_cues(srt, max_chars=80)
    assert len(cues) == 3
    starts = [c[0] for c in cues]
    ends = [c[1] for c in cues]
    assert starts == [10000, 12000, 14000]   # 6s span shared evenly by 3
    assert ends == [12000, 14000, 16000]     # contiguous, no gaps
    assert cue_at(cues, 11000).startswith("word00")
    assert cue_at(cues, 15999).endswith("word29")
