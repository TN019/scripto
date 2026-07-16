"""Engine contract tests.

The fast part runs a FakeEngine against the shared contract. The real-engine
smoke tests download tiny models and transcribe synthesized speech; they are
opt-in via SCRIPTO_ENGINE_SMOKE=1 (run before every engine-related release).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripto.core.errors import OperationStopped
from scripto.engines.base import Segment, TranscribeEngine, TranscribeResult
from scripto.engines.fw_engine import FasterWhisperEngine
from scripto.engines.mlx_engine import MlxWhisperEngine
from scripto.engines.models import get_spec

smoke = pytest.mark.skipif(
    not os.environ.get("SCRIPTO_ENGINE_SMOKE"),
    reason="set SCRIPTO_ENGINE_SMOKE=1 to run real engine smoke tests",
)


class FakeEngine(TranscribeEngine):
    name = "fake"

    def __init__(self):
        self.loaded = None
        self.released = 0

    @classmethod
    def is_available(cls) -> bool:
        return True

    def load(self, spec):
        self.loaded = spec

    def transcribe(self, audio_path, *, language=None, progress=None, stop_check=None):
        if stop_check is not None and stop_check():
            raise OperationStopped()
        if progress is not None:
            progress(1.0, 1.0)
        return TranscribeResult(
            segments=[Segment(0.0, 1.0, "hello")], language=language, duration=1.0
        )

    def release(self):
        self.released += 1


def run_contract(engine: TranscribeEngine, audio: Path) -> TranscribeResult:
    """Shared expectations every engine must satisfy."""
    spec = get_spec("tiny")
    engine.load(spec)
    engine.load(spec)  # idempotent

    with pytest.raises(OperationStopped):
        engine.transcribe(audio, stop_check=lambda: True)

    ticks: list[tuple[float, float]] = []
    result = engine.transcribe(audio, progress=lambda d, t: ticks.append((d, t)))
    assert isinstance(result, TranscribeResult)
    assert result.segments, "expected at least one segment"
    for seg in result.segments:
        assert seg.end >= seg.start >= 0.0
    assert all(0.0 <= done <= total for done, total in ticks)

    engine.release()
    engine.release()  # safe to call repeatedly
    return result


def test_abstract_engine_cannot_be_instantiated():
    with pytest.raises(TypeError):
        TranscribeEngine()  # type: ignore[abstract]


def test_fake_engine_passes_contract(tmp_path):
    result = run_contract(FakeEngine(), tmp_path / "dummy.wav")
    assert result.text == "hello"


@pytest.fixture(scope="session")
def speech_wav(tmp_path_factory) -> Path:
    """Synthesized English speech ('hello world…') as 16k mono WAV (macOS only)."""
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs macOS `say` and ffmpeg to synthesize test speech")
    base = tmp_path_factory.mktemp("speech")
    aiff = base / "speech.aiff"
    wav = base / "speech.wav"
    subprocess.run(
        ["say", "-o", str(aiff), "hello world, this is a scripto engine test"],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff), "-ac", "1", "-ar", "16000", str(wav)],
        check=True,
        capture_output=True,
    )
    return wav


@smoke
def test_faster_whisper_engine_real_smoke(speech_wav):
    if not FasterWhisperEngine.is_available():
        pytest.skip("faster-whisper not installed")
    result = run_contract(FasterWhisperEngine(), speech_wav)
    assert "hello" in result.text.lower()


@smoke
def test_mlx_engine_real_smoke(speech_wav):
    if not MlxWhisperEngine.is_available():
        pytest.skip("mlx-whisper unavailable (needs Apple Silicon)")
    result = run_contract(MlxWhisperEngine(), speech_wav)
    assert "hello" in result.text.lower()
