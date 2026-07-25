"""History page: one card per source file, with an in-place viewer dialog
that switches languages and can translate missing ones (R5)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

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
        refresh_btn = QPushButton(t("gui.history_refresh"))
        refresh_btn.clicked.connect(self.refresh)
        clean_btn = QPushButton(t("gui.history_clean"))
        clean_btn.clicked.connect(self._clean)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(refresh_btn)
        top.addWidget(clean_btn)
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

            self.list_box.insertWidget(self.list_box.count() - 1, frame)

    def _clean(self) -> None:
        removed = self.vm.history_clean_missing()
        self.window_ref.toast(self.t("gui.history_cleaned", n=removed))
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
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setProperty("role", "preview")

        close_btn = QPushButton(self.t("gui.close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addLayout(self.lang_row)
        root.addWidget(self.status_label)
        root.addWidget(self.body, 1)
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
        self.lang = lang
        try:
            self.body.setPlainText(self.vm.read_preview(self.group.existing[lang]))
        except Exception as exc:
            self.body.setPlainText(str(exc))
        self._rebuild_buttons()

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
                    try:
                        self.body.setPlainText(
                            self.vm.read_preview(self.group.existing[self.lang])
                        )
                    except Exception:
                        pass
                self._rebuild_buttons()

            window.run_in_main(apply)

        window.run_thread(job)
