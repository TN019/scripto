"""Staged pipeline (R6, docs/PLAN.md §5).

    scan ─► extract queue (bounded=2) ─► transcribe (serial) ─► translate queue ─► done
              CPU: ffmpeg prefetch          GPU/NPU: whisper        worker (M4)

- Different files occupy different stages concurrently; the extractor works
  one file ahead of the transcriber, translation runs beside transcription.
- Bounded queues give backpressure: at most 2 temp WAVs exist, translation
  work can't pile up unbounded.
- One stop event reaches every stage: ffmpeg is killed instantly, whisper
  stops at the earliest boundary its engine supports, translation stops at a
  batch boundary; remaining jobs become UNPROCESSED (not failures).
- A single file failing at any stage is recorded and never blocks the batch.
- memory_mode "low" defers translation until transcription finished and the
  whisper engine is released — one big model in memory at a time.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..engines.base import TranscribeEngine
from ..engines.models import WhisperModelSpec
from ..media import access, ffmpeg
from .errors import OperationStopped, ScriptoError
from .events import BatchEvent, EventBus, ProgressEvent, StatusEvent
from .history import HistoryEntry, HistoryStore
from .jobs import BatchStats, Job, JobStatus
from . import output as out

logger = logging.getLogger(__name__)

EXTRACT_QUEUE_SIZE = 2
TRANSLATE_QUEUE_SIZE = 64
_QUEUE_POLL_SEC = 0.1


class TranslateStage(Protocol):
    """Implemented in M4 (Ollama). The pipeline only needs this shape."""

    def translate(
        self,
        srt_path: Path,
        source: Path,
        *,
        stop_check,
        progress,
    ) -> list[Path]:
        """Translate one subtitle file; returns produced output paths."""


@dataclass
class PipelineSettings:
    model: WhisperModelSpec
    fmt: str = "srt"
    language: str | None = None            # None = auto detect
    overwrite: bool = False
    export_dir: Path | None = None
    suffix_map: dict[str, str] = field(default_factory=lambda: dict(out.DEFAULT_SUFFIXES))
    memory_mode: str = "balanced"           # balanced | low
    cache_dir: Path | None = None
    engine_label: str = ""                  # for history records


class Pipeline:
    def __init__(
        self,
        engine: TranscribeEngine,
        bus: EventBus,
        history: HistoryStore,
        settings: PipelineSettings,
        translate_stage: TranslateStage | None = None,
    ):
        self._engine = engine
        self._bus = bus
        self._history = history
        self._s = settings
        self._translate_stage = translate_stage

    # ------------------------------------------------------------------ #
    # Public entry
    # ------------------------------------------------------------------ #

    def run(self, files: list[Path], stop: threading.Event) -> tuple[list[Job], BatchStats]:
        started = time.monotonic()
        jobs = [Job(id=i, source=src) for i, src in enumerate(files, start=1)]
        stats = BatchStats(total=len(jobs))
        self._bus.emit(BatchEvent(kind="started", stats={"total": len(jobs)}))

        extract_q: queue.Queue = queue.Queue(maxsize=EXTRACT_QUEUE_SIZE)
        translate_q: queue.Queue = queue.Queue(maxsize=TRANSLATE_QUEUE_SIZE)
        deferred_translations: list[tuple[Job, Path]] = []
        translate_now = self._translate_stage is not None and self._s.memory_mode != "low"

        extractor = threading.Thread(
            target=self._extract_worker,
            args=(jobs, extract_q, stop),
            name="scripto-extract",
            daemon=True,
        )
        translator = None
        if translate_now:
            translator = threading.Thread(
                target=self._translate_worker,
                args=(translate_q, stop),
                name="scripto-translate",
                daemon=True,
            )
            translator.start()
        extractor.start()

        try:
            self._transcribe_loop(
                jobs, extract_q, translate_q, deferred_translations,
                stop, stats, translate_now,
            )
        finally:
            extractor.join(timeout=10)
            if translator is not None:
                translate_q.put(None)   # sentinel
                translator.join()
            # Whisper leaves memory before any deferred (low-mode) translation.
            self._engine.release()

        if self._translate_stage is not None and not translate_now and not stop.is_set():
            self._run_deferred_translations(deferred_translations, stop)

        if self._translate_stage is not None:
            # Let the stage evict its model (Ollama keep_alive=0) — memory hygiene.
            release = getattr(self._translate_stage, "release", None)
            if callable(release):
                try:
                    release()
                except Exception:
                    logger.debug("translate stage release failed", exc_info=True)

        for job in jobs:
            if job.status == JobStatus.PENDING:
                job.status = JobStatus.UNPROCESSED
                stats.unprocessed += 1
                self._emit_status(job)

        stats.elapsed_sec = time.monotonic() - started
        self._bus.emit(BatchEvent(kind="finished", stats=stats.as_dict()))
        ffmpeg.cleanup_orphans(self._s.cache_dir)
        return jobs, stats

    # ------------------------------------------------------------------ #
    # Stage 1: extraction (prefetch, bounded)
    # ------------------------------------------------------------------ #

    def _extract_worker(self, jobs: list[Job], extract_q: queue.Queue, stop: threading.Event) -> None:
        try:
            for job in jobs:
                if stop.is_set():
                    break

                existing = out.existing_transcript(
                    job.source, fmt=self._s.fmt, language=self._s.language,
                    suffix_map=self._s.suffix_map, export_dir=self._s.export_dir,
                )
                if existing is not None and not self._s.overwrite:
                    self._put(extract_q, (job, None, existing), stop)
                    continue

                job.status = JobStatus.EXTRACTING
                self._emit_status(job)
                try:
                    access.check_readable(job.source)
                    wav = ffmpeg.extract_audio(
                        job.source,
                        cache_dir=self._s.cache_dir,
                        stop_check=stop.is_set,
                    )
                except OperationStopped:
                    job.status = JobStatus.PENDING
                    break
                except Exception as exc:
                    self._fail(job, f"extract: {exc}")
                    self._put(extract_q, (job, None, None), stop)
                    continue
                if not self._put(extract_q, (job, wav, None), stop):
                    wav.unlink(missing_ok=True)
                    break
        finally:
            self._put(extract_q, None, None)  # end-of-stream sentinel

    @staticmethod
    def _put(q: queue.Queue, item, stop: threading.Event | None) -> bool:
        """Bounded put that stays responsive to stop; True if enqueued."""
        while True:
            try:
                q.put(item, timeout=_QUEUE_POLL_SEC)
                return True
            except queue.Full:
                if stop is not None and stop.is_set():
                    return False

    # ------------------------------------------------------------------ #
    # Stage 2: transcription (serial — the memory-bound stage)
    # ------------------------------------------------------------------ #

    def _transcribe_loop(
        self,
        jobs: list[Job],
        extract_q: queue.Queue,
        translate_q: queue.Queue,
        deferred: list[tuple[Job, Path]],
        stop: threading.Event,
        stats: BatchStats,
        translate_now: bool,
    ) -> None:
        loaded = False
        while True:
            item = extract_q.get()
            if item is None:
                break
            job, wav, existing = item

            if job.status == JobStatus.FAILED:  # extraction already failed it
                stats.failed += 1
                continue

            if existing is not None:
                job.status = JobStatus.SKIPPED
                job.outputs.append(existing)
                stats.skipped += 1
                self._emit_status(job)
                self._queue_translation(job, existing, translate_q, deferred, translate_now, stop)
                continue

            if stop.is_set():
                job.status = JobStatus.PENDING
                if wav:
                    wav.unlink(missing_ok=True)
                continue

            job.status = JobStatus.TRANSCRIBING
            self._emit_status(job)
            try:
                if not loaded:
                    self._engine.load(self._s.model)
                    loaded = True
                result = self._engine.transcribe(
                    wav,
                    language=self._s.language,
                    progress=lambda done, total, j=job: self._bus.emit(
                        ProgressEvent(scope=f"transcribe:{j.id}", done=int(done), total=int(total))
                    ),
                    stop_check=stop.is_set,
                )
                job.language = result.language or self._s.language
                target = out.output_path(
                    job.source, language=job.language, fmt=self._s.fmt,
                    suffix_map=self._s.suffix_map, export_dir=self._s.export_dir,
                )
                out.write_result(result, target, self._s.fmt)
                job.outputs.append(target)
                job.status = JobStatus.DONE
                stats.done += 1
                self._emit_status(job)
                self._record(job, duration=result.duration or 0.0)
                self._queue_translation(job, target, translate_q, deferred, translate_now, stop)
            except OperationStopped:
                job.status = JobStatus.PENDING
            except Exception as exc:
                self._fail(job, str(exc))
                stats.failed += 1
            finally:
                if wav:
                    wav.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Stage 3: translation (worker in balanced mode, deferred in low mode)
    # ------------------------------------------------------------------ #

    def _queue_translation(
        self,
        job: Job,
        srt_path: Path,
        translate_q: queue.Queue,
        deferred: list[tuple[Job, Path]],
        translate_now: bool,
        stop: threading.Event,
    ) -> None:
        if self._translate_stage is None or self._s.fmt != "srt":
            return
        if translate_now:
            self._put(translate_q, (job, srt_path), stop)
        else:
            deferred.append((job, srt_path))

    def _translate_worker(self, translate_q: queue.Queue, stop: threading.Event) -> None:
        while True:
            item = translate_q.get()
            if item is None:
                break
            job, srt_path = item
            self._translate_one(job, srt_path, stop)

    def _run_deferred_translations(
        self, deferred: list[tuple[Job, Path]], stop: threading.Event
    ) -> None:
        for job, srt_path in deferred:
            if stop.is_set():
                break
            self._translate_one(job, srt_path, stop)

    def _translate_one(self, job: Job, srt_path: Path, stop: threading.Event) -> None:
        if stop.is_set():
            return
        previous = job.status
        job.status = JobStatus.TRANSLATING
        self._emit_status(job)
        try:
            produced = self._translate_stage.translate(
                srt_path,
                job.source,
                stop_check=stop.is_set,
                progress=lambda done, total, j=job: self._bus.emit(
                    ProgressEvent(scope=f"translate:{j.id}", done=done, total=total)
                ),
            )
            job.outputs.extend(produced)
            job.status = previous
            self._emit_status(job)
            if produced:
                self._record_translation(job, produced)
        except OperationStopped:
            job.status = previous
            self._emit_status(job)
        except Exception as exc:
            # Translation failure never undoes a good transcript.
            job.error = f"translate: {exc}"
            job.status = previous
            self._emit_status(job)
            logger.warning("translation failed for %s: %s", job.source.name, exc)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _fail(self, job: Job, reason: str) -> None:
        job.status = JobStatus.FAILED
        job.error = reason
        self._emit_status(job)
        self._record(job)
        logger.warning("job failed: %s — %s", job.source, reason)

    def _record_translation(self, job: Job, produced: list[Path]) -> None:
        stage = self._translate_stage
        try:
            self._history.append(
                HistoryEntry(
                    source=str(job.source),
                    outputs=[
                        {
                            "lang": getattr(stage, "target_code", ""),
                            "format": path.suffix.lstrip("."),
                            "path": str(path),
                        }
                        for path in produced
                    ],
                    model=getattr(stage, "label", "translator"),
                    engine="translate",
                    status="done",
                )
            )
        except Exception:
            logger.exception("could not write translation history entry")

    def _record(self, job: Job, duration: float = 0.0) -> None:
        try:
            self._history.append(
                HistoryEntry(
                    source=str(job.source),
                    outputs=[
                        {
                            "lang": job.language or "",
                            "format": path.suffix.lstrip("."),
                            "path": str(path),
                        }
                        for path in job.outputs
                    ],
                    model=self._s.model.key,
                    engine=self._s.engine_label,
                    status=job.status.value if job.status != JobStatus.PENDING else "failed",
                    error=job.error,
                    duration_sec=duration,
                )
            )
        except Exception:
            logger.exception("could not write history entry")

    def _emit_status(self, job: Job) -> None:
        self._bus.emit(
            StatusEvent(subject=f"job:{job.id}", status=job.status.value, detail=job.error)
        )
