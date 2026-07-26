"""GUI state and logic — no UI-toolkit imports allowed in this module.

The view layer calls into this from background threads (scan, batch run,
model downloads) and drains UI-relevant changes on a throttled timer via
:meth:`GuiViewModel.drain`, which returns only what changed — the my-transcriptor
full-rebuild-per-event mistake is structurally impossible here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core import scanner
from ..core.config import ConfigService
from ..core.events import BatchEvent, BufferedSubscriber, EventBus, ProgressEvent, StatusEvent
from ..core.history import HistoryEntry, HistoryStore
from ..core.jobs import BatchStats, JobStatus
from ..core.pipeline import Pipeline, PipelineSettings
from ..engines.models import PRESETS, get_spec, installed_keys
from ..engines.select import create_engine, resolve_engine_name
from ..translate.ollama import OllamaClient
from ..translate.stage import OllamaTranslateStage

LOG_LIMIT = 500


@dataclass
class FileRow:
    id: int
    path: Path
    status: str = JobStatus.PENDING.value
    error: str = ""
    progress: float = 0.0          # 0..1 within the active stage
    stage: str = ""                # "transcribe" | "translate" | ""

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class Snapshot:
    """What the bottom bar needs, computed per drain."""
    running: bool = False
    done: int = 0
    total: int = 0
    current_name: str = ""
    current_status: str = ""
    eta_sec: float | None = None
    final_stats: BatchStats | None = None


@dataclass
class HistoryGroup:
    """All history knowledge about one source file."""
    source: str
    name: str
    latest_at: str
    model: str
    existing: dict[str, str] = field(default_factory=dict)   # lang -> newest existing path
    missing: list[str] = field(default_factory=list)         # registry langs not produced yet
    translate_from: str | None = None                        # an existing .srt to translate from

    @property
    def deleted(self) -> bool:
        return not self.existing


@dataclass
class DrainResult:
    changed_rows: list[int] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    snapshot: Snapshot = field(default_factory=Snapshot)
    finished: bool = False


class GuiViewModel:
    def __init__(
        self,
        config_service: ConfigService | None = None,
        history: HistoryStore | None = None,
    ):
        self.config = config_service or ConfigService()
        self.history = history or HistoryStore()
        self.bus = EventBus()
        self._buffer = BufferedSubscriber(self.bus, capacity=4000)
        self._lock = threading.Lock()

        self.rows: dict[int, FileRow] = {}
        self.row_order: list[int] = []
        self.running = False
        self.stop_requested = False
        self._stop = threading.Event()
        self._final_stats: BatchStats | None = None
        self._finished_flag = False

        self.log_lines: list[str] = []

        # ETA bookkeeping: wall time of completed files this batch
        self._durations: list[float] = []
        self._active_since: float | None = None

    # ------------------------------------------------------------------ #
    # Input / scanning (call from a background thread; never the UI thread)
    # ------------------------------------------------------------------ #

    def scan_inputs(self, text: str) -> tuple[int, list[str]]:
        """Scan pasted lines into rows; returns (count, warning-texts as key:value)."""
        raw = [line for line in text.splitlines() if line.strip()]
        config = self.config.load()
        result = scanner.scan(raw, recursive=bool(config["recursive_scan"]))
        with self._lock:
            if self.running:
                return len(self.row_order), []
            self.rows = {
                i: FileRow(id=i, path=p) for i, p in enumerate(result.files, start=1)
            }
            self.row_order = list(self.rows.keys())
        return len(result.files), result.warnings

    def clear_files(self) -> None:
        with self._lock:
            if self.running:
                return
            self.rows = {}
            self.row_order = []

    # ------------------------------------------------------------------ #
    # Batch control
    # ------------------------------------------------------------------ #

    def start_batch(
        self,
        *,
        only_ids: list[int] | None = None,
        overwrite: bool | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        """Run the pipeline on a worker thread; False when nothing to do."""
        with self._lock:
            if self.running:
                return False
            ids = only_ids if only_ids is not None else list(self.row_order)
            files = [self.rows[i].path for i in ids if i in self.rows]
            if not files:
                return False
            for i in ids:
                row = self.rows.get(i)
                if row is not None:
                    row.status = JobStatus.PENDING.value
                    row.error = ""
                    row.progress = 0.0
            self.running = True
            self.stop_requested = False
            self._stop.clear()
            self._final_stats = None
            self._finished_flag = False
            self._durations = []
            self._active_since = None
            self._id_map = {n: ids[n - 1] for n in range(1, len(ids) + 1)}

        config = self.config.load()
        pipeline = self._build_pipeline(config, overwrite=overwrite)

        def work() -> None:
            try:
                pipeline.run(files, self._stop)
            finally:
                with self._lock:
                    self.running = False
                    self._finished_flag = True
                if on_finished is not None:
                    on_finished()

        threading.Thread(target=work, name="scripto-gui-batch", daemon=True).start()
        return True

    def request_stop(self) -> None:
        self.stop_requested = True
        self._stop.set()

    def _build_pipeline(self, config: dict, *, overwrite: bool | None) -> Pipeline:
        translate_stage = None
        if config["translate_enabled"] and config["output_format"] == "srt":
            client = OllamaClient(config["ollama_url"])
            if client.is_reachable():
                translate_stage = OllamaTranslateStage(
                    client,
                    model=config["ollama_model"],
                    target=config["translate_target"],
                    overwrite=bool(config["overwrite"] if overwrite is None else overwrite),
                    batch_blocks=int(config["translate_batch_blocks"]),
                    batch_max_chars=int(config["translate_batch_max_chars"]),
                )
            else:
                self.bus.emit(StatusEvent(subject="ollama", status="unreachable"))

        engine_name, _ = resolve_engine_name(config["engine"])
        language = config["transcribe_language"]
        settings = PipelineSettings(
            model=get_spec(config["whisper_model"]),
            fmt=config["output_format"],
            language=None if language == "auto" else language,
            overwrite=bool(config["overwrite"] if overwrite is None else overwrite),
            export_dir=Path(config["export_dir"]).expanduser() if config["export_dir"] else None,
            suffix_map=dict(config["lang_suffixes"]),
            memory_mode=config["memory_mode"],
            engine_label=engine_name,
            segment_threshold_sec=float(config.get("segment_threshold_sec", 3600)),
            segment_chunk_sec=float(config.get("segment_chunk_sec", 1800)),
        )
        return Pipeline(
            engine=create_engine(config["engine"]),
            bus=self.bus,
            history=self.history,
            settings=settings,
            translate_stage=translate_stage,
        )

    # ------------------------------------------------------------------ #
    # Event draining (UI timer calls this; returns only deltas)
    # ------------------------------------------------------------------ #

    def drain(self) -> DrainResult:
        result = DrainResult()
        changed: set[int] = set()
        for event in self._buffer.drain():
            if isinstance(event, StatusEvent) and event.subject.startswith("job:"):
                row = self._row_for_job(int(event.subject.split(":", 1)[1]))
                if row is None:
                    continue
                self._track_eta(row, event.status)
                row.status = event.status
                row.error = event.detail
                if event.status in (
                    JobStatus.DONE.value, JobStatus.SKIPPED.value, JobStatus.FAILED.value
                ):
                    row.progress = 0.0
                    row.stage = ""
                changed.add(row.id)
                self._log(f"{row.name}: {event.status}"
                          + (f" — {event.detail}" if event.detail else ""))
            elif isinstance(event, ProgressEvent):
                scope, _, job = event.scope.partition(":")
                row = self._row_for_job(int(job)) if job.isdigit() else None
                if row is not None and event.total > 0:
                    row.progress = min(1.0, event.done / event.total)
                    row.stage = scope
                    changed.add(row.id)
            elif isinstance(event, BatchEvent) and event.kind == "finished":
                self._log(f"batch finished: {event.stats}")
            elif isinstance(event, StatusEvent) and event.subject == "ollama":
                self._log("ollama unreachable — translation disabled for this run")

        result.changed_rows = sorted(changed)
        result.log_lines = list(self.log_lines)
        result.snapshot = self._snapshot()
        with self._lock:
            result.finished = self._finished_flag
            if self._finished_flag:
                self._finished_flag = False
        return result

    def _row_for_job(self, job_id: int) -> FileRow | None:
        mapped = getattr(self, "_id_map", {}).get(job_id, job_id)
        return self.rows.get(mapped)

    def _track_eta(self, row: FileRow, new_status: str) -> None:
        now = time.monotonic()
        if new_status == JobStatus.TRANSCRIBING.value:
            self._active_since = now
        elif new_status == JobStatus.DONE.value and self._active_since is not None:
            self._durations.append(now - self._active_since)
            self._active_since = None

    def _snapshot(self) -> Snapshot:
        rows = list(self.rows.values())
        terminal = {
            JobStatus.DONE.value, JobStatus.SKIPPED.value,
            JobStatus.FAILED.value, JobStatus.UNPROCESSED.value,
        }
        done = sum(1 for r in rows if r.status in terminal)
        active = next(
            (r for r in rows if r.status in (
                JobStatus.TRANSCRIBING.value, JobStatus.EXTRACTING.value,
                JobStatus.TRANSLATING.value,
            )),
            None,
        )
        eta = None
        if self.running and self._durations:
            remaining = len(rows) - done
            eta = sum(self._durations) / len(self._durations) * remaining
        return Snapshot(
            running=self.running,
            done=done,
            total=len(rows),
            current_name=active.name if active else "",
            current_status=active.status if active else "",
            eta_sec=eta,
            final_stats=self._final_stats,
        )

    def _log(self, line: str) -> None:
        self.log_lines.append(time.strftime("%H:%M:%S ") + line)
        if len(self.log_lines) > LOG_LIMIT:
            del self.log_lines[: len(self.log_lines) - LOG_LIMIT]

    def failed_rows(self) -> list[FileRow]:
        return [r for r in self.rows.values() if r.status == JobStatus.FAILED.value]

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #

    def get_config(self) -> dict:
        return self.config.load()

    def update_settings(self, **changes) -> dict:
        return self.config.update(**changes)

    def is_first_run(self) -> bool:
        return not (self.config.load().get("language") or "").strip()

    # ------------------------------------------------------------------ #
    # History (R5): grouped by source file — one entry per file, languages
    # switchable inside; a missing language can be translated in place.
    # ------------------------------------------------------------------ #

    def history_rows(self) -> list[tuple[HistoryEntry, bool]]:
        """(entry, all outputs still on disk) — newest first."""
        rows = []
        for entry in self.history.entries():
            paths = [o.get("path", "") for o in entry.outputs]
            exists = bool(paths) and all(Path(p).exists() for p in paths)
            rows.append((entry, exists))
        return rows

    def history_groups(self) -> list[HistoryGroup]:
        """One group per source file, newest first; newest path wins per language."""
        from ..core.languages import known_languages

        groups: dict[str, HistoryGroup] = {}
        for entry in self.history.entries():  # newest first
            group = groups.get(entry.source)
            if group is None:
                group = HistoryGroup(
                    source=entry.source,
                    name=Path(entry.source).name,
                    latest_at=entry.created_at,
                    model=entry.model,
                )
                groups[entry.source] = group
            for output in entry.outputs:
                lang = output.get("lang") or ""
                path = output.get("path") or ""
                if lang and path and lang not in group.existing and Path(path).exists():
                    group.existing[lang] = path

        for group in groups.values():
            group.translate_from = next(
                (p for p in group.existing.values() if p.endswith(".srt")), None
            )
            if group.translate_from:
                group.missing = [
                    spec.code for spec in known_languages()
                    if spec.code not in group.existing
                ]
        return list(groups.values())

    def translate_history(self, group: "HistoryGroup", target: str) -> list[Path]:
        """Translate an existing history output into ``target`` (blocking —
        the view runs this on a worker thread); records a history entry."""
        if not group.translate_from:
            return []
        config = self.get_config()
        client = OllamaClient(config["ollama_url"])
        stage = OllamaTranslateStage(
            client,
            model=config["ollama_model"],
            target=target,
            overwrite=False,
            batch_blocks=int(config["translate_batch_blocks"]),
            batch_max_chars=int(config["translate_batch_max_chars"]),
        )
        try:
            produced = stage.translate(
                Path(group.translate_from), Path(group.source),
                stop_check=None, progress=None,
            )
        finally:
            stage.release()
        if produced:
            self.history.append(HistoryEntry(
                source=group.source,
                outputs=[
                    {"lang": target, "format": p.suffix.lstrip("."), "path": str(p)}
                    for p in produced
                ],
                model=stage.label,
                engine="translate",
                status="done",
            ))
        return produced

    def history_clean_missing(self) -> int:
        stale = {e.id for e, exists in self.history_rows() if not exists}
        return self.history.remove(stale) if stale else 0

    def history_delete_sources(self, sources: set[str]) -> int:
        """Remove every history entry for the given source files.

        Records only — output files on disk are never touched."""
        ids = {e.id for e in self.history.entries() if e.source in sources}
        return self.history.remove(ids) if ids else 0

    @staticmethod
    def read_preview(path: str, limit: int = 20_000) -> str:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return text[:limit] + ("\n…" if len(text) > limit else "")

    # ------------------------------------------------------------------ #
    # Model management (called from background threads by the view)
    # ------------------------------------------------------------------ #

    def whisper_model_rows(self) -> list[tuple[str, str, str, bool]]:
        """(key, label, size_hint, installed) for the active engine."""
        engine_name, _ = resolve_engine_name(self.get_config()["engine"])
        installed = installed_keys(engine_name)
        return [(s.key, s.label, s.size_hint, s.key in installed) for s in PRESETS]

    def ollama_client(self) -> OllamaClient:
        return OllamaClient(self.get_config()["ollama_url"])
