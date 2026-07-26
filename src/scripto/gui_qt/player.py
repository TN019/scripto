"""Built-in media player: the source video with live subtitles from the SRT.

Subtitles are drawn by us, not by the media backend: that keeps every
produced language selectable and renders exactly what the .srt on disk says
— including edits the user just made. Qt 6 ships the FFmpeg media backend,
so mkv/mp4/m4a all play.

Rendering goes through QGraphicsView + QGraphicsVideoItem, NOT QVideoWidget:
the widget is a native window on macOS, so sibling widgets can never paint
on top of it — subtitles overlaid that way show up headless but silently
vanish on a real display. Scene items composite correctly everywhere.

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

from PySide6.QtCore import QSizeF, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QTextOption
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QVBoxLayout,
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


class _StageView(QGraphicsView):
    """Black letterbox stage; notifies the dialog on every resize."""

    def __init__(self, on_resize):
        super().__init__()
        self._on_resize = on_resize
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # setBackgroundBrush, not QSS: stylesheets do not reliably reach a
        # QGraphicsView viewport.
        self.setBackgroundBrush(QColor(0, 0, 0))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._on_resize()


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

        # Scene-based rendering; see the module docstring for why not
        # QVideoWidget. The video item is resized to the viewport (aspect
        # kept inside), subtitle items float above it as scene children.
        self.view = _StageView(self._relayout_stage)
        scene = QGraphicsScene(self)
        self.view.setScene(scene)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        scene.addItem(self.video_item)
        self.player.setVideoOutput(self.video_item)

        # QGraphicsTextItem ignores CSS backgrounds — each subtitle is a
        # white text item over its own translucent backdrop rect.
        self.sub_items: list[QGraphicsTextItem] = []
        self.sub_backdrops: list[QGraphicsRectItem] = []
        for i in range(2):
            backdrop = QGraphicsRectItem()
            backdrop.setBrush(QColor(0, 0, 0, 180))
            backdrop.setPen(Qt.PenStyle.NoPen)
            backdrop.setZValue(9)
            backdrop.hide()
            scene.addItem(backdrop)
            self.sub_backdrops.append(backdrop)

            item = QGraphicsTextItem()
            item.setDefaultTextColor(QColor(255, 255, 255))
            font = QFont()
            font.setPixelSize(16 if i == 0 else 14)
            item.setFont(font)
            option = QTextOption()
            option.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item.document().setDefaultTextOption(option)
            item.document().setDocumentMargin(5)
            item.setZValue(10)
            item.hide()
            scene.addItem(item)
            self.sub_items.append(item)

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
        root.addWidget(self.view, 1)
        root.addLayout(controls)

        self._sync_tracks()
        self.video_item.nativeSizeChanged.connect(lambda _s: self._relayout_stage())
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
        changed = False
        for i, cues in enumerate(self._active[:2]):
            text = cue_at(cues, ms) if cues else ""
            if text == self._current[i]:
                continue
            self._current[i] = text
            changed = True
            item = self.sub_items[i]
            if not text:
                item.hide()
                self.sub_backdrops[i].hide()
                continue
            item.setPlainText(text)
            doc = item.document()
            doc.setTextWidth(-1)  # measure the natural single-line width…
            max_width = self.view.viewport().width() * 0.86
            if doc.idealWidth() > max_width:  # …then wrap only when needed
                doc.setTextWidth(max_width)
            item.show()
            self.sub_backdrops[i].show()
        if changed:
            self._position_subtitles()

    def _relayout_stage(self) -> None:
        viewport = self.view.viewport().size()
        self.view.scene().setSceneRect(0, 0, viewport.width(), viewport.height())
        # Size the item to the aspect-fitted video rect ourselves instead of
        # the whole viewport: the item's internal letterbox area is not
        # reliably transparent on every render path, the scene background is.
        native = self.video_item.nativeSize()
        if native.isValid() and native.width() > 0 and native.height() > 0:
            scale = min(viewport.width() / native.width(),
                        viewport.height() / native.height())
            width = native.width() * scale
            height = native.height() * scale
        else:
            width, height = viewport.width(), viewport.height()
        self.video_item.setSize(QSizeF(width, height))
        self.video_item.setPos(
            (viewport.width() - width) / 2, (viewport.height() - height) / 2
        )
        self._position_subtitles()
        # The area the video item vacated is not reliably repainted by
        # damage tracking alone (stale-pixel bands on some render paths).
        self.view.viewport().update()

    def _position_subtitles(self) -> None:
        width = self.view.viewport().width()
        y = self.view.viewport().height() - 20
        # Bottom-up: the secondary track hugs the bottom, primary above it.
        for item, backdrop in zip(reversed(self.sub_items),
                                  reversed(self.sub_backdrops)):
            if not item.isVisible():
                continue
            rect = item.boundingRect()
            y -= rect.height()
            x = (width - rect.width()) / 2
            item.setPos(x, y)
            backdrop.setRect(x - 6, y - 1, rect.width() + 12, rect.height() + 2)
            y -= 6

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        super().closeEvent(event)
