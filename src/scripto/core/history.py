"""History index (R5): one JSON file recording every produced output.

Append-mostly, atomic writes, corruption quarantined (same policy as config).
The History UI reads this to reopen past outputs — or show "deleted" when the
file is gone.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_VERSION = 1


@dataclass
class HistoryEntry:
    source: str
    outputs: list[dict[str, str]]     # {"lang": "en", "format": "srt", "path": "..."}
    model: str
    engine: str
    status: str                       # "done" | "failed" | "skipped"
    error: str = ""
    duration_sec: float = 0.0
    created_at: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self._path = path or paths.history_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: HistoryEntry) -> None:
        with self._lock:
            data = self._read()
            data["entries"].append(asdict(entry))
            self._write(data)

    def entries(self) -> list[HistoryEntry]:
        """All entries, newest first."""
        with self._lock:
            data = self._read()
        result = []
        for raw in data["entries"]:
            known = {k: raw[k] for k in HistoryEntry.__dataclass_fields__ if k in raw}
            try:
                result.append(HistoryEntry(**known))
            except TypeError:
                continue  # tolerate rows written by other versions
        result.reverse()
        return result

    def remove(self, entry_ids: set[str]) -> int:
        """Delete entries by id (History UI's 'clean up stale rows')."""
        with self._lock:
            data = self._read()
            before = len(data["entries"])
            data["entries"] = [e for e in data["entries"] if e.get("id") not in entry_ids]
            self._write(data)
            return before - len(data["entries"])

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": SCHEMA_VERSION, "entries": []}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                raise ValueError("bad history shape")
            return data
        except (json.JSONDecodeError, ValueError, OSError):
            self._quarantine()
            return {"version": SCHEMA_VERSION, "entries": []}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)

    def _quarantine(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(self._path, self._path.with_name(f"{self._path.name}.corrupt-{stamp}"))
        except OSError:
            pass
