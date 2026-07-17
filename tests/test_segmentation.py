"""Very-long-file segmentation: wav splitting + timestamp stitching."""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import pytest

from scripto.core import pipeline as pl
from scripto.core.events import EventBus
from scripto.core.history import HistoryStore
from scripto.engines.base import Segment, TranscribeEngine, TranscribeResult
from scripto.engines.models import get_spec
from scripto.media import ffmpeg as ff


def make_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def test_wav_duration(tmp_path):
    wav = make_wav(tmp_path / "a.wav", 2.5)
    assert ff.wav_duration(wav) == pytest.approx(2.5, abs=0.01)


def test_split_wav_chunks_and_preserves_frames(tmp_path):
    wav = make_wav(tmp_path / "a.wav", 5.0)
    chunks = ff.split_wav(wav, 2.0, tmp_path)
    assert len(chunks) == 3
    durations = [ff.wav_duration(c) for c in chunks]
    assert durations[0] == pytest.approx(2.0, abs=0.01)
    assert durations[1] == pytest.approx(2.0, abs=0.01)
    assert durations[2] == pytest.approx(1.0, abs=0.01)
    assert sum(durations) == pytest.approx(5.0, abs=0.01)
    with wave.open(str(chunks[0]), "rb") as first:
        assert first.getframerate() == 16000
        assert first.getnchannels() == 1


class ChunkEngine(TranscribeEngine):
    """Returns one segment per chunk stamped with the chunk's duration."""

    name = "chunk-stub"

    def __init__(self):
        self.seen: list[str] = []

    @classmethod
    def is_available(cls):
        return True

    def load(self, spec):
        pass

    def transcribe(self, audio_path, *, language=None, progress=None, stop_check=None):
        self.seen.append(audio_path.name)
        duration = ff.wav_duration(audio_path)
        return TranscribeResult(
            segments=[Segment(0.0, duration, f"chunk {len(self.seen)}")],
            language="en",
            duration=duration,
        )

    def release(self):
        pass


def test_pipeline_stitches_segmented_transcription(tmp_path, monkeypatch):
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"media")
    cache = tmp_path / "cache"
    cache.mkdir()
    long_wav = make_wav(cache / "extracted.wav", 5.0)

    monkeypatch.setattr(
        pl.ffmpeg, "extract_audio",
        lambda src, cache_dir=None, stop_check=None, **_k: long_wav,
    )
    monkeypatch.setattr(pl.ffmpeg, "cleanup_orphans", lambda *_a, **_k: 0)

    engine = ChunkEngine()
    settings = pl.PipelineSettings(
        model=get_spec("tiny"),
        cache_dir=cache,
        engine_label="stub",
        segment_threshold_sec=2.0,   # force segmentation for the 5s wav
        segment_chunk_sec=2.0,
    )
    pipe = pl.Pipeline(
        engine=engine, bus=EventBus(),
        history=HistoryStore(tmp_path / "history.json"), settings=settings,
    )
    jobs, stats = pipe.run([source], threading.Event())

    assert stats.done == 1
    assert len(engine.seen) == 3  # 5s / 2s chunks
    output = source.with_name("lecture.en.srt")
    from scripto.translate import srt

    blocks = srt.parse_srt(output.read_text(encoding="utf-8"))
    assert len(blocks) == 3
    # stitched timestamps: chunk starts at 0s, 2s, 4s
    assert blocks[0].timestamp.startswith("00:00:00,000")
    assert blocks[1].timestamp.startswith("00:00:02,000")
    assert blocks[2].timestamp.startswith("00:00:04,000")
    # chunk temp files removed
    assert list(cache.glob("*.part*.wav")) == []


def test_short_file_is_not_segmented(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"media")
    cache = tmp_path / "cache"
    cache.mkdir()
    short_wav = make_wav(cache / "short.wav", 1.0)
    monkeypatch.setattr(
        pl.ffmpeg, "extract_audio",
        lambda src, cache_dir=None, stop_check=None, **_k: short_wav,
    )
    monkeypatch.setattr(pl.ffmpeg, "cleanup_orphans", lambda *_a, **_k: 0)

    engine = ChunkEngine()
    settings = pl.PipelineSettings(
        model=get_spec("tiny"), cache_dir=cache, engine_label="stub",
        segment_threshold_sec=2.0, segment_chunk_sec=2.0,
    )
    pipe = pl.Pipeline(
        engine=engine, bus=EventBus(),
        history=HistoryStore(tmp_path / "history.json"), settings=settings,
    )
    _jobs, stats = pipe.run([source], threading.Event())
    assert stats.done == 1
    assert len(engine.seen) == 1  # single pass, no chunking
