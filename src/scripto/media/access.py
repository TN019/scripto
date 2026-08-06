"""Pre-flight readability diagnosis for source media, and iCloud downloads.

"ffmpeg: Operation not permitted" tells the user nothing. Before extraction
we probe the file ourselves and raise a ScriptoError that distinguishes:

- macOS TCC privacy denial (terminal/app lacks Desktop/Documents/Downloads
  folder permission) → point at System Settings
- anything else unreadable → plain readable reason

iCloud files are not an error at all. macOS keeps them *dataless*: the name,
size and timestamps are real, the bytes live in the cloud, and the kernel
fetches them from the file provider the moment anything reads. So a file
that isn't downloaded yet is something we can fix ourselves — ``materialize``
pulls it down instead of sending the user to Finder to right-click dozens of
lecture recordings one at a time.

Detection is the ``UF_DATALESS`` stat flag, not ``st_blocks == 0``: an
APFS-compressed file that is fully present on disk also reports zero blocks,
and refusing those was a false positive.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from ..core.errors import OperationStopped, ScriptoError

_TCC_PROTECTED_PARTS = {"Desktop", "Documents", "Downloads"}
_PROBE_BYTES = 4096

# macOS: the file's contents are not on this disk (iCloud Drive, and any
# other File Provider). Defined in <sys/stat.h>.
UF_DATALESS = 0x40000000

_BLOCK_BYTES = 512
_POLL_SEC = 0.25
# A download gets this long per megabyte, on top of a fixed floor — generous
# enough for a lecture recording on hotel wifi, short enough that an offline
# machine reports something instead of hanging until the user gives up.
_TIMEOUT_FLOOR_SEC = 120.0
_TIMEOUT_PER_MB_SEC = 12.0


def _is_tcc_suspect(path: Path) -> bool:
    return sys.platform == "darwin" and bool(_TCC_PROTECTED_PARTS & set(path.parts))


def _is_not_downloaded(st: os.stat_result) -> bool:
    """True when the file's bytes are in the cloud rather than on this disk."""
    if hasattr(st, "st_flags"):
        return bool(st.st_flags & UF_DATALESS)
    # No stat flags (non-BSD): fall back to the blocks heuristic, which is
    # all that platform gives us.
    return getattr(st, "st_blocks", None) == 0 and st.st_size > 0


def needs_download(src: Path) -> bool:
    try:
        return _is_not_downloaded(src.stat())
    except OSError:
        return False


def download_timeout(size_bytes: int) -> float:
    return _TIMEOUT_FLOOR_SEC + (size_bytes / 1_000_000) * _TIMEOUT_PER_MB_SEC


def materialize(
    src: Path,
    *,
    stop_check=None,
    on_progress=None,
    timeout_sec: float | None = None,
) -> None:
    """Pull a not-yet-downloaded iCloud file onto local disk.

    Reading is the trigger — one byte is enough to bring the whole file
    down — but that read blocks for the entire download, minutes for a
    lecture recording. So it runs on a thread while we watch the file grow,
    stay responsive to a stop request, and give up on a size-scaled
    deadline rather than hanging forever when there is no network.

    ``on_progress(done_bytes, total_bytes)`` is best-effort: some providers
    only publish the blocks at the end, in which case it reports 0 until
    the download completes.
    """
    try:
        st = src.stat()
    except OSError:
        return
    if not _is_not_downloaded(st):
        return

    total = st.st_size
    limit = timeout_sec or download_timeout(total)
    deadline = time.monotonic() + limit
    failure: list[BaseException] = []

    def pull() -> None:
        try:
            with src.open("rb") as handle:
                handle.read(1)
        except BaseException as exc:  # re-raised on the caller's thread
            failure.append(exc)

    # Daemon: if we abandon it on stop or timeout, the read is still parked
    # in the kernel and must not keep the process alive.
    worker = threading.Thread(target=pull, name="scripto-icloud", daemon=True)
    worker.start()

    while True:
        worker.join(_POLL_SEC)
        if not worker.is_alive():
            break
        if stop_check is not None and stop_check():
            raise OperationStopped()
        if on_progress is not None:
            on_progress(_downloaded_bytes(src, total), total)
        if time.monotonic() > deadline:
            raise ScriptoError(
                f"{src.name} is still downloading from iCloud after "
                f"{int(limit)}s. Check your connection, or download it in "
                "Finder first.",
                key="errors.icloud_timeout",
                name=src.name,
                seconds=int(limit),
            )

    if failure:
        exc = failure[0]
        if isinstance(exc, PermissionError):
            _raise_permission(src)
        raise ScriptoError(
            f"Could not download {src.name} from iCloud: {exc}. "
            "In Finder, right-click it and choose \"Download Now\".",
            key="errors.icloud_failed",
            name=src.name,
            reason=str(exc),
        ) from None
    if on_progress is not None:
        on_progress(total, total)


def evict(src: Path) -> bool:
    """Send a downloaded iCloud file back to cloud-only storage.

    The counterpart to ``materialize`` — Finder's "Remove Download". Called
    for files we pulled down ourselves, so a batch of lecture recordings
    doesn't leave tens of gigabytes behind: the file is left exactly as we
    found it, name and size intact, bytes back in the cloud.

    ``brctl evict`` was removed from macOS, so the only supported route is
    NSFileManager's, reached here through the Objective-C runtime rather
    than by taking a PyObjC dependency for a single selector. Returns False
    instead of raising — failing to tidy up is never worth failing a job.
    """
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(restype, obj, selector, argtypes=(), *args):
            # objc_msgSend is variadic: its signature must be declared per
            # call site, not once.
            fn = objc["objc_msgSend"]
            fn.restype = restype
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
            return fn(obj, objc.sel_registerName(selector), *args)

        text = send(
            ctypes.c_void_p, objc.objc_getClass(b"NSString"),
            b"stringWithUTF8String:", [ctypes.c_char_p], str(src).encode(),
        )
        url = send(
            ctypes.c_void_p, objc.objc_getClass(b"NSURL"),
            b"fileURLWithPath:", [ctypes.c_void_p], text,
        )
        manager = send(
            ctypes.c_void_p, objc.objc_getClass(b"NSFileManager"), b"defaultManager"
        )
        error = ctypes.c_void_p(0)
        return bool(send(
            ctypes.c_bool, manager, b"evictUbiquitousItemAtURL:error:",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
            url, ctypes.byref(error),
        ))
    except Exception:
        return False


def _downloaded_bytes(src: Path, total: int) -> int:
    try:
        blocks = getattr(src.stat(), "st_blocks", 0) or 0
    except OSError:
        return 0
    return min(total, blocks * _BLOCK_BYTES)


def check_readable(src: Path) -> None:
    """Raise a diagnosis-specific ScriptoError when ``src`` can't be processed.

    A file that is merely not downloaded yet is not a failure — see
    ``materialize``, which the caller runs first.
    """
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
