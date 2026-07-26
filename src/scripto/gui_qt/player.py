"""Built-in media player: the source video with live subtitles from the SRT.

Subtitles are drawn by us (labels overlaid on the video), not by the media
backend: that keeps every produced language selectable and renders exactly
what the .srt on disk says — including edits the user just made. Qt 6 ships
the FFmpeg media backend, so mkv/mp4/m4a all play.

Display rules:
- One subtitle track: shown as-is. Two: stacked, primary over secondary.
  More than two: the selectors in the control bar pick which (defaults:
  the language being viewed + none).
- Overlong cues are split at word boundaries into chunks of at most
  ``MAX_CUE_CHARS`` characters, and the chunks share the cue's time span
  evenly — long paragraphs page through instead of flooding the screen.
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..translate.srt import parse_srt
from .widgets import subtext

_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

Cue = tuple[int, int, str]  # start_ms, end_ms, text

MAX_CUE_CHARS = 100
SKIP_MS = 10_000
RATES = (0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)


def timestamp_ms(text: str) -> int:
    """`00:01:02,345` (or `.345`) → milliseconds; 0 when unparsable."""
    match = _TIME_RE.search(text)
    if not match:
        return 0
    hours, minutes, seconds, ms = (int(g) for g in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + ms


def split_text(text: str, max_chars: int = MAX_CUE_CHARS) -> list[str]:
    """Chunks of at most ``max_chars``, never breaking a word.

    A single word longer than the limit (typical for CJK text, which has no
    spaces) is hard-cut — any cut point is acceptable there.
    """
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return [flat] if flat else []

    chunks: list[str] = []
    current = ""
    for word in flat.split(" "):
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = word
        while len(current) > max_chars:  # oversized single word: hard cut
            chunks.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        chunks.append(current)
    return chunks


def build_cues(content: str, max_chars: int = MAX_CUE_CHARS) -> list[Cue]:
    cues: list[Cue] = []
    for block in parse_srt(content):
        start_raw, _, end_raw = block.timestamp.partition("-->")
        start = timestamp_ms(start_raw)
        end = timestamp_ms(end_raw)
        chunks = split_text(block.text, max_chars)
        if not chunks:
            continue
        # Chunks share the cue's span evenly, so a long paragraph pages
        # through at a steady rhythm instead of sitting there for 20s.
        span = max(0, end - start)
        for i, chunk in enumerate(chunks):
            piece_start = start + span * i // len(chunks)
            piece_end = start + span * (i + 1) // len(chunks)
            cues.append((piece_start, piece_end, chunk))
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
    def __init__(self, parent, window, video_path: str,
                 tracks: dict[str, str] | None = None):
        """``tracks``: display label → .srt path, in preference order."""
        super().__init__(parent)
        self.window_ref = window
        self.setWindowTitle(Path(video_path).name)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(
            min(920, int(screen.width() * 0.8)),
            min(620, int(screen.height() * 0.8)),
        )

        self.tracks: dict[str, list[Cue]] = {}
        for label, path in (tracks or {}).items():
            try:
                self.tracks[label] = build_cues(
                    Path(path).read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                continue
        self._active: list[list[Cue]] = []
        self._current: list[str] = ["", ""]
        self._dragging = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        video = QVideoWidget()
        # Once media loads, QVideoWidget's sizeHint becomes the video's
        # native resolution — a 4K/retina recording would balloon the whole
        # dialog. Ignore the hint: the widget fills whatever space we give.
        video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.player.setVideoOutput(video)

        sub_style = (
            "background: rgba(0, 0, 0, 168); color: white; font-size: {}px;"
            "padding: 4px 12px; border-radius: 7px;"
        )
        self.sub_labels = [QLabel(""), QLabel("")]
        for i, label in enumerate(self.sub_labels):
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(sub_style.format(16 if i == 0 else 14))
            label.hide()
        sub_stack = QWidget()
        sub_box = QVBoxLayout(sub_stack)
        sub_box.setContentsMargins(0, 0, 0, 22)
        sub_box.setSpacing(4)
        for label in self.sub_labels:
            sub_box.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)

        stage = QWidget()
        stage.setStyleSheet("background: black;")
        grid = QGridLayout(stage)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(video, 0, 0)
        grid.addWidget(
            sub_stack, 0, 0,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        )

        # Controls: skip / play / skip · slider · time · rate · subtitles
        self.back_btn = QPushButton("⏪ 10")
        self.back_btn.clicked.connect(lambda: self._skip(-SKIP_MS))
        self.play_btn = QPushButton("⏸")
        self.play_btn.setFixedWidth(44)
        self.play_btn.clicked.connect(self._toggle)
        self.fwd_btn = QPushButton("10 ⏩")
        self.fwd_btn.clicked.connect(lambda: self._skip(SKIP_MS))

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderReleased.connect(self._seek_released)
        self.slider.sliderMoved.connect(self._preview_position)
        self.time_label = subtext("0:00 / 0:00")

        self.rate_combo = QComboBox()
        for rate in RATES:
            self.rate_combo.addItem(f"{rate:g}x", rate)
        self.rate_combo.setCurrentIndex(self.rate_combo.findData(1.0))
        self.rate_combo.currentIndexChanged.connect(
            lambda i: self.player.setPlaybackRate(self.rate_combo.itemData(i))
        )

        track_labels = list(self.tracks)
        none_label = window.t("gui.sub_none") if window is not None else "—"
        self.sub_combos = [QComboBox(), QComboBox()]
        for slot, combo in enumerate(self.sub_combos):
            combo.addItem(none_label, "")
            for label in track_labels:
                combo.addItem(label, label)
            combo.currentIndexChanged.connect(self._sync_tracks)
        # Defaults: first track on top; the second stacks below only when
        # exactly two exist — with more, the user picks (你选).
        if track_labels:
            self.sub_combos[0].setCurrentIndex(1)
        if len(track_labels) == 2:
            self.sub_combos[1].setCurrentIndex(2)
        for combo in self.sub_combos:
            combo.setVisible(len(track_labels) >= 2)

        controls = QHBoxLayout()
        controls.setContentsMargins(12, 8, 12, 10)
        controls.setSpacing(8)
        controls.addWidget(self.back_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.fwd_btn)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.rate_combo)
        controls.addWidget(self.sub_combos[0])
        controls.addWidget(self.sub_combos[1])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(stage, 1)
        root.addLayout(controls)

        self._sync_tracks()
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, int(d)))
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.setSource(QUrl.fromLocalFile(video_path))
        self.player.play()

    # ------------------------------------------------------------------ #

    def _sync_tracks(self) -> None:
        self._active = [
            self.tracks.get(combo.currentData() or "", [])
            for combo in self.sub_combos
        ]
        self._current = ["", ""]
        self._update_subtitles(int(self.player.position()))

    def _toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _skip(self, delta_ms: int) -> None:
        target = int(self.player.position()) + delta_ms
        target = max(0, min(target, int(self.player.duration())))
        self.player.setPosition(target)
        self._update_subtitles(target)

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸" if playing else "▶")

    def _seek_released(self) -> None:
        self._dragging = False
        self.player.setPosition(self.slider.value())

    def _preview_position(self, ms: int) -> None:
        self._update_subtitles(ms)
        self._update_time(ms)

    def _on_position(self, ms: int) -> None:
        if not self._dragging:
            self.slider.setValue(int(ms))
            self._update_time(int(ms))
        self._update_subtitles(int(ms))

    def _update_time(self, ms: int) -> None:
        self.time_label.setText(
            f"{format_ms(ms)} / {format_ms(int(self.player.duration()))}"
        )

    def _update_subtitles(self, ms: int) -> None:
        for i, cues in enumerate(self._active[:2]):
            text = cue_at(cues, ms) if cues else ""
            if text == self._current[i]:
                continue
            self._current[i] = text
            self.sub_labels[i].setText(text)
            self.sub_labels[i].setVisible(bool(text))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        super().closeEvent(event)
