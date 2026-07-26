"""Player cue logic: timestamp parsing, cue lookup, time formatting."""

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
