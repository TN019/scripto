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

Controls:
- Every position change — buttons, arrow keys, clicking or dragging the bar
  — funnels through ``_queue_seek``: the UI moves at once, the backend is
  seeked once the burst of input stops. Seeking is the expensive operation
  (a decode restart), so one per burst is the difference between instant
  and stuck buffering.
- The transport icons and the seek bar are drawn here rather than borrowed
  from the platform: emoji glyphs and stock slider parts neither match the
  theme nor behave like a player's.
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QTextOption
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
    QToolTip,
    QVBoxLayout,
)

from ..translate.srt import parse_srt
from . import icons, theme
from .widgets import subtext

_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

Cue = tuple[int, int, str]  # start_ms, end_ms, text

MAX_CUE_CHARS = 100
SKIP_MS = 10_000
RATES = (0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)

# A seek is a full decode restart in the media backend, so a burst of skip
# clicks must not become a burst of seeks — the backend would spend the
# whole burst buffering and the UI would freeze on the last one. Clicks
# accumulate into a single pending target, committed once the burst stops.
SEEK_COMMIT_MS = 140
# The position the backend reports lags a committed seek (it lands on a
# keyframe, and stale positions keep arriving meanwhile). Until it settles
# within this tolerance — or the backstop below fires — reported positions
# are ignored, so the slider never snaps back under the user's cursor.
SEEK_SETTLE_MS = 400
SEEK_SETTLE_TIMEOUT_MS = 1200


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


def _controls_qss(p) -> str:
    """Flat, round icon buttons for the transport controls."""
    return f"""
QPushButton[transport="true"] {{
    background: transparent;
    border: none;
    border-radius: 17px;
}}
QPushButton[transport="true"]:hover {{
    background: {p.selection};
}}
"""


