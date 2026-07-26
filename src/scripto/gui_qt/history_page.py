"""History page: one card per source file, with an in-place viewer dialog
that switches languages and can translate missing ones (R5)."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_MS_RE = re.compile(r"[,.]\d{3}")

from .widgets import ElidedLabel, card, clear_layout, reveal_in_file_manager, subtext


class HistoryPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.vm = window.vm
        self.t = window.t
        self._build()

    def _build(self) -> None:
        t = self.t
        self._selected: set[str] = set()
        refresh_btn = QPushButton(t("gui.history_refresh"))
        refresh_btn.clicked.connect(self.refresh)
        clean_btn = QPushButton(t("gui.history_clean"))
        clean_btn.clicked.connect(self._clean)
        self.delete_selected_btn = QPushButton()
        self.delete_selected_btn.setProperty("variant", "danger")
        self.delete_selected_btn.clicked.connect(self._delete_selected)
        self.delete_selected_btn.hide()

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(refresh_btn)
        top.addWidget(clean_btn)
        top.addWidget(self.delete_selected_btn)
        top.addStretch(1)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(6)
        self.list_box.addStretch(1)
        host = QWidget()
        host.setLayout(self.list_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(scroll, 1)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        t = self.t
        clear_layout(self.list_box, keep_tail=1)
        self._selected.clear()
        self._sync_delete_button()

        groups = self.vm.history_groups()
        if not groups:
            empty = subtext(t("gui.history_empty"))
            self.list_box.insertWidget(0, empty)
            return

        for group in groups:
            frame = card()
            row = QHBoxLayout(frame)
            row.setContentsMargins(12, 8, 12, 8)
            row.setSpacing(8)

            select = QCheckBox()
            select.setToolTip(t("gui.history_delete_selected", n="…"))
            select.toggled.connect(
                lambda on, s=group.source: self._toggle_selected(s, on)
            )
            row.addWidget(select)

            name = ElidedLabel(group.name)
            name.setToolTip(group.source)
            langs = " · ".join(
                self.window_ref.lang_label(code) for code in group.existing
            )
            stamp = group.latest_at[:16].replace("T", " ")
            sub = subtext(f"{langs or '—'} · {stamp}")

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            text_col.addWidget(name)
            text_col.addWidget(sub)
            row.addLayout(text_col, 1)

            if group.deleted:
                deleted = subtext(t("gui.history_deleted"))
                deleted.setStyleSheet(
                    f"color: {self.window_ref.palette_tokens.error}; font-style: italic;"
                )
                row.addWidget(deleted)
            else:
                view_btn = QPushButton(t("gui.history_view"))
                view_btn.setProperty("variant", "quiet")
                view_btn.clicked.connect(lambda _=False, g=group: self._open_viewer(g))
                reveal_btn = QPushButton("📂")
                reveal_btn.setProperty("variant", "quiet")
                reveal_btn.setToolTip(t("gui.reveal"))
                first = next(iter(group.existing.values()))
                reveal_btn.clicked.connect(
                    lambda _=False, p=first: reveal_in_file_manager(Path(p))
                )
                row.addWidget(view_btn)
                row.addWidget(reveal_btn)

            remove_btn = QPushButton("✕")
            remove_btn.setProperty("variant", "quiet")
            remove_btn.setFixedWidth(28)
            remove_btn.setToolTip(t("gui.history_delete"))
            remove_btn.clicked.connect(
                lambda _=False, s=group.source: self._delete_sources({s})
            )
            row.addWidget(remove_btn)

            self.list_box.insertWidget(self.list_box.count() - 1, frame)

    def _clean(self) -> None:
        removed = self.vm.history_clean_missing()
        self.window_ref.toast(self.t("gui.history_cleaned", n=removed))
        self.refresh()

    # Deleting removes history records only; files on disk stay untouched.

    def _toggle_selected(self, source: str, on: bool) -> None:
        if on:
            self._selected.add(source)
        else:
            self._selected.discard(source)
        self._sync_delete_button()

    def _sync_delete_button(self) -> None:
        count = len(self._selected)
        self.delete_selected_btn.setVisible(count > 0)
        if count:
            self.delete_selected_btn.setText(
                self.t("gui.history_delete_selected", n=count)
            )

    def _delete_selected(self) -> None:
        self._delete_sources(set(self._selected))

    def _delete_sources(self, sources: set[str]) -> None:
        removed = self.vm.history_delete_sources(sources)
        self.window_ref.toast(self.t("gui.history_deleted_n", n=removed))
        self.refresh()

    # ------------------------------------------------------------------ #
    # Viewer dialog
    # ------------------------------------------------------------------ #

    def _open_viewer(self, group) -> None:
        dialog = _ViewerDialog(self, group)
        dialog.exec()
        self.refresh()


class _ViewerDialog(QDialog):
    """One dialog per source file: switch languages, translate missing ones."""

    def __init__(self, page: HistoryPage, group):
        super().__init__(page)
        self.page = page
        self.t = page.t
        self.vm = page.vm
        self.group = group
        self.lang = next(iter(group.existing), None)
        self.busy = False

        self.setWindowTitle(group.name)
        self.resize(680, 520)

        self.lang_row = QHBoxLayout()
        self.lang_row.setSpacing(6)
        self.lang_row.addStretch(1)
        self.status_label = subtext()
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(False)
        self.body.setProperty("role", "preview")
        self.body.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.editor = QPlainTextEdit()
        self.editor.setProperty("role", "preview")
        self.editor.hide()

        self.play_btn = QPushButton(self.t("gui.play"))
        self.play_btn.clicked.connect(self._play)
        self.edit_btn = QPushButton(self.t("gui.edit"))
        self.edit_btn.clicked.connect(self._start_edit)
        self.save_btn = QPushButton(self.t("gui.save"))
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self._save_edit)
        self.save_btn.hide()
        self.cancel_btn = QPushButton(self.t("gui.cancel"))
        self.cancel_btn.clicked.connect(self._cancel_edit)
        self.cancel_btn.hide()
        close_btn = QPushButton(self.t("gui.close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.play_btn)
        bottom.addWidget(self.edit_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_btn)
        bottom.addWidget(self.save_btn)
        bottom.addWidget(close_btn)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addLayout(self.lang_row)
        root.addWidget(self.status_label)
        root.addWidget(self.body, 1)
        root.addWidget(self.editor, 1)
        root.addLayout(bottom)

        self._rebuild_buttons()
        if self.lang:
            self._load(self.lang)

    def _clear_buttons(self) -> None:
        clear_layout(self.lang_row, keep_tail=1)  # keep the trailing stretch

    def _rebuild_buttons(self) -> None:
        self._clear_buttons()
        insert_at = 0
        for code in self.group.existing:
            btn = QPushButton(self.page.window_ref.lang_label(code))
            if code == self.lang:
                btn.setProperty("variant", "primary")
            btn.setEnabled(not self.busy)
            btn.clicked.connect(lambda _=False, c=code: self._load(c))
            self.lang_row.insertWidget(insert_at, btn)
            insert_at += 1
        for code in self.group.missing:
            btn = QPushButton(
                self.t("gui.history_translate_to",
                       lang=self.page.window_ref.lang_label(code))
            )
            btn.setProperty("variant", "quiet")
            btn.setEnabled(not self.busy)
            btn.clicked.connect(lambda _=False, c=code: self._translate(c))
            self.lang_row.insertWidget(insert_at, btn)
            insert_at += 1

    def _load(self, lang: str) -> None:
        if self.editor.isVisible():
            self._cancel_edit()
        self.lang = lang
        path = self.group.existing[lang]
        try:
            self._render(self.vm.read_preview(path), path)
        except Exception as exc:
            self.body.setPlainText(str(exc))
        self._rebuild_buttons()

    # ------------------------------------------------------------------ #
    # Play (source video + this language's subtitles)
    # ------------------------------------------------------------------ #

    def _play(self) -> None:
        from .player import PlayerDialog

        if not Path(self.group.source).exists():
            self.page.window_ref.toast(
                self.t("gui.player_missing_video"), ok=False
            )
            return
        srt = self.group.existing.get(self.lang or "", "")
        PlayerDialog(
            self, self.page.window_ref, self.group.source,
            srt if srt.endswith(".srt") else None,
        ).exec()

    # ------------------------------------------------------------------ #
    # Edit the underlying file in place
    # ------------------------------------------------------------------ #

    def _current_path(self) -> str | None:
        return self.group.existing.get(self.lang or "")

    def _start_edit(self) -> None:
        path = self._current_path()
        if path is None:
            return
        try:
            # Full file, not read_preview: previews truncate long files and
            # saving a truncated buffer would destroy the tail.
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.editor.setPlainText(content)
        self.busy = True  # freezes language switching + translate buttons
        self._rebuild_buttons()
        self._set_editing(True)

    def _save_edit(self) -> None:
        path = self._current_path()
        if path is None:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.busy = False
        self._set_editing(False)
        self._load(self.lang)
        self.page.window_ref.toast(self.t("gui.settings_saved"))

    def _cancel_edit(self) -> None:
        self.busy = False
        self._set_editing(False)
        self._rebuild_buttons()

    def _set_editing(self, editing: bool) -> None:
        self.editor.setVisible(editing)
        self.body.setVisible(not editing)
        self.save_btn.setVisible(editing)
        self.cancel_btn.setVisible(editing)
        self.edit_btn.setVisible(not editing)
        self.play_btn.setVisible(not editing)

    def _render(self, content: str, path: str) -> None:
        """Subtitles read like a transcript, not like the raw file format:
        one block per cue, dim second-precision time range, normal text."""
        tokens = self.page.window_ref.palette_tokens
        if not path.endswith(".srt"):
            self.body.setHtml(
                f'<pre style="color:{tokens.text}; font-size:12px;">'
                f"{escape(content)}</pre>"
            )
            return

        from ..translate.srt import parse_srt

        parts = []
        for block in parse_srt(content):
            start, _, end = block.timestamp.partition("-->")
            stamp = f"{_MS_RE.sub('', start).strip()} → {_MS_RE.sub('', end).strip()}"
            text = escape(block.text).replace("\n", "<br>")
            parts.append(
                f'<p style="margin:0 0 4px 0;">'
                f'<span style="color:{tokens.subtext}; font-size:11px;">{stamp}</span>'
                f"</p>"
                f'<p style="margin:0 0 14px 0; color:{tokens.text};">{text}</p>'
            )
        self.body.setHtml("".join(parts) or escape(content))

    def _translate(self, lang: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_label.setText(
            self.t("gui.history_translating",
                   lang=self.page.window_ref.lang_label(lang))
        )
        self._rebuild_buttons()
        window = self.page.window_ref

        def job() -> None:
            status_text = ""
            try:
                produced = self.vm.translate_history(self.group, lang)
                if produced:
                    status_text = self.t("gui.history_translate_done")
                    self.group.existing[lang] = str(produced[0])
                    if lang in self.group.missing:
                        self.group.missing.remove(lang)
                    self.lang = lang
                else:
                    status_text = self.t("gui.models_failed", reason="no output")
            except Exception as exc:
                status_text = self.t("gui.models_failed", reason=exc)

            def apply() -> None:
                self.busy = False
                self.status_label.setText(status_text)
                if self.lang in self.group.existing:
                    path = self.group.existing[self.lang]
                    try:
                        self._render(self.vm.read_preview(path), path)
                    except Exception:
                        pass
                self._rebuild_buttons()

            window.run_in_main(apply)

        window.run_thread(job)
