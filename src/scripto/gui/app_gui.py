"""Flet view layer: thin renderer over GuiViewModel.

Hard rules (my-transcriptor lessons):
- UI thread never touches disk or network: scanning is debounced onto a
  worker, batch/model work runs on threads, results arrive via vm.drain().
- Rendering is incremental: one control per file row, only changed rows are
  updated, drained at ~4 Hz.
- Every visible string goes through i18n (R7); switching language remounts
  the whole view so nothing is left behind.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import flet as ft

from ..core.jobs import JobStatus
from ..i18n import I18n
from .viewmodel import FileRow, GuiViewModel

TICK_SEC = 0.25
SCAN_DEBOUNCE_SEC = 0.6

STATUS_COLORS = {
    JobStatus.PENDING.value: ft.Colors.ON_SURFACE_VARIANT,
    JobStatus.EXTRACTING.value: ft.Colors.BLUE_400,
    JobStatus.TRANSCRIBING.value: ft.Colors.BLUE_600,
    JobStatus.TRANSLATING.value: ft.Colors.INDIGO_400,
    JobStatus.DONE.value: ft.Colors.GREEN_600,
    JobStatus.SKIPPED.value: ft.Colors.GREY_500,
    JobStatus.FAILED.value: ft.Colors.RED_600,
    JobStatus.UNPROCESSED.value: ft.Colors.ORANGE_600,
}


def reveal_in_file_manager(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def choose_folder_via_osascript() -> str | None:
    """Native folder picker fallback (Flet's picker can't return mac paths)."""
    try:
        proc = subprocess.run(
            ["osascript", "-e", "POSIX path of (choose folder)"],
            capture_output=True, text=True, timeout=300,
        )
        path = proc.stdout.strip()
        return path or None
    except Exception:
        return None


class RowControl:
    """One file row; mutated in place, updated only when its data changes."""

    def __init__(self, app: "GuiApp", row: FileRow):
        self.row_id = row.id
        t = app.t
        self.status_text = ft.Text("", size=12, width=150)
        self.name_text = ft.Text(
            row.name, size=13, expand=True, no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(row.path),
        )
        self.progress = ft.ProgressBar(value=0, width=120, visible=False)
        self.error_text = ft.Text(
            "", size=11, color=ft.Colors.RED_600, visible=False,
            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
        )
        self.retry_btn = ft.TextButton(
            t("gui.retry"), visible=False,
            on_click=lambda _e, i=row.id: app.retry_row(i),
        )
        self.reveal_btn = ft.IconButton(
            ft.Icons.FOLDER_OPEN, icon_size=16, tooltip=t("gui.reveal"),
            on_click=lambda _e, i=row.id: app.reveal_row(i),
        )
        self.container = ft.Container(
            content=ft.Column(
                spacing=2,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.status_text, self.name_text,
                            self.progress, self.retry_btn, self.reveal_btn,
                        ],
                    ),
                    ft.Row(spacing=8, controls=[self.error_text]),
                ],
            ),
            padding=ft.Padding(10, 6, 10, 6),
            border_radius=8,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        )
        self.apply(app, row)

    def apply(self, app: "GuiApp", row: FileRow) -> None:
        t = app.t
        self.status_text.value = t(f"status.{row.status}")
        self.status_text.color = STATUS_COLORS.get(row.status)
        active = row.status in (
            JobStatus.EXTRACTING.value, JobStatus.TRANSCRIBING.value,
            JobStatus.TRANSLATING.value,
        )
        self.progress.visible = active and row.progress > 0
        self.progress.value = row.progress if row.progress > 0 else None
        failed = row.status == JobStatus.FAILED.value
        self.error_text.value = row.error
        self.error_text.visible = failed and bool(row.error)
        self.retry_btn.visible = failed
        done = row.status in (JobStatus.DONE.value, JobStatus.SKIPPED.value)
        self.reveal_btn.visible = done or failed
        self.container.bgcolor = (
            ft.Colors.RED_50 if failed and app.light_mode else
            (ft.Colors.with_opacity(0.08, ft.Colors.RED) if failed else None)
        )


class GuiApp:
    def __init__(self, page: ft.Page, vm: GuiViewModel):
        self.page = page
        self.vm = vm
        self.i18n = I18n(lambda: vm.get_config().get("language", ""))
        self.row_controls: dict[int, RowControl] = {}
        self._scan_task: asyncio.Task | None = None
        self._last_snapshot: tuple = ()
        self._last_log_len = 0
        self._was_running = False
        self.log_filter = ""

    # convenience
    def t(self, key: str, **kw) -> str:
        return self.i18n.t(key, **kw)

    @property
    def light_mode(self) -> bool:
        return self.page.theme_mode == ft.ThemeMode.LIGHT

    # ------------------------------------------------------------------ #
    # Mount / remount (language switch rebuilds everything — R7)
    # ------------------------------------------------------------------ #

    def mount(self) -> None:
        page = self.page
        config = self.vm.get_config()
        page.title = "Scripto"
        page.padding = 0
        theme = config.get("theme", "system")
        page.theme_mode = {
            "light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK,
        }.get(theme, ft.ThemeMode.SYSTEM)
        page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO, use_material3=True)
        page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO, use_material3=True)
        page.window.width = int(config.get("gui_window_width", 1000) or 1000)
        page.window.height = int(config.get("gui_window_height", 760) or 760)
        page.window.min_width = 760
        page.window.min_height = 560
        page.window.on_resized = self._on_resized

        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)

        self._build_run_tab()
        self._build_history_tab()
        self._build_settings_tab()

        # flet 0.86 tabs: Tabs(length) wraps a TabBar + TabBarView pair.
        self.tab_view = ft.TabBarView(
            expand=True,
            controls=[self.run_tab, self.history_tab, self.settings_tab],
        )
        self.tabs = ft.Tabs(
            length=3,
            selected_index=0,
            expand=True,
            on_change=self._on_tab_change,
            content=ft.Column(
                spacing=0,
                controls=[
                    ft.TabBar(tabs=[
                        ft.Tab(label=self.t("gui.tab_run")),
                        ft.Tab(label=self.t("gui.tab_history")),
                        ft.Tab(label=self.t("gui.tab_settings")),
                    ]),
                    self.tab_view,
                ],
            ),
        )
        page.controls.clear()
        page.add(self.tabs)
        page.update()

        if self.vm.is_first_run():
            self._show_wizard()
        page.run_task(self._ticker)
        page.run_thread(self._startup_doctor)

    def remount(self) -> None:
        self.row_controls.clear()
        self._last_snapshot = ()
        self._last_log_len = 0
        self.mount()
        self._rebuild_rows()

    # ------------------------------------------------------------------ #
    # Run tab
    # ------------------------------------------------------------------ #

    def _build_run_tab(self) -> None:
        t = self.t
        self.paths_input = ft.TextField(
            label=t("gui.paths_label"), hint_text=t("gui.paths_hint"),
            multiline=True, min_lines=2, max_lines=4, text_size=13,
            border_radius=8, on_change=self._on_paths_change,
        )
        self.scan_status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.file_list = ft.ListView(spacing=6, expand=True, padding=ft.Padding(0, 4, 0, 4))

        self.translate_switch = ft.Switch(
            label=t("gui.translate_toggle"),
            value=bool(self.vm.get_config()["translate_enabled"]),
            on_change=lambda e: self._save_setting("translate_enabled", bool(e.control.value)),
        )
        self.target_dd = ft.Dropdown(
            label=t("gui.target"), width=130, border_radius=8,
            value=self.vm.get_config()["translate_target"],
            options=[ft.dropdown.Option("zh", t("tlang_zh")),
                     ft.dropdown.Option("en", t("tlang_en"))],
            on_select=lambda e: self._save_setting("translate_target", e.control.value),
        )

        # Bottom action bar — always visible (the old GUI buried Start in a card)
        self.start_btn = ft.FilledButton(
            t("gui.start"), icon=ft.Icons.PLAY_ARROW_ROUNDED, on_click=self._on_start,
        )
        self.stop_btn = ft.FilledButton(
            t("gui.stop"), icon=ft.Icons.STOP_ROUNDED, visible=False,
            style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700, color=ft.Colors.WHITE),
            on_click=self._on_stop,
        )
        self.bar_progress = ft.ProgressBar(value=0, expand=True, height=6, border_radius=3)
        self.bar_text = ft.Text(t("gui.idle"), size=13, weight=ft.FontWeight.W_500)
        self.bar_detail = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        bottom_bar = ft.Container(
            padding=ft.Padding(16, 10, 16, 12),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            content=ft.Column(
                spacing=6,
                controls=[
                    self.bar_progress,
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.start_btn, self.stop_btn,
                            self.bar_text, self.bar_detail,
                            ft.Container(expand=True),
                            self.translate_switch, self.target_dd,
                        ],
                    ),
                ],
            ),
        )

        # Collapsible event log
        self.log_list = ft.ListView(spacing=2, height=140, auto_scroll=True)
        self.log_filter_input = ft.TextField(
            hint_text=t("gui.log_filter"), dense=True, expand=True, text_size=12,
            border_radius=8, prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_log_filter,
        )
        log_panel = ft.ExpansionTile(
            title=ft.Text(t("gui.log_title"), size=13),
            expanded=False,
            controls=[
                ft.Row(controls=[
                    self.log_filter_input,
                    ft.IconButton(ft.Icons.CONTENT_COPY, icon_size=16,
                                  tooltip=t("gui.log_copy"), on_click=self._on_log_copy),
                ]),
                ft.Container(self.log_list, padding=ft.Padding(8, 0, 8, 8)),
            ],
        )

        self.run_tab = ft.Container(
            padding=ft.Padding(16, 12, 16, 0),
            content=ft.Column(
                spacing=10,
                controls=[
                    self.paths_input,
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.OutlinedButton(t("gui.pick_files"), icon=ft.Icons.ATTACH_FILE,
                                              on_click=self._on_pick_files),
                            ft.OutlinedButton(t("gui.pick_folder"), icon=ft.Icons.FOLDER_OUTLINED,
                                              on_click=self._on_pick_folder),
                            ft.OutlinedButton(t("gui.clear"), icon=ft.Icons.CLEAR_ALL,
                                              on_click=self._on_clear),
                            self.scan_status,
                        ],
                    ),
                    self.file_list,
                    log_panel,
                    bottom_bar,
                ],
            ),
        )

    # ------------------------------------------------------------------ #
    # Input handling: debounce → thread scan (UI thread does zero disk IO)
    # ------------------------------------------------------------------ #

    def _on_paths_change(self, _e: ft.ControlEvent) -> None:
        if self._scan_task is not None:
            self._scan_task.cancel()
        self._scan_task = self.page.run_task(self._debounced_scan)

    async def _debounced_scan(self) -> None:
        try:
            await asyncio.sleep(SCAN_DEBOUNCE_SEC)
        except asyncio.CancelledError:
            return
        await self._scan_now()

    async def _scan_now(self) -> None:
        text = self.paths_input.value or ""
        self.scan_status.value = self.t("gui.scanning")
        self.scan_status.update()
        count, _warnings = await asyncio.to_thread(self.vm.scan_inputs, text)
        self.scan_status.value = self.t("gui.scan_found", n=count) if text.strip() else ""
        self._rebuild_rows()
        self.page.update()

    def _append_paths(self, paths: list[str]) -> None:
        existing = self.paths_input.value or ""
        lines = [line for line in existing.splitlines() if line.strip()]
        for p in paths:
            if p not in lines:
                lines.append(p)
        self.paths_input.value = "\n".join(lines)
        self.paths_input.update()
        self.page.run_task(self._scan_now)

    async def _pick_files(self) -> None:
        files = await self.file_picker.pick_files(allow_multiple=True)
        if files:
            paths = [f.path for f in files if f.path]
            if paths:
                self._append_paths(paths)

    def _on_pick_files(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._pick_files)

    def _on_pick_folder(self, _e: ft.ControlEvent) -> None:
        async def pick() -> None:
            path = None
            try:
                path = await self.file_picker.get_directory_path()
            except Exception:
                path = None
            if not path and sys.platform == "darwin":
                path = await asyncio.to_thread(choose_folder_via_osascript)
            if path:
                self._append_paths([path])

        self.page.run_task(pick)

    def _on_clear(self, _e: ft.ControlEvent) -> None:
        if self.vm.running:
            return
        self.paths_input.value = ""
        self.scan_status.value = ""
        self.vm.clear_files()
        self._rebuild_rows()
        self.page.update()

    # ------------------------------------------------------------------ #
    # File rows: incremental updates only
    # ------------------------------------------------------------------ #

    def _rebuild_rows(self) -> None:
        self.row_controls = {
            row_id: RowControl(self, self.vm.rows[row_id])
            for row_id in self.vm.row_order
        }
        self.file_list.controls = [rc.container for rc in self.row_controls.values()]

    def retry_row(self, row_id: int) -> None:
        if self.vm.start_batch(only_ids=[row_id]):
            self._sync_run_buttons(running=True)
            self.page.update()

    def reveal_row(self, row_id: int) -> None:
        row = self.vm.rows.get(row_id)
        if row is not None:
            reveal_in_file_manager(row.path)

    # ------------------------------------------------------------------ #
    # Batch control + ticker
    # ------------------------------------------------------------------ #

    def _on_start(self, _e: ft.ControlEvent) -> None:
        if not self.vm.start_batch():
            self._toast(self.t("gui.toast_no_files"), ok=False)
            return
        self._sync_run_buttons(running=True)
        self.page.update()

    def _on_stop(self, _e: ft.ControlEvent) -> None:
        self.vm.request_stop()
        self.stop_btn.disabled = True
        self.bar_text.value = self.t("gui.stopping")
        self.page.update()

    def _sync_run_buttons(self, *, running: bool) -> None:
        self.start_btn.visible = not running
        self.stop_btn.visible = running
        self.stop_btn.disabled = False
        self.paths_input.disabled = running

    async def _ticker(self) -> None:
        """4 Hz drain loop — the only place UI state is refreshed."""
        while True:
            await asyncio.sleep(TICK_SEC)
            try:
                result = self.vm.drain()
            except Exception:
                continue
            dirty = False
            for row_id in result.changed_rows:
                control = self.row_controls.get(row_id)
                row = self.vm.rows.get(row_id)
                if control is not None and row is not None:
                    control.apply(self, row)
                    dirty = True

            snap = result.snapshot
            key = (snap.running, snap.done, snap.total, snap.current_name,
                   snap.current_status, int(snap.eta_sec or 0))
            if key != self._last_snapshot:
                self._last_snapshot = key
                self.bar_progress.value = (snap.done / snap.total) if snap.total else 0
                if snap.running:
                    parts = [self.t("gui.progress", done=snap.done, total=snap.total)]
                    if snap.current_name:
                        parts.append(
                            f"{snap.current_name} · {self.t('status.' + snap.current_status)}"
                        )
                    self.bar_text.value = "  ".join(parts)
                    self.bar_detail.value = (
                        self.t("gui.eta", min=max(1, round(snap.eta_sec / 60)))
                        if snap.eta_sec else ""
                    )
                else:
                    self.bar_text.value = self.t("gui.idle")
                    self.bar_detail.value = ""
                dirty = True

            if len(result.log_lines) != self._last_log_len:
                self._last_log_len = len(result.log_lines)
                needle = self.log_filter.strip().casefold()
                lines = [
                    line for line in result.log_lines
                    if not needle or needle in line.casefold()
                ]
                self.log_list.controls = [
                    ft.Text(line, size=11, font_family="monospace", selectable=True)
                    for line in lines[-200:]
                ]
                dirty = True

            if result.finished:
                self._sync_run_buttons(running=False)
                failed = len(self.vm.failed_rows())
                if self.vm.stop_requested:
                    self._toast(self.t("gui.toast_stopped"), ok=False)
                elif failed:
                    self._toast(self.t("gui.toast_failed", n=failed), ok=False)
                else:
                    self._toast(self.t("gui.toast_done"))
                dirty = True

            if dirty:
                self.page.update()

    # ------------------------------------------------------------------ #
    # History tab (R5)
    # ------------------------------------------------------------------ #

    def _build_history_tab(self) -> None:
        t = self.t
        self.history_list = ft.ListView(spacing=6, expand=True)
        self.history_tab = ft.Container(
            padding=ft.Padding(16, 12, 16, 12),
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(controls=[
                        ft.OutlinedButton(t("gui.history_refresh"), icon=ft.Icons.REFRESH,
                                          on_click=lambda _e: self._refresh_history()),
                        ft.OutlinedButton(t("gui.history_clean"), icon=ft.Icons.DELETE_SWEEP,
                                          on_click=self._on_history_clean),
                    ]),
                    self.history_list,
                ],
            ),
        )

    def _lang_label(self, code: str) -> str:
        label = self.t(f"tlang_{code}")
        return code if label == f"tlang_{code}" else label

    def _refresh_history(self) -> None:
        t = self.t
        controls: list[ft.Control] = []
        for group in self.vm.history_groups():
            langs = " · ".join(self._lang_label(code) for code in group.existing)
            subtitle = f"{langs or '—'} · {group.latest_at[:16].replace('T', ' ')}"
            if group.deleted:
                trailing: list[ft.Control] = [
                    ft.Text(t("gui.history_deleted"), size=12,
                            color=ft.Colors.RED_400, italic=True),
                ]
            else:
                first = next(iter(group.existing.values()))
                trailing = [
                    ft.TextButton(t("gui.history_view"),
                                  on_click=lambda _e, g=group: self._open_history_viewer(g)),
                    ft.IconButton(ft.Icons.FOLDER_OPEN, icon_size=16, tooltip=t("gui.reveal"),
                                  on_click=lambda _e, p=first: reveal_in_file_manager(Path(p))),
                ]
            controls.append(
                ft.Container(
                    padding=ft.Padding(12, 8, 12, 8),
                    border_radius=8,
                    border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(
                                spacing=2, expand=True,
                                controls=[
                                    ft.Text(group.name, size=13, no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            tooltip=group.source),
                                    ft.Text(subtitle, size=11,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                ],
                            ),
                            *trailing,
                        ],
                    ),
                )
            )
        if not controls:
            controls = [ft.Container(
                padding=40, alignment=ft.Alignment.CENTER,
                content=ft.Text(t("gui.history_empty"), size=13,
                                color=ft.Colors.ON_SURFACE_VARIANT),
            )]
        self.history_list.controls = controls
        self.page.update()

    def _on_history_clean(self, _e: ft.ControlEvent) -> None:
        removed = self.vm.history_clean_missing()
        self._toast(self.t("gui.history_cleaned", n=removed))
        self._refresh_history()

    def _open_history_viewer(self, group) -> None:
        """One dialog per source file: switch languages, translate missing ones."""
        t = self.t
        state = {"lang": next(iter(group.existing), None), "busy": False}
        body = ft.Text("", size=12, font_family="monospace", selectable=True)
        status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        lang_row = ft.Row(spacing=6, wrap=True)

        def load(lang: str) -> None:
            state["lang"] = lang
            try:
                body.value = self.vm.read_preview(group.existing[lang])
            except Exception as exc:
                body.value = str(exc)
            rebuild()
            self.page.update()

        def start_translate(lang: str) -> None:
            if state["busy"]:
                return
            state["busy"] = True
            status.value = t("gui.history_translating", lang=self._lang_label(lang))
            rebuild()
            self.page.update()

            def job() -> None:
                try:
                    produced = self.vm.translate_history(group, lang)
                    if produced:
                        status.value = t("gui.history_translate_done")
                        state["lang"] = lang
                    else:
                        status.value = t("gui.models_failed", reason="no output")
                except Exception as exc:
                    status.value = t("gui.models_failed", reason=exc)
                finally:
                    state["busy"] = False
                    if state["lang"] in group.existing:
                        try:
                            body.value = self.vm.read_preview(group.existing[state["lang"]])
                        except Exception:
                            pass
                    rebuild()
                    self._refresh_history()
                    self.page.update()

            self.page.run_thread(job)

        def rebuild() -> None:
            buttons: list[ft.Control] = []
            for code in group.existing:
                label = self._lang_label(code)
                if code == state["lang"]:
                    buttons.append(ft.FilledButton(
                        label, disabled=state["busy"],
                        on_click=lambda _e, c=code: load(c),
                    ))
                else:
                    buttons.append(ft.OutlinedButton(
                        label, disabled=state["busy"],
                        on_click=lambda _e, c=code: load(c),
                    ))
            for code in group.missing:
                buttons.append(ft.TextButton(
                    t("gui.history_translate_to", lang=self._lang_label(code)),
                    icon=ft.Icons.TRANSLATE, disabled=state["busy"],
                    on_click=lambda _e, c=code: start_translate(c),
                ))
            lang_row.controls = buttons

        rebuild()
        if state["lang"]:
            try:
                body.value = self.vm.read_preview(group.existing[state["lang"]])
            except Exception as exc:
                body.value = str(exc)

        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(group.name, size=15),
            content=ft.Container(
                width=640, height=460,
                content=ft.Column(
                    spacing=8,
                    controls=[
                        lang_row,
                        status,
                        ft.Container(
                            expand=True,
                            content=ft.Column(
                                scroll=ft.ScrollMode.AUTO,
                                controls=[body],
                            ),
                        ),
                    ],
                ),
            ),
            actions=[ft.TextButton(self.t("gui.close"),
                                   on_click=lambda _e: self.page.pop_dialog())],
        ))
        self.page.update()

    # ------------------------------------------------------------------ #
    # Settings tab + wizard + model manager
    # ------------------------------------------------------------------ #

    def _dd(self, label: str, key: str, options: list[tuple[str, str]],
            width: int = 260, on_saved=None) -> ft.Dropdown:
        config = self.vm.get_config()

        def save(e: ft.ControlEvent) -> None:
            self._save_setting(key, e.control.value)
            if on_saved is not None:
                on_saved(e.control.value)

        return ft.Dropdown(
            label=label, width=width, border_radius=8,
            value=str(config.get(key, "")),
            options=[ft.dropdown.Option(v, text) for v, text in options],
            on_select=save,
        )

    def _build_settings_tab(self) -> None:
        t = self.t
        config = self.vm.get_config()

        language_dd = self._dd(
            t("gui.settings_language"), "language",
            [("en", "English"), ("zh", "中文")],
            on_saved=lambda _v: self.remount(),
        )
        theme_dd = self._dd(
            t("gui.settings_theme"), "theme",
            [("system", t("gui.theme_system")), ("light", t("gui.theme_light")),
             ("dark", t("gui.theme_dark"))],
            on_saved=self._apply_theme,
        )
        model_dd = self._dd(
            t("gui.settings_model"), "whisper_model",
            [(k, f"{label} ({size})") for k, label, size, _inst in self.vm.whisper_model_rows()],
            width=380,
        )
        format_dd = self._dd(
            t("gui.settings_format"), "output_format",
            [(f, f) for f in ("srt", "txt", "vtt", "json")], width=150,
        )
        tlang_dd = self._dd(
            t("gui.settings_tlang"), "transcribe_language",
            [("auto", t("tlang_auto")), ("en", t("tlang_en")), ("zh", t("tlang_zh"))],
            width=200,
        )
        memory_dd = self._dd(
            t("gui.settings_memory"), "memory_mode",
            [("balanced", t("gui.memory_balanced")), ("low", t("gui.memory_low"))],
            width=320,
        )
        ollama_dd = self._dd(
            t("gui.settings_ollama_model"), "ollama_model",
            [(m, m) for m in self._ollama_options(config)], width=260,
        )
        recursive_sw = ft.Switch(
            label=t("gui.settings_recursive"), value=bool(config["recursive_scan"]),
            on_change=lambda e: self._save_setting("recursive_scan", bool(e.control.value)),
        )
        overwrite_sw = ft.Switch(
            label=t("gui.settings_overwrite"), value=bool(config["overwrite"]),
            on_change=lambda e: self._save_setting("overwrite", bool(e.control.value)),
        )
        export_tf = ft.TextField(
            label=t("gui.settings_export"), value=str(config.get("export_dir") or ""),
            width=420, border_radius=8, text_size=13,
            on_blur=lambda e: self._save_setting(
                "export_dir", e.control.value.strip() or None
            ),
        )

        self.settings_tab = ft.Container(
            padding=ft.Padding(16, 12, 16, 12),
            content=ft.Column(
                spacing=14, scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Row(spacing=12, controls=[language_dd, theme_dd]),
                    ft.Row(spacing=12, controls=[model_dd, ft.OutlinedButton(
                        t("gui.manage_models"), icon=ft.Icons.CLOUD_DOWNLOAD,
                        on_click=self._open_model_manager,
                    ), ft.OutlinedButton(
                        t("gui.doctor_run"), icon=ft.Icons.HEALTH_AND_SAFETY_OUTLINED,
                        on_click=self._on_run_doctor,
                    ), ft.OutlinedButton(
                        t("gui.update_check"), icon=ft.Icons.SYSTEM_UPDATE_ALT,
                        on_click=self._on_check_update,
                    )]),
                    ft.Row(spacing=12, controls=[format_dd, tlang_dd, memory_dd]),
                    ft.Row(spacing=12, controls=[ollama_dd]),
                    ft.Row(spacing=24, controls=[recursive_sw, overwrite_sw]),
                    export_tf,
                ],
            ),
        )

    def _ollama_options(self, config: dict) -> list[str]:
        preset = [config.get("ollama_model", "qwen3:8b")]
        return sorted(set(preset + ["qwen3:4b", "qwen3:8b", "qwen3:14b"]))

    def _apply_theme(self, value: str) -> None:
        self.page.theme_mode = {
            "light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK,
        }.get(value, ft.ThemeMode.SYSTEM)
        self.page.update()

    def _save_setting(self, key: str, value) -> None:
        self.vm.update_settings(**{key: value})

    def _show_wizard(self) -> None:
        t = self.t
        lang_dd = ft.Dropdown(
            label=t("gui.settings_language"), width=260, border_radius=8, value="zh",
            options=[ft.dropdown.Option("en", "English"), ft.dropdown.Option("zh", "中文")],
        )
        model_dd = ft.Dropdown(
            label=t("gui.settings_model"), width=380, border_radius=8,
            value=self.vm.get_config()["whisper_model"],
            options=[
                ft.dropdown.Option(k, f"{label} ({size})")
                for k, label, size, _inst in self.vm.whisper_model_rows()
            ],
        )

        def save(_e: ft.ControlEvent) -> None:
            self.vm.update_settings(
                language=lang_dd.value or "zh",
                whisper_model=model_dd.value or "large-v3-turbo",
            )
            self.page.pop_dialog()
            self.remount()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(t("gui.wizard_title")),
            content=ft.Column(
                tight=True, spacing=12,
                controls=[ft.Text(t("gui.wizard_body"), size=13), lang_dd, model_dd],
            ),
            actions=[ft.FilledButton(t("gui.wizard_save"), on_click=save)],
        ))
        self.page.update()

    # Model manager ------------------------------------------------------ #

    def _open_model_manager(self, _e: ft.ControlEvent) -> None:
        t = self.t
        self.mm_status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.mm_progress = ft.ProgressBar(value=None, visible=False)
        self.mm_list = ft.Column(spacing=4, tight=True)
        self._mm_busy = False
        self._refresh_model_manager()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(t("gui.models_title")),
            content=ft.Container(
                width=560, height=460,
                content=ft.Column(
                    scroll=ft.ScrollMode.AUTO,
                    controls=[self.mm_progress, self.mm_status, self.mm_list],
                ),
            ),
            actions=[ft.TextButton(t("gui.close"),
                                   on_click=lambda _e: self.page.pop_dialog())],
        ))
        self.page.update()

    def _refresh_model_manager(self) -> None:
        t = self.t
        rows: list[ft.Control] = [ft.Text(t("gui.models_whisper"), weight=ft.FontWeight.W_600)]
        for key, label, size, installed in self.vm.whisper_model_rows():
            rows.append(self._mm_row(
                f"{label} · {size}",
                installed,
                on_download=lambda k=key: self._mm_run(self._download_whisper, k),
                on_delete=lambda k=key: self._mm_run(self._delete_whisper, k),
            ))
        rows.append(ft.Divider())
        rows.append(ft.Text(t("gui.models_ollama"), weight=ft.FontWeight.W_600))
        client = self.vm.ollama_client()
        if client.is_reachable():
            installed_models = set(client.list_models())
            for name in sorted(installed_models | set(self._ollama_options(self.vm.get_config()))):
                rows.append(self._mm_row(
                    name,
                    name in installed_models,
                    on_download=lambda n=name: self._mm_run(self._pull_ollama, n),
                    on_delete=lambda n=name: self._mm_run(self._delete_ollama, n),
                ))
        else:
            rows.append(ft.Text(t("gui.models_ollama_down"), size=12,
                                color=ft.Colors.ORANGE_700))
        self.mm_list.controls = rows

    def _mm_row(self, label: str, installed: bool, *, on_download, on_delete) -> ft.Row:
        t = self.t
        action = (
            ft.TextButton(t("gui.models_delete"), disabled=self._mm_busy,
                          on_click=lambda _e: on_delete())
            if installed else
            ft.TextButton(t("gui.models_download"), disabled=self._mm_busy,
                          on_click=lambda _e: on_download())
        )
        badge = ft.Text(
            t("gui.models_installed") if installed else t("gui.models_not_installed"),
            size=11,
            color=ft.Colors.GREEN_600 if installed else ft.Colors.ON_SURFACE_VARIANT,
        )
        return ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[ft.Text(label, size=13, expand=True), badge, action],
        )

    def _mm_run(self, fn, arg: str) -> None:
        if self._mm_busy:
            return
        self._mm_busy = True
        self.mm_progress.visible = True
        self._refresh_model_manager()
        self.page.update()

        def job() -> None:
            try:
                fn(arg)
                self.mm_status.value = self.t("gui.models_done", name=arg)
            except Exception as exc:
                self.mm_status.value = self.t("gui.models_failed", reason=exc)
            finally:
                self._mm_busy = False
                self.mm_progress.visible = False
                self._refresh_model_manager()
                self.page.update()

        self.page.run_thread(job)

    def _mm_progress_cb(self, name: str):
        def cb(done, total=None) -> None:
            if isinstance(done, str):  # ollama pull: (detail, fraction)
                detail = done
                self.mm_status.value = self.t("gui.models_working", name=name, detail=detail)
            else:
                self.mm_status.value = self.t(
                    "gui.models_working", name=name, detail=f"{done}/{total}"
                )
            self.page.update()
        return cb

    def _download_whisper(self, key: str) -> None:
        from ..engines.models import download_model, get_spec
        from ..engines.select import resolve_engine_name

        engine_name, _ = resolve_engine_name(self.vm.get_config()["engine"])
        cb = self._mm_progress_cb(key)
        download_model(get_spec(key), engine_name, progress=lambda d, t_: cb(d, t_))

    def _delete_whisper(self, key: str) -> None:
        from ..engines.models import delete_model, get_spec
        from ..engines.select import resolve_engine_name

        engine_name, _ = resolve_engine_name(self.vm.get_config()["engine"])
        delete_model(get_spec(key), engine_name)

    def _pull_ollama(self, name: str) -> None:
        cb = self._mm_progress_cb(name)
        self.vm.ollama_client().pull(name, progress=lambda detail, _frac: cb(detail))

    def _delete_ollama(self, name: str) -> None:
        self.vm.ollama_client().delete(name)

    # ------------------------------------------------------------------ #
    # Doctor (M6): startup check + on-demand from Settings
    # ------------------------------------------------------------------ #

    def _startup_doctor(self) -> None:
        from ..core.doctor import doctor_ok, run_doctor

        try:
            results = run_doctor(self.vm.get_config())
        except Exception:
            return
        if not doctor_ok(results):
            self._toast(self.t("gui.doctor_startup_failed"), ok=False)

    def _on_run_doctor(self, _e: ft.ControlEvent) -> None:
        rows = ft.Column(spacing=6, tight=True, controls=[
            ft.ProgressRing(width=18, height=18, stroke_width=2),
        ])
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(self.t("gui.doctor_title"), size=15),
            content=ft.Container(width=520, content=rows),
            actions=[ft.TextButton(self.t("gui.close"),
                                   on_click=lambda _e: self.page.pop_dialog())],
        ))
        self.page.update()

        def job() -> None:
            from ..core.doctor import run_doctor

            results = run_doctor(self.vm.get_config())
            controls: list[ft.Control] = []
            for result in results:
                if result.ok:
                    icon, color = ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN_600
                elif result.required:
                    icon, color = ft.Icons.CANCEL, ft.Colors.RED_600
                else:
                    icon, color = ft.Icons.WARNING_AMBER, ft.Colors.ORANGE_600
                name = self.t(f"doctor.{result.key}", detail=result.detail)
                lines = [ft.Row(spacing=8, controls=[
                    ft.Icon(icon, size=16, color=color),
                    ft.Text(name, size=13, expand=True),
                    ft.Text(result.detail if result.ok else "", size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, width=180),
                ])]
                if not result.ok and result.hint:
                    lines.append(ft.Text(
                        result.hint, size=11, font_family="monospace",
                        color=ft.Colors.ON_SURFACE_VARIANT, selectable=True,
                    ))
                controls.append(ft.Column(spacing=2, tight=True, controls=lines))
            rows.controls = controls
            self.page.update()

        self.page.run_thread(job)

    # ------------------------------------------------------------------ #
    # In-app update (Settings → Check updates)
    # ------------------------------------------------------------------ #

    def _on_check_update(self, _e: ft.ControlEvent) -> None:
        from ..core import update as up

        if self.vm.running:
            self._toast(self.t("gui.update_running"), ok=False)
            return
        root = up.repo_root()
        if root is None:
            self._toast(self.t("gui.update_not_checkout"), ok=False)
            return

        body = ft.Column(spacing=8, tight=True, controls=[])
        dialog = ft.AlertDialog(
            title=ft.Text(self.t("gui.update_title"), size=15),
            content=ft.Container(width=440, content=body),
            actions=[ft.TextButton(self.t("gui.close"),
                                   on_click=lambda _e: self.page.pop_dialog())],
        )

        def show(*controls: ft.Control, actions: list[ft.Control] | None = None) -> None:
            body.controls = list(controls)
            if actions is not None:
                dialog.actions = actions
            self.page.update()

        def progress(label: str) -> ft.Row:
            return ft.Row(spacing=8, controls=[
                ft.ProgressRing(width=18, height=18, stroke_width=2),
                ft.Text(label, size=13),
            ])

        def do_update(_e: ft.ControlEvent) -> None:
            show(progress(self.t("gui.update_pulling")), actions=[])

            def job() -> None:
                ok, detail = up.pull(root)
                if not ok:
                    show(
                        ft.Text(self.t("gui.update_failed", detail=detail),
                                size=13, selectable=True),
                        actions=[ft.TextButton(self.t("gui.close"),
                                               on_click=lambda _e: self.page.pop_dialog())],
                    )
                    return
                # New instance boots with the pulled code; this one closes.
                up.spawn_restart(root)
                self.page.window.destroy()

            self.page.run_thread(job)

        self.page.show_dialog(dialog)
        show(progress(self.t("gui.update_checking")))

        def job() -> None:
            status = up.check(root)
            if not status.ok:
                show(ft.Text(self.t("gui.update_check_failed", detail=status.detail),
                             size=13, selectable=True))
                return
            if status.behind == 0:
                show(ft.Text(self.t("gui.update_uptodate"), size=13))
                return
            if status.dirty:
                show(ft.Text(self.t("gui.update_dirty", count=status.behind),
                             size=13, selectable=True))
                return
            show(
                ft.Text(self.t("gui.update_behind", count=status.behind), size=13),
                ft.Text(self.t("gui.update_restart_note"), size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                actions=[
                    ft.TextButton(self.t("gui.close"),
                                  on_click=lambda _e: self.page.pop_dialog()),
                    ft.FilledButton(self.t("gui.update_now"), on_click=do_update),
                ],
            )

        self.page.run_thread(job)

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #

    def _on_tab_change(self, e: ft.ControlEvent) -> None:
        if e.control.selected_index == 1:
            self._refresh_history()

    def _on_log_filter(self, e: ft.ControlEvent) -> None:
        self.log_filter = e.control.value or ""
        self._last_log_len = -1  # force refresh on next tick

    def _on_log_copy(self, _e: ft.ControlEvent) -> None:
        self.page.set_clipboard("\n".join(self.vm.log_lines))
        self._toast(self.t("gui.toast_copied"))

    def _on_resized(self, _e) -> None:
        try:
            self.vm.update_settings(
                gui_window_width=int(self.page.window.width or 1000),
                gui_window_height=int(self.page.window.height or 760),
            )
        except Exception:
            pass

    def _toast(self, message: str, ok: bool = True) -> None:
        self.page.show_dialog(ft.SnackBar(
            content=ft.Text(message),
            bgcolor=ft.Colors.GREEN_700 if ok else ft.Colors.RED_700,
        ))


def gui_main(page: ft.Page) -> None:
    GuiApp(page, GuiViewModel()).mount()
