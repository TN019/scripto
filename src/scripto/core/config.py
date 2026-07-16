"""Config service: defaults merge, atomic writes, corruption fallback.

Merge is shallow: a key present in the user's file wins wholesale (including
dict values like ``lang_suffixes``). Unknown keys are preserved on save so
newer configs survive older builds.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    # UI
    "language": "",                  # "" = not chosen yet; "en" | "zh"
    "theme": "system",               # light | dark | system
    # Transcription
    "engine": "auto",                # auto | mlx | faster-whisper
    "whisper_model": "large-v3-turbo",
    "transcribe_language": "auto",   # auto | en | zh
    "output_format": "srt",          # srt | txt | vtt | json
    "recursive_scan": True,
    "overwrite": False,
    # Translation
    "translate_enabled": False,
    "translate_target": "zh",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen3:8b",
    "translate_batch_blocks": 40,
    "translate_batch_max_chars": 3000,
    # Pipeline
    "memory_mode": "balanced",       # balanced | low
    # Output (R4): None = write next to the source file
    "export_dir": None,
    "lang_suffixes": {"en": ".en", "zh": ".cn"},
}


class ConfigService:
    def __init__(self, path: Path | None = None):
        self._path = path or paths.config_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        """Read config merged over DEFAULTS; never raises on a bad file."""
        with self._lock:
            raw = self._read_raw()
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in raw.items() if v is not None})
        return merged

    def save(self, config: dict[str, Any]) -> None:
        """Atomically replace the config file (temp file + rename)."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)

    def update(self, **changes: Any) -> dict[str, Any]:
        """Apply changes on top of the current file and save; returns the result."""
        config = self.load()
        config.update(changes)
        self.save(config)
        return config

    def _read_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config root must be an object")
        except (json.JSONDecodeError, ValueError, OSError):
            self._quarantine()
            return {}
        return data

    def _quarantine(self) -> None:
        """Keep the corrupt file for inspection instead of silently losing it."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        try:
            os.replace(self._path, backup)
        except OSError:
            pass
