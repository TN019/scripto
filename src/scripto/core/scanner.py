"""Media discovery (R4): mixed files/folders from any directories.

Handles the messy inputs people actually paste: shell-escaped paths
(``Mobile\\ Documents``), quoted paths, ``file://`` URLs, ``~``, and bare
``Desktop/...`` fragments. Scanning is plain functions with a ``stop_check``
so callers run it on a background thread and cancel freely — never on a UI
thread (my-transcriptor froze up to 120s doing exactly that).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from .errors import OperationStopped

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".flac"}
DEFAULT_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

StopCheck = Callable[[], bool]


@dataclass
class ScanResult:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # (i18n key, raw) pairs rendered by UI

    def warn(self, key: str, value: str) -> None:
        self.warnings.append(f"{key}:{value}")


def normalize_input_path(raw: str) -> str:
    """Undo quoting/escaping so pasted paths become real filesystem paths."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1]
    s = s.replace("\\ ", " ").replace("\\~", "~")
    if s.startswith("file://"):
        s = unquote(urlparse(s).path)
    return s.strip()


def resolve_input_path(raw: str) -> Path:
    """Best-effort resolution including home-relative macOS Finder pastes."""
    s = normalize_input_path(raw)
    p = Path(s).expanduser()
    if p.exists():
        return p
    if s and not s.startswith(("/", "~")) and not _looks_like_windows_abs(s):
        home_candidate = Path.home() / s
        if home_candidate.exists():
            return home_candidate
        head = s.split("/", 1)[0]
        if head in ("Users", "private", "var", "Volumes"):
            abs_candidate = Path("/" + s)
            if abs_candidate.exists():
                return abs_candidate
    return p


def _looks_like_windows_abs(s: str) -> bool:
    return len(s) >= 2 and s[1] == ":" and s[0].isalpha()


def is_supported_media(path: Path, extensions: set[str]) -> bool:
    if path.suffix.lower() not in extensions:
        return False
    try:
        if path.is_dir():
            return False
    except OSError:
        pass
    return True


def scan(
    raw_inputs: list[str],
    *,
    recursive: bool = True,
    extensions: set[str] | None = None,
    stop_check: StopCheck | None = None,
) -> ScanResult:
    """Expand raw user inputs into a de-duplicated, ordered media file list."""
    supported = extensions or DEFAULT_EXTENSIONS
    result = ScanResult()
    seen: set[Path] = set()

    def check_stop() -> None:
        if stop_check is not None and stop_check():
            raise OperationStopped()

    def add(path: Path) -> bool:
        resolved = path.resolve()
        if resolved in seen:
            return False
        seen.add(resolved)
        result.files.append(resolved)
        return True

    for raw in raw_inputs:
        check_stop()
        if not raw.strip():
            continue
        path = resolve_input_path(raw)
        if not path.exists():
            result.warn("scan.missing", raw.strip())
            continue
        if is_supported_media(path, supported):
            add(path)
            continue
        if path.is_dir():
            added = _scan_dir(path, recursive, supported, add, check_stop)
            if added == 0:
                result.warn("scan.empty_dir", str(path))
        else:
            result.warn("scan.unsupported", str(path))
    return result


def _scan_dir(
    root: Path,
    recursive: bool,
    supported: set[str],
    add: Callable[[Path], bool],
    check_stop: Callable[[], None],
) -> int:
    added = 0
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            check_stop()
            dirnames.sort()
            base = Path(dirpath)
            for name in sorted(filenames):
                candidate = base / name
                if is_supported_media(candidate, supported) and add(candidate):
                    added += 1
    else:
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return 0
        for candidate in entries:
            check_stop()
            if is_supported_media(candidate, supported) and add(candidate):
                added += 1
    return added
