"""ffmpeg wrapper: detection, audio extraction, timeouts, cleanup.

Rules (from docs/PLAN.md §2):
- every subprocess has a timeout and can be killed by a stop request
- failures surface a readable reason (stderr tail), not a traceback
- temporary files are cleaned up on success, failure, and interruption
- temp audio never lands next to the user's media
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..core import paths
from ..core.errors import OperationStopped, ScriptoError

DEFAULT_TIMEOUT = 1800.0  # generous: extraction of very long videos
_POLL_INTERVAL = 0.1

StopCheck = Callable[[], bool]


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def install_hint() -> str:
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    if sys.platform.startswith("win"):
        return "winget install ffmpeg  (or: choco install ffmpeg)"
    return "sudo apt install ffmpeg  (or your distro's package manager)"


def require_ffmpeg() -> str:
    path = ffmpeg_path()
    if path is None:
        raise ScriptoError(
            f"ffmpeg not found. Install it with: {install_hint()}",
            key="errors.ffmpeg_missing",
            hint=install_hint(),
        )
    return path


def extract_audio(
    src: Path,
    *,
    cache_dir: Path | None = None,
    sample_rate: int = 16000,
    channels: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
    stop_check: StopCheck | None = None,
) -> Path:
    """Extract mono WAV for transcription; returns the temp file path.

    The caller owns the returned file and must delete it when done (the
    pipeline does). On any failure path the temp file is already gone.
    """
    ffmpeg = require_ffmpeg()
    directory = cache_dir or paths.cache_dir()
    directory.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix=src.stem + "_", dir=directory)
    import os

    os.close(fd)
    wav_path = Path(tmp_name)
    stderr_path = wav_path.with_suffix(".stderr")

    cmd = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-i", str(src),
        "-vn",
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(wav_path),
    ]

    try:
        with stderr_path.open("wb") as stderr_file:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=stderr_file
            )
            started = time.monotonic()
            try:
                while proc.poll() is None:
                    if stop_check is not None and stop_check():
                        _kill(proc)
                        raise OperationStopped()
                    if time.monotonic() - started > timeout:
                        _kill(proc)
                        raise ScriptoError(
                            f"ffmpeg timed out after {int(timeout)}s on {src.name}",
                            key="errors.extract_timeout",
                            seconds=int(timeout),
                            name=src.name,
                        )
                    time.sleep(_POLL_INTERVAL)
            except BaseException:
                if proc.poll() is None:
                    _kill(proc)
                raise

        if proc.returncode != 0:
            raise ScriptoError(
                f"ffmpeg could not read {src.name}: {_stderr_tail(stderr_path)}",
                key="errors.ffmpeg_failed",
                name=src.name,
                reason=_stderr_tail(stderr_path),
            )
        return wav_path
    except BaseException:
        wav_path.unlink(missing_ok=True)
        raise
    finally:
        stderr_path.unlink(missing_ok=True)


def wav_duration(path: Path) -> float:
    """Duration in seconds of a PCM WAV (the format our extraction produces)."""
    import wave

    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        return wav.getnframes() / rate if rate else 0.0


def split_wav(path: Path, chunk_sec: float, out_dir: Path | None = None) -> list[Path]:
    """Split a PCM WAV into consecutive chunks of ``chunk_sec`` (last one shorter).

    Streams frame blocks — a multi-GB WAV never sits in memory whole. Caller
    owns (and deletes) the returned chunk files.
    """
    import wave

    directory = out_dir or path.parent
    chunks: list[Path] = []
    with wave.open(str(path), "rb") as src_wav:
        rate = src_wav.getframerate()
        params = src_wav.getparams()
        frames_per_chunk = max(1, int(chunk_sec * rate))
        total = src_wav.getnframes()
        index = 0
        read = 0
        block = rate * 60  # one minute per read keeps memory flat
        while read < total:
            chunk_path = directory / f"{path.stem}.part{index:03d}.wav"
            with wave.open(str(chunk_path), "wb") as out_wav:
                out_wav.setparams(params)
                remaining = min(frames_per_chunk, total - read)
                while remaining > 0:
                    frames = src_wav.readframes(min(block, remaining))
                    if not frames:
                        break
                    out_wav.writeframes(frames)
                    got = len(frames) // (params.sampwidth * params.nchannels)
                    remaining -= got
                    read += got
            chunks.append(chunk_path)
            index += 1
    return chunks


def cleanup_orphans(cache_dir: Path | None = None) -> int:
    """Remove leftover temp files from a previous crash; returns count removed."""
    directory = cache_dir or paths.cache_dir()
    removed = 0
    if not directory.exists():
        return 0
    for leftover in directory.iterdir():
        if leftover.suffix in (".wav", ".stderr"):
            try:
                leftover.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _kill(proc: subprocess.Popen) -> None:
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _stderr_tail(stderr_path: Path) -> str:
    try:
        lines = [
            line.strip()
            for line in stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        return lines[-1] if lines else "unknown ffmpeg error"
    except OSError:
        return "unknown ffmpeg error"
