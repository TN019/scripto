import subprocess
import wave

import pytest

from scripto.core.errors import OperationStopped, ScriptoError
from scripto.media import ffmpeg as ff

pytestmark = pytest.mark.skipif(
    ff.ffmpeg_path() is None, reason="ffmpeg not installed"
)


@pytest.fixture
def sample_media(tmp_path):
    """A 1-second AAC tone in an m4a container."""
    out = tmp_path / "tone.m4a"
    subprocess.run(
        [
            ff.ffmpeg_path(), "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=1",
            "-c:a", "aac", str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def test_extract_produces_16k_mono_wav(sample_media, tmp_path):
    cache = tmp_path / "cache"
    wav_path = ff.extract_audio(sample_media, cache_dir=cache)
    try:
        with wave.open(str(wav_path)) as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getnframes() > 0
        assert not list(cache.glob("*.stderr"))
    finally:
        wav_path.unlink(missing_ok=True)


def test_unreadable_input_raises_readable_error_and_cleans_up(tmp_path):
    fake = tmp_path / "fake.mp4"
    fake.write_text("this is not media", encoding="utf-8")
    cache = tmp_path / "cache"
    with pytest.raises(ScriptoError) as excinfo:
        ff.extract_audio(fake, cache_dir=cache)
    assert "fake.mp4" in str(excinfo.value)
    assert excinfo.value.key == "errors.ffmpeg_failed"
    assert list(cache.iterdir()) == []


class _NeverEndingProc:
    """Fake Popen: runs until killed (deterministic stop/timeout tests)."""

    def __init__(self):
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_stop_check_kills_process_and_cleans_up(sample_media, tmp_path, monkeypatch):
    proc = _NeverEndingProc()
    monkeypatch.setattr(ff.subprocess, "Popen", lambda *a, **k: proc)
    cache = tmp_path / "cache"
    with pytest.raises(OperationStopped):
        ff.extract_audio(sample_media, cache_dir=cache, stop_check=lambda: True)
    assert proc.killed
    assert list(cache.iterdir()) == []


def test_timeout_kills_process_and_cleans_up(sample_media, tmp_path, monkeypatch):
    proc = _NeverEndingProc()
    monkeypatch.setattr(ff.subprocess, "Popen", lambda *a, **k: proc)
    cache = tmp_path / "cache"
    with pytest.raises(ScriptoError) as excinfo:
        ff.extract_audio(sample_media, cache_dir=cache, timeout=0.0)
    assert excinfo.value.key == "errors.extract_timeout"
    assert proc.killed
    assert list(cache.iterdir()) == []


def test_missing_ffmpeg_raises_install_hint(monkeypatch):
    monkeypatch.setattr(ff.shutil, "which", lambda _name: None)
    with pytest.raises(ScriptoError) as excinfo:
        ff.require_ffmpeg()
    assert excinfo.value.key == "errors.ffmpeg_missing"
    assert ff.install_hint() in str(excinfo.value)


def test_cleanup_orphans(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "b.stderr").write_bytes(b"")
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    assert ff.cleanup_orphans(tmp_path) == 2
    assert (tmp_path / "keep.txt").exists()
