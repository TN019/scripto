"""Environment doctor: first-run bootstrap checks with actionable hints.

Required checks gate transcription (ffmpeg, an engine, writable data dir);
optional ones only warn (selected model not downloaded yet, Ollama down).
Every hint is platform-specific and copy-pasteable.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

from . import paths


@dataclass(frozen=True)
class CheckResult:
    key: str          # i18n suffix: doctor.<key>
    ok: bool
    required: bool
    detail: str       # short factual detail (version/path/url)
    hint: str = ""    # how to fix, when not ok


def _check_ffmpeg() -> CheckResult:
    from ..media.ffmpeg import ffmpeg_path, install_hint

    path = ffmpeg_path()
    return CheckResult(
        key="ffmpeg",
        ok=path is not None,
        required=True,
        detail=path or "",
        hint=install_hint(),
    )


def _check_engine() -> CheckResult:
    from ..engines.select import available_engines

    engines = available_engines()
    if sys.platform.startswith("win"):
        hint = "uv sync"
    else:
        hint = "uv sync"
    return CheckResult(
        key="engine",
        ok=bool(engines),
        required=True,
        detail=", ".join(engines),
        hint=hint,
    )


def _check_model(config: dict) -> CheckResult:
    from ..engines.models import installed_keys
    from ..engines.select import resolve_engine_name

    model = config.get("whisper_model", "")
    try:
        engine_name, _ = resolve_engine_name(config.get("engine", "auto"))
        installed = model in installed_keys(engine_name)
    except Exception:
        installed = False
    return CheckResult(
        key="model",
        ok=installed,
        required=False,   # downloadable in-app; first transcription can fetch it
        detail=model,
        hint="Scripto → Settings → Manage models",
    )


def _check_ollama(config: dict) -> CheckResult:
    from ..translate.ollama import OllamaClient

    url = config.get("ollama_url", "http://localhost:11434")
    reachable = OllamaClient(url).is_reachable()
    return CheckResult(
        key="ollama",
        ok=reachable,
        required=False,   # translation is optional
        detail=url,
        hint="ollama serve",
    )


def _check_data_dir() -> CheckResult:
    try:
        directory = paths.data_dir()
        probe = directory / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult(key="data_dir", ok=True, required=True, detail=str(directory))
    except Exception as exc:
        return CheckResult(
            key="data_dir", ok=False, required=True, detail=str(exc), hint=""
        )


def run_doctor(config: dict) -> list[CheckResult]:
    return [
        _check_ffmpeg(),
        _check_engine(),
        _check_model(config),
        _check_ollama(config),
        _check_data_dir(),
    ]


def doctor_ok(results: list[CheckResult]) -> bool:
    """True when every *required* check passes."""
    return all(r.ok for r in results if r.required)
