"""Job model: one source media file flowing through the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class JobStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    DONE = "done"
    SKIPPED = "skipped"          # output already existed, overwrite off
    FAILED = "failed"
    UNPROCESSED = "unprocessed"  # batch was stopped before this job ran


@dataclass
class Job:
    id: int
    source: Path
    status: JobStatus = JobStatus.PENDING
    error: str = ""
    language: str | None = None      # detected (or forced) audio language
    outputs: list[Path] = field(default_factory=list)


@dataclass
class BatchStats:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    unprocessed: int = 0
    elapsed_sec: float = 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "done": self.done,
            "skipped": self.skipped,
            "failed": self.failed,
            "unprocessed": self.unprocessed,
            "elapsed_sec": round(self.elapsed_sec, 1),
        }
