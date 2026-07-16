"""Pre-flight readability diagnosis for source media.

"ffmpeg: Operation not permitted" tells the user nothing. Before extraction
we probe the file ourselves and raise a ScriptoError that distinguishes:

- macOS TCC privacy denial (terminal/app lacks Desktop/Documents/Downloads
  folder permission) → point at System Settings
- iCloud placeholder (file not downloaded locally: size > 0, zero blocks
  on disk) → point at Finder's "Download Now"
- anything else unreadable → plain readable reason
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..core.errors import ScriptoError

_TCC_PROTECTED_PARTS = {"Desktop", "Documents", "Downloads"}
_PROBE_BYTES = 4096


def _is_tcc_suspect(path: Path) -> bool:
    return sys.platform == "darwin" and bool(_TCC_PROTECTED_PARTS & set(path.parts))


def _is_icloud_placeholder(st: os.stat_result) -> bool:
    blocks = getattr(st, "st_blocks", None)
    return blocks == 0 and st.st_size > 0


def check_readable(src: Path) -> None:
    """Raise a diagnosis-specific ScriptoError when ``src`` can't be processed."""
    try:
        st = src.stat()
    except FileNotFoundError:
        raise ScriptoError(
            f"File not found: {src}", key="errors.file_missing", name=src.name
        ) from None
    except PermissionError:
        _raise_permission(src)
        return

    if st.st_size == 0:
        raise ScriptoError(
            f"File is empty: {src.name}", key="errors.file_empty", name=src.name
        )
    if _is_icloud_placeholder(st):
        raise ScriptoError(
            f"{src.name} looks like an iCloud placeholder that is not downloaded "
            "locally. In Finder, right-click it and choose \"Download Now\".",
            key="errors.icloud_placeholder",
            name=src.name,
        )

    try:
        with src.open("rb") as f:
            f.read(_PROBE_BYTES)
    except PermissionError:
        _raise_permission(src)
    except OSError as exc:
        raise ScriptoError(
            f"Cannot read {src.name}: {exc.strerror or exc}",
            key="errors.file_unreadable",
            name=src.name,
            reason=str(exc.strerror or exc),
        ) from None


def _raise_permission(src: Path) -> None:
    if _is_tcc_suspect(src):
        raise ScriptoError(
            f"macOS denied access to {src.name}. Grant your terminal access to "
            "this folder: System Settings → Privacy & Security → Files and "
            "Folders (or move the file out of Desktop/Documents/Downloads).",
            key="errors.tcc_denied",
            name=src.name,
        ) from None
    raise ScriptoError(
        f"Permission denied reading {src.name}.",
        key="errors.file_permission",
        name=src.name,
    ) from None
