"""Pipeline scheduler tests: run against a fake engine and a fake extractor
so they are fast and deterministic; the real end-to-end path is covered by
the SCRIPTO_ENGINE_SMOKE test in test_pipeline_e2e.py.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from scripto.core import pipeline as pl
from scripto.core.errors import OperationStopped
from scripto.core.events import EventBus, StatusEvent
from scripto.core.history import HistoryStore
from scripto.core.jobs import JobStatus
from scripto.engines.base import Segment, TranscribeEngine, TranscribeResult
from scripto.engines.models import get_spec


class StubEngine(TranscribeEngine):
    name = "stub"

    def __init__(self, *, delay: float = 0.0, fail_on: set[str] | None = None,
                 language: str = "en"):
        self.delay = delay
        self.fail_on = fail_on or set()
        self.language = language
        self.loads = 0
        self.released = False
        self.release_order: list[str] = []

    @classmethod
    def is_available(cls) -> bool:
        return True

    def load(self, spec):
        self.loads += 1

    def transcribe(self, audio_path, *, language=None, progress=None, stop_check=None):
        if stop_check is not None and stop_check():
            raise OperationStopped()
        if self.delay:
            time.sleep(self.delay)
        stem = audio_path.stem
        if any(marker in stem for marker in self.fail_on):
            raise RuntimeError("stub transcription failure")
        return TranscribeResult(
            segments=[Segment(0.0, 1.0, f"text for {stem}")],
            language=language or self.language,
            duration=1.0,
        )

    def release(self):
        self.released = True
        self.release_order.append("engine-released")


@pytest.fixture
def fake_extract(monkeypatch):
    """Replace ffmpeg extraction with a fast fake WAV producer."""
    calls: dict = {"delay": 0.0, "fail_on": set(), "started": []}

    def extract(src, *, cache_dir=None, stop_check=None, **_kwargs):
        calls["started"].append(src.name)
        if stop_check is not None and stop_check():
            raise OperationStopped()
        if calls["delay"]:
            time.sleep(calls["delay"])
        if any(marker in src.stem for marker in calls["fail_on"]):
            raise RuntimeError("stub extraction failure")
        wav = (cache_dir or src.parent) / (src.stem + ".wav")
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"RIFFfake")
        return wav

    monkeypatch.setattr(pl.ffmpeg, "extract_audio", extract)
    monkeypatch.setattr(pl.ffmpeg, "cleanup_orphans", lambda *_a, **_k: 0)
    return calls


def make_media(tmp_path, count: int, prefix: str = "clip") -> list:
    files = []
    for i in range(count):
        f = tmp_path / f"{prefix}{i}.mp4"
        f.write_bytes(b"media")
        files.append(f)
    return files


def make_pipeline(tmp_path, engine=None, *, translate_stage=None, memory_mode="balanced",
                  overwrite=False, language=None, bus=None) -> tuple[pl.Pipeline, EventBus]:
    bus = bus or EventBus()
    settings = pl.PipelineSettings(
        model=get_spec("tiny"),
        language=language,
        overwrite=overwrite,
        memory_mode=memory_mode,
        cache_dir=tmp_path / "cache",
        engine_label="stub",
    )
    pipe = pl.Pipeline(
        engine=engine or StubEngine(),
        bus=bus,
        history=HistoryStore(tmp_path / "history.json"),
        settings=settings,
        translate_stage=translate_stage,
    )
    return pipe, bus


def test_outputs_written_next_to_sources(tmp_path, fake_extract):
    files = make_media(tmp_path, 3)
    pipe, _bus = make_pipeline(tmp_path)
    jobs, stats = pipe.run(files, threading.Event())
    assert stats.done == 3 and stats.failed == 0
    for job, src in zip(jobs, files):
        assert job.status == JobStatus.DONE
        expected = src.with_name(src.stem + ".en.srt")
        assert expected.exists()
        assert job.outputs == [expected]
    # temp wavs cleaned up
    assert list((tmp_path / "cache").glob("*.wav")) == []


def test_history_records_every_product(tmp_path, fake_extract):
    files = make_media(tmp_path, 2)
    pipe, _bus = make_pipeline(tmp_path)
    pipe.run(files, threading.Event())
    entries = HistoryStore(tmp_path / "history.json").entries()
    assert len(entries) == 2
    assert all(e.status == "done" and e.model == "tiny" for e in entries)


def test_skip_existing_without_overwrite(tmp_path, fake_extract):
    files = make_media(tmp_path, 2)
    (files[0].parent / (files[0].stem + ".en.srt")).write_text("old", encoding="utf-8")
    pipe, _bus = make_pipeline(tmp_path)
    jobs, stats = pipe.run(files, threading.Event())
    assert stats.skipped == 1 and stats.done == 1
    assert jobs[0].status == JobStatus.SKIPPED
    assert (files[0].parent / (files[0].stem + ".en.srt")).read_text(encoding="utf-8") == "old"


def test_skipped_file_lands_in_history_with_all_its_languages(tmp_path, fake_extract):
    """A video that arrives already subtitled is a history row, not a no-op."""
    (video,) = make_media(tmp_path, 1)
    (tmp_path / f"{video.stem}.srt").write_text("en", encoding="utf-8")
    (tmp_path / f"{video.stem}.zh.srt").write_text("zh", encoding="utf-8")
    (tmp_path / f"{video.stem}.ja.srt").write_text("ja", encoding="utf-8")

    pipe, _bus = make_pipeline(tmp_path)
    jobs, stats = pipe.run([video], threading.Event())
    assert stats.skipped == 1 and jobs[0].status == JobStatus.SKIPPED

    entries = HistoryStore(tmp_path / "history.json").entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.status == "skipped"
    assert {o["lang"]: Path(o["path"]).name for o in entry.outputs} == {
        "en": f"{video.stem}.srt",       # suffix-less file read as English
        "zh": f"{video.stem}.zh.srt",
        "ja": f"{video.stem}.ja.srt",
    }


def test_transcribed_file_reports_subtitles_it_arrived_with(tmp_path, fake_extract):
    (video,) = make_media(tmp_path, 1)
    (tmp_path / f"{video.stem}.zh.srt").write_text("zh", encoding="utf-8")

    # Forced English: auto-detect would treat the Chinese file as "done".
    pipe, _bus = make_pipeline(tmp_path, language="en")
    _jobs, stats = pipe.run([video], threading.Event())
    assert stats.done == 1

    (entry,) = HistoryStore(tmp_path / "history.json").entries()
    langs = {o["lang"] for o in entry.outputs}
    assert langs == {"en", "zh"}          # the one produced plus the one found


def test_failed_job_does_not_claim_unrelated_subtitles(tmp_path, fake_extract):
    (video,) = make_media(tmp_path, 1)
    (tmp_path / f"{video.stem}.zh.srt").write_text("zh", encoding="utf-8")
    pipe, _bus = make_pipeline(tmp_path, StubEngine(fail_on={video.stem}), language="en")
    pipe.run([video], threading.Event())

    (entry,) = HistoryStore(tmp_path / "history.json").entries()
    assert entry.status == "failed" and entry.outputs == []


def test_overwrite_regenerates(tmp_path, fake_extract):
    files = make_media(tmp_path, 1)
    target = files[0].parent / (files[0].stem + ".en.srt")
    target.write_text("old", encoding="utf-8")
    pipe, _bus = make_pipeline(tmp_path, overwrite=True)
    _jobs, stats = pipe.run(files, threading.Event())
    assert stats.done == 1
    assert "text for" in target.read_text(encoding="utf-8")


def test_single_failure_does_not_block_batch(tmp_path, fake_extract):
    files = make_media(tmp_path, 3)
    engine = StubEngine(fail_on={"clip1"})
    pipe, _bus = make_pipeline(tmp_path, engine)
    jobs, stats = pipe.run(files, threading.Event())
    assert stats.done == 2 and stats.failed == 1
    assert jobs[1].status == JobStatus.FAILED
    assert "stub transcription failure" in jobs[1].error
    failed_entries = [e for e in HistoryStore(tmp_path / "history.json").entries() if e.status == "failed"]
    assert len(failed_entries) == 1


def test_extraction_failure_is_isolated_too(tmp_path, fake_extract):
    fake_extract["fail_on"] = {"clip0"}
    files = make_media(tmp_path, 2)
    pipe, _bus = make_pipeline(tmp_path)
    jobs, stats = pipe.run(files, threading.Event())
    assert jobs[0].status == JobStatus.FAILED
    assert "extract" in jobs[0].error
    assert stats.done == 1 and stats.failed == 1


def test_stop_marks_remaining_unprocessed(tmp_path, fake_extract):
    files = make_media(tmp_path, 5)
    engine = StubEngine(delay=0.05)
    pipe, bus = make_pipeline(tmp_path, engine)
    stop = threading.Event()

    done_once = threading.Event()

    def watcher(event):
        if isinstance(event, StatusEvent) and event.status == "done":
            done_once.set()
            stop.set()

    bus.subscribe(watcher)
    started = time.monotonic()
    jobs, stats = pipe.run(files, stop)
    elapsed = time.monotonic() - started

    assert done_once.is_set()
    assert stats.done >= 1
    assert stats.unprocessed >= 1
    assert stats.done + stats.unprocessed == stats.total
    assert all(j.status in (JobStatus.DONE, JobStatus.UNPROCESSED) for j in jobs)
    assert elapsed < 5.0  # back to idle quickly


def test_prefetch_overlaps_extract_and_transcribe(tmp_path, fake_extract):
    """4 files × (0.1s extract + 0.1s transcribe): serial ≈ 0.8s, pipelined ≈ 0.5s."""
    fake_extract["delay"] = 0.1
    files = make_media(tmp_path, 4)
    engine = StubEngine(delay=0.1)
    pipe, _bus = make_pipeline(tmp_path, engine)
    started = time.monotonic()
    _jobs, stats = pipe.run(files, threading.Event())
    elapsed = time.monotonic() - started
    assert stats.done == 4
    assert elapsed < 0.75, f"pipeline did not overlap stages ({elapsed:.2f}s)"


class RecordingStage:
    """Fake translate stage that records when it runs and what it sees."""

    def __init__(self, engine: StubEngine | None = None):
        self.calls: list[str] = []
        self.engine = engine
        self.engine_released_before_first_call: bool | None = None

    def translate(self, srt_path, source, *, stop_check, progress):
        if self.engine is not None and self.engine_released_before_first_call is None:
            self.engine_released_before_first_call = self.engine.released
        self.calls.append(source.name)
        produced = srt_path.with_name(srt_path.name.replace(".en.srt", ".zh.srt"))
        produced.write_text("translated", encoding="utf-8")
        return [produced]


def test_balanced_mode_translates_alongside(tmp_path, fake_extract):
    files = make_media(tmp_path, 3)
    stage = RecordingStage()
    pipe, _bus = make_pipeline(tmp_path, translate_stage=stage)
    jobs, stats = pipe.run(files, threading.Event())
    assert stats.done == 3
    assert len(stage.calls) == 3
    for job in jobs:
        assert any(p.name.endswith(".zh.srt") for p in job.outputs)


def test_low_memory_mode_translates_after_engine_release(tmp_path, fake_extract):
    files = make_media(tmp_path, 2)
    engine = StubEngine()
    stage = RecordingStage(engine)
    pipe, _bus = make_pipeline(tmp_path, engine, translate_stage=stage, memory_mode="low")
    _jobs, stats = pipe.run(files, threading.Event())
    assert stats.done == 2
    assert len(stage.calls) == 2
    assert stage.engine_released_before_first_call is True


def test_translation_failure_never_breaks_transcript(tmp_path, fake_extract):
    files = make_media(tmp_path, 1)

    class ExplodingStage:
        def translate(self, srt_path, source, *, stop_check, progress):
            raise RuntimeError("ollama exploded")

    pipe, _bus = make_pipeline(tmp_path, translate_stage=ExplodingStage())
    jobs, stats = pipe.run(files, threading.Event())
    assert stats.done == 1
    assert jobs[0].status == JobStatus.DONE
    assert "translate" in jobs[0].error
    assert jobs[0].outputs[0].exists()
