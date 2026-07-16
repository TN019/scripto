"""faster-whisper backend (Windows and any non-Apple-Silicon machine).

Device policy: CUDA when present (float16), otherwise CPU with int8 — free and
usable on ordinary laptops. Segments stream out of CTranslate2, which gives us
real mid-file progress and mid-file stop support.
"""

from __future__ import annotations

import gc
import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.errors import OperationStopped
from .base import ProgressFn, Segment, StopCheck, TranscribeEngine, TranscribeResult

if TYPE_CHECKING:
    from .models import WhisperModelSpec

logger = logging.getLogger(__name__)


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class FasterWhisperEngine(TranscribeEngine):
    name = "faster-whisper"

    def __init__(self) -> None:
        self._model = None
        self._loaded_repo: str | None = None

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def load(self, spec: "WhisperModelSpec") -> None:
        if self._loaded_repo == spec.fw_repo and self._model is not None:
            return
        self.release()

        from faster_whisper import WhisperModel

        if _cuda_available():
            device, compute_type = "cuda", "float16"
        else:
            device, compute_type = "cpu", "int8"
        logger.info(
            "loading %s on %s (%s)", spec.fw_repo, device, compute_type
        )
        self._model = WhisperModel(spec.fw_repo, device=device, compute_type=compute_type)
        self._loaded_repo = spec.fw_repo

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: ProgressFn | None = None,
        stop_check: StopCheck | None = None,
    ) -> TranscribeResult:
        if self._model is None:
            raise RuntimeError("load() must be called before transcribe()")
        if stop_check is not None and stop_check():
            raise OperationStopped()

        segment_iter, info = self._model.transcribe(str(audio_path), language=language)
        segments: list[Segment] = []
        total = float(getattr(info, "duration", 0.0) or 0.0)
        for seg in segment_iter:
            if stop_check is not None and stop_check():
                raise OperationStopped()
            segments.append(
                Segment(start=float(seg.start), end=float(seg.end), text=seg.text.strip())
            )
            if progress is not None and total > 0:
                progress(min(float(seg.end), total), total)
        return TranscribeResult(
            segments=segments,
            language=getattr(info, "language", None),
            duration=total or (segments[-1].end if segments else 0.0),
        )

    def release(self) -> None:
        self._model = None
        self._loaded_repo = None
        gc.collect()
