"""Settings page plus its three dialogs: model manager, doctor, updater."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.languages import known_languages
from .widgets import card, subtext


def _combo(options: list[tuple[str, str]], value: str, on_change) -> QComboBox:
    box = QComboBox()
    for data, label in options:
        box.addItem(label, data)
    index = box.findData(value)
    if index >= 0:
        box.setCurrentIndex(index)
    box.currentIndexChanged.connect(lambda i: on_change(box.itemData(i)))
    return box


class SettingsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.vm = window.vm
        self.t = window.t
        self._build()

    def _save(self, key: str, value) -> None:
        self.vm.update_settings(**{key: value})

    def _build(self) -> None:
        t = self.t
        vm = self.vm
        config = vm.get_config()
        lang_label = self.window_ref.lang_label

        language = _combo(
            [("en", "English"), ("zh", "中文")], config.get("language", "en"),
            self._on_ui_language,
        )
        theme = _combo(
            [("system", t("gui.theme_system")), ("light", t("gui.theme_light")),
             ("dark", t("gui.theme_dark"))],
            config.get("theme", "system"),
            self._on_theme,
        )
        model = _combo(
            [(k, f"{label} ({size})") for k, label, size, _ in vm.whisper_model_rows()],
            config["whisper_model"],
            lambda v: self._save("whisper_model", v),
        )
        fmt = _combo(
            [(f, f) for f in ("srt", "txt", "vtt", "json")], config["output_format"],
            lambda v: self._save("output_format", v),
        )
        tlang = _combo(
            [("auto", t("tlang_auto"))]
            + [(s.code, lang_label(s.code)) for s in known_languages()],
            config["transcribe_language"],
            lambda v: self._save("transcribe_language", v),
        )
        memory = _combo(
            [("balanced", t("gui.memory_balanced")), ("low", t("gui.memory_low"))],
            config["memory_mode"],
            lambda v: self._save("memory_mode", v),
        )
        preset = [config.get("ollama_model", "qwen3:8b")]
        ollama = _combo(
            [(m, m) for m in sorted(set(preset + ["qwen3:4b", "qwen3:8b", "qwen3:14b"]))],
            config["ollama_model"],
            lambda v: self._save("ollama_model", v),
        )

        recursive = QCheckBox(t("gui.settings_recursive"))
        recursive.setChecked(bool(config["recursive_scan"]))
        recursive.toggled.connect(lambda on: self._save("recursive_scan", bool(on)))
        overwrite = QCheckBox(t("gui.settings_overwrite"))
        overwrite.setChecked(bool(config["overwrite"]))
        overwrite.toggled.connect(lambda on: self._save("overwrite", bool(on)))

        export = QLineEdit(str(config.get("export_dir") or ""))
        export.setPlaceholderText(t("gui.settings_export"))
        export.editingFinished.connect(
            lambda: self._save("export_dir", export.text().strip() or None)
        )

        manage_btn = QPushButton(t("gui.manage_models"))
        manage_btn.clicked.connect(lambda: ModelManagerDialog(self).exec())
        doctor_btn = QPushButton(t("gui.doctor_run"))
        doctor_btn.clicked.connect(lambda: DoctorDialog(self).exec())
        update_btn = QPushButton(t("gui.update_check"))
        update_btn.clicked.connect(self._check_update)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(manage_btn)
        tools.addWidget(doctor_btn)
        tools.addWidget(update_btn)
        tools.addStretch(1)

        form_frame = card()
        form = QFormLayout(form_frame)
        form.setContentsMargins(18, 16, 18, 16)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.addRow(t("gui.settings_language"), language)
        form.addRow(t("gui.settings_theme"), theme)
        form.addRow(t("gui.settings_model"), model)
        form.addRow(t("gui.settings_format"), fmt)
        form.addRow(t("gui.settings_tlang"), tlang)
        form.addRow(t("gui.settings_memory"), memory)
        form.addRow(t("gui.settings_ollama_model"), ollama)
        form.addRow("", recursive)
        form.addRow("", overwrite)
        form.addRow(t("gui.settings_export"), export)

        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(12)
        box.addWidget(form_frame)
        box.addLayout(tools)
        box.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # ------------------------------------------------------------------ #

    def _on_ui_language(self, value: str) -> None:
        self._save("language", value)
        self.window_ref.remount()

    def _on_theme(self, value: str) -> None:
        self._save("theme", value)
        self.window_ref.apply_theme()

    def _check_update(self) -> None:
        from ..core import update as up

        if self.vm.running:
            self.window_ref.toast(self.t("gui.update_running"), ok=False)
            return
        if up.repo_root() is None:
            self.window_ref.toast(self.t("gui.update_not_checkout"), ok=False)
            return
        UpdateDialog(self).exec()


# ---------------------------------------------------------------------- #
# Model manager
# ---------------------------------------------------------------------- #

class ModelManagerDialog(QDialog):
    def __init__(self, page: SettingsPage):
        super().__init__(page)
        self.page = page
        self.t = page.t
        self.vm = page.vm
        self.busy = False

        self.setWindowTitle(self.t("gui.models_title"))
        self.resize(560, 480)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status_label = subtext()

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        self.rows_box.addStretch(1)
        host = QWidget()
        host.setLayout(self.rows_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)

        close_btn = QPushButton(self.t("gui.close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self.progress)
        root.addWidget(self.status_label)
        root.addWidget(scroll, 1)
        root.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        t = self.t
        while self.rows_box.count() > 1:
            item = self.rows_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        def header(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet("font-weight: 600; margin-top: 6px;")
            return label

        def add(widget) -> None:
            self.rows_box.insertWidget(self.rows_box.count() - 1, widget)

        add(header(t("gui.models_whisper")))
        for key, label, size, installed in self.vm.whisper_model_rows():
            add(self._row(f"{label} · {size}", installed,
                          lambda k=key: self._run(self._download_whisper, k),
                          lambda k=key: self._run(self._delete_whisper, k)))

        add(header(t("gui.models_ollama")))
        client = self.vm.ollama_client()
        if client.is_reachable():
            installed_models = set(client.list_models())
            config = self.vm.get_config()
            preset = {config.get("ollama_model", "qwen3:8b"),
                      "qwen3:4b", "qwen3:8b", "qwen3:14b"}
            for name in sorted(installed_models | preset):
                add(self._row(name, name in installed_models,
                              lambda n=name: self._run(self._pull_ollama, n),
                              lambda n=name: self._run(self._delete_ollama, n)))
        else:
            down = subtext(t("gui.models_ollama_down"))
            down.setStyleSheet(
                f"color: {self.page.window_ref.palette_tokens.warn};"
            )
            add(down)

    def _row(self, label: str, installed: bool, on_download, on_delete) -> QWidget:
        t = self.t
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(8)
        row.addWidget(QLabel(label), 1)
        badge = subtext(
            t("gui.models_installed") if installed else t("gui.models_not_installed")
        )
        if installed:
            badge.setStyleSheet(f"color: {self.page.window_ref.palette_tokens.ok};")
        row.addWidget(badge)
        action = QPushButton(
            t("gui.models_delete") if installed else t("gui.models_download")
        )
        action.setProperty("variant", "quiet")
        action.setEnabled(not self.busy)
        action.clicked.connect(lambda: (on_delete if installed else on_download)())
        row.addWidget(action)
        return host

    def _run(self, fn, arg: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.progress.show()
        self.refresh()
        window = self.page.window_ref

        def job() -> None:
            status_text = ""
            try:
                fn(arg)
                status_text = self.t("gui.models_done", name=arg)
            except Exception as exc:
                status_text = self.t("gui.models_failed", reason=exc)

            def apply() -> None:
                self.busy = False
                self.progress.hide()
                self.status_label.setText(status_text)
                self.refresh()

            window.run_in_main(apply)

        window.run_thread(job)

    def _set_status(self, text: str) -> None:
        self.page.window_ref.run_in_main(lambda: self.status_label.setText(text))

    def _download_whisper(self, key: str) -> None:
        from ..engines.models import download_model, get_spec
        from ..engines.select import resolve_engine_name

        engine_name, _ = resolve_engine_name(self.vm.get_config()["engine"])
        download_model(
            get_spec(key), engine_name,
            progress=lambda done, total: self._set_status(
                self.t("gui.models_working", name=key, detail=f"{done}/{total}")
            ),
        )

    def _delete_whisper(self, key: str) -> None:
        from ..engines.models import delete_model, get_spec
        from ..engines.select import resolve_engine_name

        engine_name, _ = resolve_engine_name(self.vm.get_config()["engine"])
        delete_model(get_spec(key), engine_name)

    def _pull_ollama(self, name: str) -> None:
        self.vm.ollama_client().pull(
            name,
            progress=lambda detail, _frac: self._set_status(
                self.t("gui.models_working", name=name, detail=detail)
            ),
        )

    def _delete_ollama(self, name: str) -> None:
        self.vm.ollama_client().delete(name)


# ---------------------------------------------------------------------- #
# Doctor
# ---------------------------------------------------------------------- #

class DoctorDialog(QDialog):
    def __init__(self, page: SettingsPage):
        super().__init__(page)
        self.page = page
        self.t = page.t
        self.setWindowTitle(self.t("gui.doctor_title"))
        self.resize(560, 380)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(8)
        self.rows_box.addStretch(1)
        host = QWidget()
        host.setLayout(self.rows_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)

        close_btn = QPushButton(self.t("gui.close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        root = QVBoxLayout(self)
        root.addWidget(scroll, 1)
        root.addLayout(bottom)

        self._spinner = subtext("…")
        self.rows_box.insertWidget(0, self._spinner)
        window = page.window_ref
        window.run_thread(lambda: self._check(window))

    def _check(self, window) -> None:
        from ..core.doctor import run_doctor

        results = run_doctor(self.page.vm.get_config())

        def apply() -> None:
            palette = window.palette_tokens
            while self.rows_box.count() > 1:
                item = self.rows_box.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            for result in results:
                if result.ok:
                    mark, color = "✓", palette.ok
                elif result.required:
                    mark, color = "✕", palette.error
                else:
                    mark, color = "!", palette.warn
                host = QWidget()
                box = QVBoxLayout(host)
                box.setContentsMargins(4, 0, 4, 0)
                box.setSpacing(2)
                row = QHBoxLayout()
                row.setSpacing(8)
                icon = QLabel(mark)
                icon.setStyleSheet(f"color: {color}; font-weight: 700;")
                icon.setFixedWidth(16)
                row.addWidget(icon)
                name = QLabel(self.t(f"doctor.{result.key}", detail=result.detail))
                row.addWidget(name, 1)
                if result.ok and result.detail:
                    row.addWidget(subtext(result.detail))
                box.addLayout(row)
                if not result.ok and result.hint:
                    hint = subtext(result.hint)
                    hint.setStyleSheet("font-family: Menlo, Consolas, monospace;")
                    hint.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse
                    )
                    box.addWidget(hint)
                self.rows_box.insertWidget(self.rows_box.count() - 1, host)

        window.run_in_main(apply)


# ---------------------------------------------------------------------- #
# In-app update
# ---------------------------------------------------------------------- #

class UpdateDialog(QDialog):
    def __init__(self, page: SettingsPage):
        super().__init__(page)
        self.page = page
        self.t = page.t
        self.setWindowTitle(self.t("gui.update_title"))
        self.setMinimumWidth(440)

        self.message = QLabel(self.t("gui.update_checking"))
        self.message.setWordWrap(True)
        self.note = subtext()
        self.note.setWordWrap(True)
        self.note.hide()
        self.update_btn = QPushButton(self.t("gui.update_now"))
        self.update_btn.setProperty("variant", "primary")
        self.update_btn.clicked.connect(self._do_update)
        self.update_btn.hide()
        close_btn = QPushButton(self.t("gui.close"))
        close_btn.clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        bottom.addWidget(self.update_btn)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(self.message)
        root.addWidget(self.note)
        root.addLayout(bottom)

        window = page.window_ref
        window.run_thread(self._check)

    def _check(self) -> None:
        from ..core import update as up

        root = up.repo_root()
        status = up.check(root)
        window = self.page.window_ref

        def apply() -> None:
            if not status.ok:
                self.message.setText(
                    self.t("gui.update_check_failed", detail=status.detail)
                )
            elif status.behind == 0:
                self.message.setText(self.t("gui.update_uptodate"))
            elif status.dirty:
                self.message.setText(
                    self.t("gui.update_dirty", count=status.behind)
                )
            else:
                self.message.setText(
                    self.t("gui.update_behind", count=status.behind)
                )
                self.note.setText(self.t("gui.update_restart_note"))
                self.note.show()
                self.update_btn.show()

        window.run_in_main(apply)

    def _do_update(self) -> None:
        from ..core import update as up

        self.update_btn.setEnabled(False)
        self.message.setText(self.t("gui.update_pulling"))
        window = self.page.window_ref
        root = up.repo_root()

        def job() -> None:
            ok, detail = up.pull(root)

            def apply() -> None:
                if not ok:
                    self.update_btn.setEnabled(True)
                    self.message.setText(self.t("gui.update_failed", detail=detail))
                    return
                # New instance boots with the pulled code; this one exits.
                up.spawn_restart(root)
                window.quit_app()

            window.run_in_main(apply)

        window.run_thread(job)


# ---------------------------------------------------------------------- #
# First-run wizard
# ---------------------------------------------------------------------- #

class WizardDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setModal(True)
        t = window.t
        self.setWindowTitle(t("gui.wizard_title"))
        self.setMinimumWidth(420)

        body = QLabel(t("gui.wizard_body"))
        body.setWordWrap(True)

        lang = QComboBox()
        lang.addItem("中文", "zh")
        lang.addItem("English", "en")
        model = QComboBox()
        for key, label, size, _installed in window.vm.whisper_model_rows():
            model.addItem(f"{label} ({size})", key)
        index = model.findData(window.vm.get_config()["whisper_model"])
        if index >= 0:
            model.setCurrentIndex(index)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow(t("gui.settings_language"), lang)
        form.addRow(t("gui.settings_model"), model)

        save_btn = QPushButton(t("gui.wizard_save"))
        save_btn.setProperty("variant", "primary")

        def save() -> None:
            window.vm.update_settings(
                language=lang.currentData() or "zh",
                whisper_model=model.currentData() or "large-v3-turbo",
            )
            self.accept()
            window.remount()

        save_btn.clicked.connect(save)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(save_btn)

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(body)
        root.addLayout(form)
        root.addLayout(bottom)
