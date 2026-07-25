"""Run page: inputs → file rows → bottom action bar, with drag & drop.

The page owns no state: it renders vm.rows and reacts to DrainResult deltas
handed down by MainWindow's ticker. Scanning runs on a worker thread after a
debounce; the paths box accepts drops of files and folders.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.jobs import JobStatus
from ..core.languages import known_languages
from .widgets import ElidedLabel, reveal_in_file_manager, subtext

SCAN_DEBOUNCE_MS = 600

STATUS_ROLES = {
    JobStatus.PENDING.value: "subtext",
    JobStatus.EXTRACTING.value: "running",
    JobStatus.TRANSCRIBING.value: "running",
    JobStatus.TRANSLATING.value: "running",
    JobStatus.DONE.value: "ok",
    JobStatus.SKIPPED.value: "subtext",
    JobStatus.FAILED.value: "error",
    JobStatus.UNPROCESSED.value: "warn",
}

ACTIVE_STATUSES = (
    JobStatus.EXTRACTING.value,
    JobStatus.TRANSCRIBING.value,
    JobStatus.TRANSLATING.value,
)


class DropPathsEdit(QPlainTextEdit):
    """Paths textarea that also accepts Finder/Explorer drops."""

    def __init__(self, on_dropped, parent=None):
        super().__init__(parent)
        self._on_dropped = on_dropped
        self.setAcceptDrops(True)

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802
        return source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            if paths:
                self._on_dropped(paths)
                return
        super().insertFromMimeData(source)


class FileRowWidget(QFrame):
    """One file row; mutated in place, updated only when its data changes."""

    def __init__(self, page: "RunPage", row):
        super().__init__()
        self.setProperty("card", "true")
        self.row_id = row.id
        t = page.t

        self.status_label = QLabel()
        self.status_label.setFixedWidth(150)
        self.name_label = ElidedLabel(row.name)
        self.name_label.setToolTip(str(row.path))
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(120)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.retry_btn = QPushButton(t("gui.retry"))
        self.retry_btn.setProperty("variant", "quiet")
        self.retry_btn.clicked.connect(lambda: page.retry_row(self.row_id))
        self.retry_btn.hide()
        self.reveal_btn = QPushButton("📂")
        self.reveal_btn.setProperty("variant", "quiet")
        self.reveal_btn.setToolTip(t("gui.reveal"))
        self.reveal_btn.clicked.connect(lambda: page.reveal_row(self.row_id))
        self.reveal_btn.hide()
        self.error_label = subtext()
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.status_label)
        top.addWidget(self.name_label, 1)
        top.addWidget(self.progress)
        top.addWidget(self.retry_btn)
        top.addWidget(self.reveal_btn)

        box = QVBoxLayout(self)
        box.setContentsMargins(12, 7, 12, 7)
        box.setSpacing(2)
        box.addLayout(top)
        box.addWidget(self.error_label)

        self.apply(page, row)

    def apply(self, page: "RunPage", row) -> None:
        palette = page.window_ref.palette_tokens
        color = {
            "subtext": palette.subtext, "running": palette.running,
            "ok": palette.ok, "warn": palette.warn, "error": palette.error,
        }[STATUS_ROLES.get(row.status, "subtext")]
        self.status_label.setText(page.t(f"status.{row.status}"))
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px;")

        active = row.status in ACTIVE_STATUSES
        show_progress = active and row.progress > 0
        self.progress.setVisible(show_progress)
        if show_progress:
            self.progress.setValue(int(row.progress * 1000))

        failed = row.status == JobStatus.FAILED.value
        done = row.status in (JobStatus.DONE.value, JobStatus.SKIPPED.value)
        self.error_label.setText(row.error)
        self.error_label.setStyleSheet(f"color: {palette.error}; font-size: 11px;")
        self.error_label.setVisible(failed and bool(row.error))
        self.retry_btn.setVisible(failed)
        self.reveal_btn.setVisible(done or failed)


class RunPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.vm = window.vm
        self.t = window.t
        self.row_widgets: dict[int, FileRowWidget] = {}
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(SCAN_DEBOUNCE_MS)
        self._scan_timer.timeout.connect(self._scan_now)
        self.log_filter = ""
        self._build()

    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        t = self.t

        self.paths_edit = DropPathsEdit(self._append_paths)
        self.paths_edit.setPlaceholderText(t("gui.paths_hint"))
        self.paths_edit.setFixedHeight(74)
        self.paths_edit.textChanged.connect(self._scan_timer.start)

        pick_files = QPushButton(t("gui.pick_files"))
        pick_files.clicked.connect(self._pick_files)
        pick_folder = QPushButton(t("gui.pick_folder"))
        pick_folder.clicked.connect(self._pick_folder)
        clear_btn = QPushButton(t("gui.clear"))
        clear_btn.clicked.connect(self._clear)
        self.scan_status = subtext()

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(pick_files)
        buttons.addWidget(pick_folder)
        buttons.addWidget(clear_btn)
        buttons.addWidget(self.scan_status)
        buttons.addStretch(1)

        # File rows in a scroll area
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        self.rows_box.addStretch(1)
        rows_host = QWidget()
        rows_host.setLayout(self.rows_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rows_host)

        # Collapsible event log
        self.log_toggle = QToolButton()
        self.log_toggle.setText(f"▸ {t('gui.log_title')}")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setProperty("nav", "true")
        self.log_toggle.toggled.connect(self._toggle_log)
        self.log_panel = QWidget()
        self.log_panel.hide()
        self.log_filter_edit = QLineEdit()
        self.log_filter_edit.setPlaceholderText(t("gui.log_filter"))
        self.log_filter_edit.textChanged.connect(self._on_log_filter)
        log_copy = QPushButton(t("gui.log_copy"))
        log_copy.setProperty("variant", "quiet")
        log_copy.clicked.connect(self._copy_log)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setProperty("role", "log")
        self.log_view.setFixedHeight(140)
        log_top = QHBoxLayout()
        log_top.addWidget(self.log_filter_edit, 1)
        log_top.addWidget(log_copy)
        log_box = QVBoxLayout(self.log_panel)
        log_box.setContentsMargins(0, 0, 0, 0)
        log_box.setSpacing(4)
        log_box.addLayout(log_top)
        log_box.addWidget(self.log_view)

        # Bottom bar
        self.bar_progress = QProgressBar()
        self.bar_progress.setRange(0, 1000)
        self.bar_progress.setValue(0)
        self.bar_progress.setTextVisible(False)
        self.start_btn = QPushButton(t("gui.start"))
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton(t("gui.stop"))
        self.stop_btn.setProperty("variant", "danger")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.hide()
        self.bar_text = QLabel(t("gui.idle"))
        self.bar_text.setStyleSheet("font-weight: 600;")
        self.bar_detail = subtext()
        self.tlang_combo = QComboBox()
        self.tlang_combo.setToolTip(t("gui.settings_tlang"))
        self.tlang_combo.addItem(t("tlang_auto"), "auto")
        for spec in known_languages():
            self.tlang_combo.addItem(self.window_ref.lang_label(spec.code), spec.code)
        tlang_index = self.tlang_combo.findData(
            self.vm.get_config()["transcribe_language"]
        )
        if tlang_index >= 0:
            self.tlang_combo.setCurrentIndex(tlang_index)
        self.tlang_combo.currentIndexChanged.connect(
            lambda i: self.vm.update_settings(
                transcribe_language=self.tlang_combo.itemData(i)
            )
        )

        self.ollama_btn = QPushButton(t("gui.ollama_start"))
        self.ollama_btn.setToolTip(t("gui.models_ollama_down"))
        self.ollama_btn.clicked.connect(self._start_ollama)
        self.ollama_btn.hide()

        self.translate_check = QCheckBox(t("gui.translate_toggle"))
        self.translate_check.setChecked(bool(self.vm.get_config()["translate_enabled"]))
        self.translate_check.toggled.connect(self._on_translate_toggled)
        self.target_combo = QComboBox()
        for spec in known_languages():
            self.target_combo.addItem(self.window_ref.lang_label(spec.code), spec.code)
        current = self.vm.get_config()["translate_target"]
        index = self.target_combo.findData(current)
        if index >= 0:
            self.target_combo.setCurrentIndex(index)
        self.target_combo.currentIndexChanged.connect(
            lambda i: self.vm.update_settings(
                translate_target=self.target_combo.itemData(i)
            )
        )

        bar_row = QHBoxLayout()
        bar_row.setSpacing(12)
        bar_row.addWidget(self.start_btn)
        bar_row.addWidget(self.stop_btn)
        bar_row.addWidget(self.bar_text)
        bar_row.addWidget(self.bar_detail)
        bar_row.addStretch(1)
        bar_row.addWidget(self.ollama_btn)
        bar_row.addWidget(subtext(t("gui.settings_tlang")))
        bar_row.addWidget(self.tlang_combo)
        bar_row.addSpacing(8)
        bar_row.addWidget(self.translate_check)
        bar_row.addWidget(self.target_combo)

        bottom = QFrame()
        bottom.setObjectName("BottomBar")
        bottom_box = QVBoxLayout(bottom)
        bottom_box.setContentsMargins(16, 10, 16, 12)
        bottom_box.setSpacing(8)
        bottom_box.addWidget(self.bar_progress)
        bottom_box.addLayout(bar_row)

        content = QVBoxLayout()
        content.setContentsMargins(16, 12, 16, 4)
        content.setSpacing(10)
        content.addWidget(self.paths_edit)
        content.addLayout(buttons)
        content.addWidget(scroll, 1)
        content.addWidget(self.log_toggle)
        content.addWidget(self.log_panel)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(content, 1)
        root.addWidget(bottom)

        if self.translate_check.isChecked():
            self._check_ollama_async()

    # ------------------------------------------------------------------ #
    # Ollama availability (translate needs a running server)
    # ------------------------------------------------------------------ #

    def _on_translate_toggled(self, on: bool) -> None:
        self.vm.update_settings(translate_enabled=bool(on))
        if on:
            self._check_ollama_async()
        else:
            self.ollama_btn.hide()

    def _check_ollama_async(self) -> None:
        client = self.vm.ollama_client()

        def job() -> None:
            reachable = client.is_reachable()
            self.window_ref.run_in_main(
                lambda: self.ollama_btn.setVisible(
                    not reachable and self.translate_check.isChecked()
                )
            )

        self.window_ref.run_thread(job)

    def _start_ollama(self) -> None:
        self.ollama_btn.setEnabled(False)
        self.window_ref.toast(self.t("gui.ollama_starting"))

        def done(ok: bool, message: str) -> None:
            self.ollama_btn.setEnabled(True)
            self.ollama_btn.setVisible(not ok and self.translate_check.isChecked())
            self.window_ref.toast(message, ok=ok)

        self.window_ref.start_ollama(done)

    # ------------------------------------------------------------------ #
    # Inputs
    # ------------------------------------------------------------------ #

    def _append_paths(self, paths: list[str]) -> None:
        lines = [l for l in self.paths_edit.toPlainText().splitlines() if l.strip()]
        for p in paths:
            if p not in lines:
                lines.append(p)
        self.paths_edit.setPlainText("\n".join(lines))
        self._scan_timer.start(0)

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, self.t("gui.pick_files"))
        if files:
            self._append_paths(files)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.t("gui.pick_folder"))
        if folder:
            self._append_paths([folder])

    def _clear(self) -> None:
        if self.vm.running:
            return
        self.paths_edit.blockSignals(True)
        self.paths_edit.setPlainText("")
        self.paths_edit.blockSignals(False)
        self.scan_status.setText("")
        self.vm.clear_files()
        self.rebuild_rows()

    def _scan_now(self) -> None:
        text = self.paths_edit.toPlainText()
        self.scan_status.setText(self.t("gui.scanning"))

        def job() -> None:
            count, _warnings = self.vm.scan_inputs(text)
            def apply() -> None:
                self.scan_status.setText(
                    self.t("gui.scan_found", n=count) if text.strip() else ""
                )
                self.rebuild_rows()
            self.window_ref.run_in_main(apply)

        threading.Thread(target=job, name="scripto-scan", daemon=True).start()

    # ------------------------------------------------------------------ #
    # Rows
    # ------------------------------------------------------------------ #

    def rebuild_rows(self) -> None:
        while self.rows_box.count() > 1:  # keep the trailing stretch
            item = self.rows_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # off-screen now, not at next event loop
                widget.deleteLater()
        self.row_widgets = {}
        for row_id in self.vm.row_order:
            widget = FileRowWidget(self, self.vm.rows[row_id])
            self.row_widgets[row_id] = widget
            self.rows_box.insertWidget(self.rows_box.count() - 1, widget)

    def retry_row(self, row_id: int) -> None:
        if self.vm.start_batch(only_ids=[row_id]):
            self.sync_buttons(running=True)

    def reveal_row(self, row_id: int) -> None:
        row = self.vm.rows.get(row_id)
        if row is not None:
            reveal_in_file_manager(row.path)

    # ------------------------------------------------------------------ #
    # Batch control (ticker below is driven by MainWindow)
    # ------------------------------------------------------------------ #

    def _start(self) -> None:
        if not self.vm.start_batch():
            self.window_ref.toast(self.t("gui.toast_no_files"), ok=False)
            return
        self.sync_buttons(running=True)

    def _stop(self) -> None:
        self.vm.request_stop()
        self.stop_btn.setEnabled(False)
        self.bar_text.setText(self.t("gui.stopping"))

    def sync_buttons(self, *, running: bool) -> None:
        self.start_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.stop_btn.setEnabled(True)
        self.paths_edit.setReadOnly(running)

    def apply_drain(self, result) -> None:
        for row_id in result.changed_rows:
            widget = self.row_widgets.get(row_id)
            row = self.vm.rows.get(row_id)
            if widget is not None and row is not None:
                widget.apply(self, row)

        snap = result.snapshot
        self.bar_progress.setValue(
            int(snap.done / snap.total * 1000) if snap.total else 0
        )
        if snap.running:
            parts = [self.t("gui.progress", done=snap.done, total=snap.total)]
            if snap.current_name:
                parts.append(
                    f"{snap.current_name} · {self.t('status.' + snap.current_status)}"
                )
            self.bar_text.setText("  ".join(parts))
            self.bar_detail.setText(
                self.t("gui.eta", min=max(1, round(snap.eta_sec / 60)))
                if snap.eta_sec else ""
            )
        else:
            self.bar_text.setText(self.t("gui.idle"))
            self.bar_detail.setText("")

        if result.finished:
            self.sync_buttons(running=False)
            failed = len(self.vm.failed_rows())
            if self.vm.stop_requested:
                self.window_ref.toast(self.t("gui.toast_stopped"), ok=False)
            elif failed:
                self.window_ref.toast(self.t("gui.toast_failed", n=failed), ok=False)
            else:
                self.window_ref.toast(self.t("gui.toast_done"))

    def refresh_log(self, lines: list[str]) -> None:
        needle = self.log_filter.strip().casefold()
        shown = [l for l in lines if not needle or needle in l.casefold()]
        self.log_view.setPlainText("\n".join(shown[-200:]))
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------ #

    def _toggle_log(self, open_: bool) -> None:
        self.log_toggle.setText(
            f"{'▾' if open_ else '▸'} {self.t('gui.log_title')}"
        )
        self.log_panel.setVisible(open_)

    def _on_log_filter(self, text: str) -> None:
        self.log_filter = text or ""
        self.refresh_log(self.vm.log_lines)

    def _copy_log(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(self.vm.log_lines))
        self.window_ref.toast(self.t("gui.toast_copied"))