class _SeekBar(QSlider):
    """Progress bar that behaves like a player's, not like a scrollbar.

    A plain QSlider page-steps when you click the groove, which is useless
    for seeking: clicking at 40% must jump to 40%. Dragging scrubs live
    (``on_scrub``) and releasing commits (``on_commit``); hovering shows the
    time under the cursor so you can aim before clicking, and thickens the
    bar so a 5px target becomes an easy one.

    It paints itself rather than going through QSS: a styled ``::sub-page``
    ignores the groove's height and floods the whole widget rect, and the
    painted geometry is the same arithmetic that maps clicks to positions,
    so what you point at is what you get.
    """

    TRACK = 5.0
    TRACK_HOVER = 7.0
    DOT = 5.5
    DOT_HOVER = 7.0

    def __init__(self, tokens, on_scrub, on_commit):
        super().__init__(Qt.Orientation.Horizontal)
        self.setObjectName("SeekBar")
        self._tokens = tokens
        self._on_scrub = on_scrub
        self._on_commit = on_commit
        self._scrubbing = False
        self._hovered = False
        self.setFixedHeight(20)
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrows belong to the dialog

    @property
    def scrubbing(self) -> bool:
        return self._scrubbing

    def value_at(self, x: int) -> int:
        """The position (ms) the bar maps to at widget x.

        The mapping spans the full width — the far right edge is the end of
        the media, with no dead margin to miss by.
        """
        fraction = min(1.0, max(0.0, x / max(1.0, float(self.width()))))
        return self.minimum() + round(
            (self.maximum() - self.minimum()) * fraction
        )

    def _x_for(self, value: int) -> float:
        span = self.maximum() - self.minimum()
        fraction = 0.0 if span <= 0 else (value - self.minimum()) / span
        return self.width() * min(1.0, max(0.0, fraction))

    def paintEvent(self, event) -> None:  # noqa: N802
        active = self._hovered or self._scrubbing
        track = self.TRACK_HOVER if active else self.TRACK
        dot = self.DOT_HOVER if active else self.DOT
        top = (self.height() - track) / 2
        radius = track / 2
        played = self._x_for(self.value())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._tokens.border))
        painter.drawRoundedRect(
            QRectF(0, top, self.width(), track), radius, radius
        )
        if self.maximum() > self.minimum():
            painter.setBrush(QColor(self._tokens.accent))
            painter.drawRoundedRect(
                QRectF(0, top, played, track), radius, radius
            )
            painter.setBrush(QColor(self._tokens.accent if active
                                    else self._tokens.subtext))
            # Kept a dot-radius inside the ends so the head never half-clips.
            center = min(max(played, dot), self.width() - dot)
            painter.drawEllipse(QPointF(center, self.height() / 2), dot, dot)
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.maximum() <= 0:
            super().mousePressEvent(event)
            return
        self._scrubbing = True
        self.setSliderDown(True)
        self._scrub_to(event.position().toPoint().x())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().toPoint().x()
        if self._scrubbing:
            self._scrub_to(x)
            event.accept()
            return
        if self.maximum() > 0:
            QToolTip.showText(
                self.mapToGlobal(QPoint(x, -28)),
                format_ms(self.value_at(x)),
                self,
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._scrubbing:
            super().mouseReleaseEvent(event)
            return
        self._scrubbing = False
        self.setSliderDown(False)
        self.setValue(self.value_at(event.position().toPoint().x()))
        self._on_commit(self.value())
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def _scrub_to(self, x: int) -> None:
        self.setValue(self.value_at(x))
        self._on_scrub(self.value())


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
        # None means "not decided yet", which is different from "no text":
        # only the sentinel forces a slot that just went empty to be hidden.
        self._current: list[str | None] = [None, None]

        self._seek_target: int | None = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(SEEK_COMMIT_MS)
        self._seek_timer.timeout.connect(self._commit_seek)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(SEEK_SETTLE_TIMEOUT_MS)
        self._settle_timer.timeout.connect(self._settle_seek)

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

        # Controls: skip / play / skip · seek bar · time · rate · subtitles
        tokens = getattr(window, "palette_tokens", theme.DARK)
        ratio = QApplication.primaryScreen().devicePixelRatio()
        seconds = SKIP_MS // 1000
        self._play_icon = icons.play_icon(tokens.text, ratio=ratio)
        self._pause_icon = icons.pause_icon(tokens.text, ratio=ratio)

        self.back_btn = self._transport(
            icons.skip_icon(seconds, forward=False, color=tokens.text, ratio=ratio)
        )
        self.back_btn.clicked.connect(lambda: self._skip(-SKIP_MS))
        self.play_btn = self._transport(self._pause_icon)
        self.play_btn.clicked.connect(self._toggle)
        self.fwd_btn = self._transport(
            icons.skip_icon(seconds, forward=True, color=tokens.text, ratio=ratio)
        )
        self.fwd_btn.clicked.connect(lambda: self._skip(SKIP_MS))

        self.slider = _SeekBar(tokens, self._queue_seek, self._commit_now)
        self.slider.setRange(0, 0)
        self.time_label = subtext("0:00 / 0:00")
        self.time_label.setMinimumWidth(
            self.time_label.fontMetrics().horizontalAdvance("0:00:00 / 0:00:00")
        )
        self.setStyleSheet(_controls_qss(tokens))

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
        # Visible whenever there is anything to choose: with a single track
        # the selector is still the only way to turn subtitles off.
        for combo in self.sub_combos:
            combo.setVisible(bool(track_labels))

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

    def _transport(self, icon) -> QPushButton:
        button = QPushButton()
        button.setProperty("transport", "true")
        button.setIcon(icon)
        button.setIconSize(QSize(24, 24))  # the icons' natural size: no rescale
        button.setFixedSize(34, 34)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _sync_tracks(self) -> None:
        self._active = [
            self.tracks.get(combo.currentData() or "", [])
            for combo in self.sub_combos
        ]
        self._current = [None, None]  # force a redraw, including "now empty"
        self._update_subtitles(self._display_ms())

    def _toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _display_ms(self) -> int:
        """Where the UI says we are: the pending seek wins over the backend."""
        if self._seek_target is not None:
            return self._seek_target
        return int(self.player.position())

    # ---- seeking ------------------------------------------------------ #

    def _skip(self, delta_ms: int) -> None:
        # Chained off the pending target, so ten quick clicks are +100s and
        # one seek — not ten seeks racing each other from the same origin.
        self._queue_seek(self._display_ms() + delta_ms)

    def _queue_seek(self, ms: int) -> None:
        """Show ``ms`` immediately; seek there once the burst settles."""
        target = max(0, int(ms))
        duration = int(self.player.duration())
        if duration > 0:  # unknown while the source is still loading
            target = min(target, duration)
        self._seek_target = target
        self._settle_timer.stop()
        if not self.slider.scrubbing:
            self.slider.setValue(self._seek_target)
        self._update_time(self._seek_target)
        self._update_subtitles(self._seek_target)
        self._seek_timer.start()

    def _commit_now(self, ms: int) -> None:
        """Release of a scrub: no reason to wait out the debounce."""
        self._queue_seek(ms)
        self._commit_seek()

    def _commit_seek(self) -> None:
        if self._seek_target is None:
            return
        self._seek_timer.stop()
        self.player.setPosition(self._seek_target)
        self._settle_timer.start()

    def _settle_seek(self) -> None:
        """Backstop: a seek that never reports its target must not wedge us."""
        self._seek_target = None

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(self._pause_icon if playing else self._play_icon)

    def _on_position(self, ms: int) -> None:
        ms = int(ms)
        if self._seek_target is not None:
            pending = self._seek_timer.isActive()
            if pending or abs(ms - self._seek_target) > SEEK_SETTLE_MS:
                return  # still queued, or the backend has not landed yet
            self._seek_target = None
            self._settle_timer.stop()
        if self.slider.scrubbing:
            return  # the cursor owns the position, not the backend
        self.slider.setValue(ms)
        self._update_time(ms)
        self._update_subtitles(ms)

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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Space/arrows go through the same coalescing path as the buttons,
        # so held-down arrows scrub instead of drowning the backend.
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._toggle()
        elif key == Qt.Key.Key_Left:
            self._skip(-SKIP_MS)
        elif key == Qt.Key.Key_Right:
            self._skip(SKIP_MS)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._seek_timer.stop()
        self._settle_timer.stop()
        self.player.stop()
        super().closeEvent(event)
