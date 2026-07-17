import pytest

from scripto import cli
from scripto.core import doctor
from scripto.core.config import ConfigService


@pytest.fixture
def healthy(monkeypatch):
    """Force every probe healthy; individual tests break one at a time."""
    monkeypatch.setattr("scripto.media.ffmpeg.ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr("scripto.engines.select.available_engines", lambda: ["mlx"])
    monkeypatch.setattr(
        "scripto.engines.select.resolve_engine_name", lambda c="auto": ("mlx", "auto")
    )
    monkeypatch.setattr("scripto.engines.models.installed_keys", lambda e: {"tiny"})
    monkeypatch.setattr(
        "scripto.translate.ollama.OllamaClient.is_reachable", lambda self: True
    )


def run(config=None):
    return doctor.run_doctor(config or {"whisper_model": "tiny", "engine": "auto"})


def test_all_green(healthy):
    results = run()
    assert all(r.ok for r in results)
    assert doctor.doctor_ok(results)


def test_missing_ffmpeg_fails_required(healthy, monkeypatch):
    monkeypatch.setattr("scripto.media.ffmpeg.ffmpeg_path", lambda: None)
    results = run()
    ffmpeg = next(r for r in results if r.key == "ffmpeg")
    assert not ffmpeg.ok and ffmpeg.required and ffmpeg.hint
    assert not doctor.doctor_ok(results)


def test_ollama_down_is_only_a_warning(healthy, monkeypatch):
    monkeypatch.setattr(
        "scripto.translate.ollama.OllamaClient.is_reachable", lambda self: False
    )
    results = run()
    ollama = next(r for r in results if r.key == "ollama")
    assert not ollama.ok and not ollama.required
    assert doctor.doctor_ok(results)  # optional failure doesn't gate


def test_missing_model_is_downloadable_warning(healthy, monkeypatch):
    monkeypatch.setattr("scripto.engines.models.installed_keys", lambda e: set())
    results = run()
    model = next(r for r in results if r.key == "model")
    assert not model.ok and not model.required
    assert doctor.doctor_ok(results)


def test_cli_doctor_exit_codes(healthy, monkeypatch, capsys, tmp_path):
    assert cli.main(["doctor"]) == 0
    assert "✓" in capsys.readouterr().out

    monkeypatch.setattr("scripto.media.ffmpeg.ffmpeg_path", lambda: None)
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "✗" in out
