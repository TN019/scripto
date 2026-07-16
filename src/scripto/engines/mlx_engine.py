"""mlx-whisper backend (macOS Apple Silicon).

Memory hygiene is the whole point of this wrapper (docs/PLAN.md §2):
my-transcriptor's batches grew RSS monotonically because MLX's Metal buffer
cache is never returned between files. Here every ``transcribe()`` ends with a
cache clear, and ``release()`` also drops the model itself.

mlx-whisper has no mid-file callback, so ``progress`` is only fired once at
completion and ``stop_check`` is honored at file boundaries.
"""

from __future__ import annotations

import gc
import importlib.util
import logging
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.errors import OperationStopped
from .base import ProgressFn, Segment, StopCheck, TranscribeEngine, TranscribeResult

if TYPE_CHECKING:
    from .models import WhisperModelSpec

logger = logging.getLogger(__name__)


class MlxWhisperEngine(TranscribeEngine):
    name = "mlx"

    def __init__(self) -> None:
        self._repo: str | None = None

    @classmethod
    def is_available(cls) -> bool:
        return (
            sys.platform == "darwin"
            and platform.machine() == "arm64"
            and importlib.util.find_spec("mlx_whisper") is not None
        )

    def load(self, spec: "WhisperModelSpec") -> None:
        # mlx-whisper loads lazily inside transcribe() and caches the model in
        # its module-level ModelHolder; switching repos replaces it there.
        if self._repo != spec.mlx_repo and self._repo is not None:
            self.release()
        self._repo = spec.mlx_repo

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: ProgressFn | None = None,
        stop_check: StopCheck | None = None,
    ) -> TranscribeResult:
        if self._repo is None:
            raise RuntimeError("load() must be called before transcribe()")
        if stop_check is not None and stop_check():
            raise OperationStopped()

        import mlx_whisper

        kwargs: dict = {"path_or_hf_repo": self._repo, "verbose": None}
        if language:
            kwargs["language"] = language
        try:
            raw = mlx_whisper.transcribe(str(audio_path), **kwargs)
        finally:
            # Return Metal buffers after every file, success or not.
            self._clear_backend_cache()

        segments = [
            Segment(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=(seg.get("text") or "").strip(),
            )
            for seg in raw.get("segments", []) or []
        ]
        duration = segments[-1].end if segments else 0.0
        if progress is not None:
            progress(duration, duration)
        return TranscribeResult(
            segments=segments,
            language=raw.get("language"),
            duration=duration,
        )

    def release(self) -> None:
        self._repo = self._repo  # keep selection; only free memory
        try:
            # ModelHolder is mlx-whisper's internal cache; clearing it is the
            # only way to actually unload the model. Defensive getattr so an
            # upstream refactor degrades to "no unload" instead of a crash.
            from mlx_whisper import transcribe as _transcribe_mod

            holder = getattr(_transcribe_mod, "ModelHolder", None)
            if holder is not None:
                holder.model = None
                holder.model_path = None
        except Exception:
            logger.debug("could not reset mlx-whisper ModelHolder", exc_info=True)
        self._clear_backend_cache()
        gc.collect()

    @staticmethod
    def _clear_backend_cache() -> None:
        try:
            import mlx.core as mx

            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            else:  # older mlx
                mx.metal.clear_cache()
        except Exception:
            logger.debug("could not clear the MLX cache", exc_info=True)
