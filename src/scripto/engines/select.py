"""Engine selection: users shouldn't need to know engines exist.

Policy: Apple Silicon → mlx; anything else → faster-whisper. Config may force
one; a forced-but-unavailable engine falls back to auto with a logged warning
instead of failing.
"""

from __future__ import annotations

import logging

from ..core.errors import ScriptoError
from .base import TranscribeEngine
from .fw_engine import FasterWhisperEngine
from .mlx_engine import MlxWhisperEngine

logger = logging.getLogger(__name__)

ENGINES: dict[str, type[TranscribeEngine]] = {
    MlxWhisperEngine.name: MlxWhisperEngine,
    FasterWhisperEngine.name: FasterWhisperEngine,
}


def available_engines() -> list[str]:
    return [name for name, cls in ENGINES.items() if cls.is_available()]


def resolve_engine_name(configured: str = "auto") -> tuple[str, str]:
    """Return (engine_name, human-readable reason)."""
    available = available_engines()
    if not available:
        raise ScriptoError(
            "No transcription engine is available. Run `uv sync` to install "
            "dependencies (faster-whisper everywhere, mlx-whisper on Apple Silicon).",
            key="errors.no_engine",
        )

    if configured != "auto":
        if configured in available:
            return configured, "forced by config"
        logger.warning(
            "configured engine %r is not available here; falling back to auto",
            configured,
        )

    if MlxWhisperEngine.name in available:
        reason = "auto: Apple Silicon detected"
    else:
        reason = "auto: default cross-platform engine"
    name = MlxWhisperEngine.name if MlxWhisperEngine.name in available else available[0]
    return name, reason


def create_engine(configured: str = "auto") -> TranscribeEngine:
    name, reason = resolve_engine_name(configured)
    logger.info("engine selected: %s (%s)", name, reason)
    return ENGINES[name]()
