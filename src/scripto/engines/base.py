"""The engine contract both backends must satisfy.

Rules:
- ``load()`` is separate from ``transcribe()`` so a batch reuses one loaded
  model; loading a different model releases the previous one first.
- ``transcribe()`` honors ``stop_check`` at least at its boundaries (engines
  that stream segments also honor it mid-file) and raises
  :class:`~scripto.core.errors.OperationStopped` when stopped.
- ``release()`` frees the model and any backend caches; after it the process
  footprint must drop back near baseline (verified in M7).
- Both engines return the exact same ``TranscribeResult`` shape so nothing
  downstream knows which backend ran.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import WhisperModelSpec

ProgressFn = Callable[[float, float], None]  # (done_seconds, total_seconds)
StopCheck = Callable[[], bool]


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscribeResult:
    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    duration: float | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())


class TranscribeEngine(ABC):
    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Whether this backend can run on the current machine."""

    @abstractmethod
    def load(self, spec: "WhisperModelSpec") -> None:
        """Prepare ``spec`` for transcription; idempotent for the same model."""

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: ProgressFn | None = None,
        stop_check: StopCheck | None = None,
    ) -> TranscribeResult:
        """Transcribe a prepared (16k mono WAV) audio file."""

    @abstractmethod
    def release(self) -> None:
        """Free the model and backend caches; safe to call repeatedly."""
