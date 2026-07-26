"""Built-in media player: the source video with live subtitles from the SRT.

Subtitles are drawn by us (a label overlaid on the video), not by the media
backend: that keeps every produced language selectable and renders exactly
what the .srt on disk says — including edits the user just made. Qt 6 ships
the FFmpeg media backend, so mkv/mp4/m4a all play.
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..translate.srt import parse_srt
from .widgets import subtext

_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

Cue = tuple[int, int, str]  # start_ms, end_ms, text


def timestamp_ms(text: str) -> int:
    """`00:01:02,345` (or `.345`) → milliseconds; 0 when unparsable."""
    match = _TIME_RE.search(text)
    if not match:
        return 0
    hours, minutes, seconds, ms = (int(g) for g in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + ms


def build_cues(content: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in parse_srt(content):
        start_raw, _, end_raw = block.timestamp.partition("-->")
        cues.append((timestamp_ms(start_raw), timestamp_ms(end_raw), block.text))
    cues.sort(key=lambda c: (c[0], c[1]))
    return cues


def cue_at(cues: list[Cue], ms: int) -> str:
    """The subtitle text active at ``ms``; empty string between cues."""
    index = bisect.bisect_right(cues, (ms, 1 << 62, "")) - 1
    if index >= 0:
        start, end, text = cues[index]
        if start <= ms < end:
            return text
    return ""


def format_ms(ms: int) -> str:
    seconds = max(0, ms) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class PlayerDialog(QDialog):
    def __init__(self, parent, window, video_path: str, srt_path: str | None):
        super().__init__(parent)
        self.window_ref = window
        self.setWindowTitle(Path(video_path).name)
        self.resize(920, 600)

        self.cues: list[Cue] = []
        if srt_path:
            try:
                self.cues = build_cues(
                    Path(srt_path).read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                pass
        self._current_text = ""
        self._dragging = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        video = QVideoWidget()
        self.player.setVideoOutput(video)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setStyleSheet(
            "background: rgba(0, 0, 0, 168); color: white; font-size: 16px;"
            "padding: 6px 14px; border-radius: 8px; margin-bottom: 26px;"
        )
        self.subtitle.hide()

        stage = QWidget()
        stage.setStyleSheet("background: black;")
        grid = QGridLayout(stage)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(video, 0, 0)
        grid.addWidget(
            self.subtitle, 0, 0,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        )

        self.play_btn = QPushButton("⏸")
        self.play_btn.setFixedWidth(44)
        self.play_btn.clicked.connect(self._toggle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderReleased.connect(self._seek_released)
        self.slider.sliderMoved.connect(self._preview_position)
        self.time_label = subtext("0:00 / 0:00")

        controls = QHBoxLayout()
        controls.setContentsMargins(12, 8, 12, 10)
        controls.setSpacing(10)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.time_label)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(stage, 1)
        root.addLayout(controls)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, int(d)))
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.setSource(QUrl.fromLocalFile(video_path))
        self.player.play()

    # ------------------------------------------------------------------ #

    def _toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸" if playing else "▶")

    def _seek_released(self) -> None:
        self._dragging = False
        self.player.setPosition(self.slider.value())

    def _preview_position(self, ms: int) -> None:
        self._update_subtitle(ms)
        self._update_time(ms)

    def _on_position(self, ms: int) -> None:
        if not self._dragging:
            self.slider.setValue(int(ms))
            self._update_time(int(ms))
        self._update_subtitle(int(ms))

    def _update_time(self, ms: int) -> None:
        self.time_label.setText(
            f"{format_ms(ms)} / {format_ms(int(self.player.duration()))}"
        )

    def _update_subtitle(self, ms: int) -> None:
        text = cue_at(self.cues, ms)
        if text == self._current_text:
            return
        self._current_text = text
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        super().closeEvent(event)
