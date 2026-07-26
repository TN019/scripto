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
