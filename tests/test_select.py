import pytest

from scripto.core.errors import ScriptoError
from scripto.engines import select
from scripto.engines.fw_engine import FasterWhisperEngine
from scripto.engines.mlx_engine import MlxWhisperEngine


def _force_availability(monkeypatch, *, mlx: bool, fw: bool):
    monkeypatch.setattr(
        MlxWhisperEngine, "is_available", classmethod(lambda cls: mlx)
    )
    monkeypatch.setattr(
        FasterWhisperEngine, "is_available", classmethod(lambda cls: fw)
    )


def test_auto_prefers_mlx_on_apple_silicon(monkeypatch):
    _force_availability(monkeypatch, mlx=True, fw=True)
    name, reason = select.resolve_engine_name("auto")
    assert name == "mlx"
    assert "Apple Silicon" in reason


def test_auto_falls_back_to_faster_whisper(monkeypatch):
    _force_availability(monkeypatch, mlx=False, fw=True)
    name, _reason = select.resolve_engine_name("auto")
    assert name == "faster-whisper"


def test_forced_engine_is_respected(monkeypatch):
    _force_availability(monkeypatch, mlx=True, fw=True)
    name, reason = select.resolve_engine_name("faster-whisper")
    assert name == "faster-whisper"
    assert reason == "forced by config"


def test_forced_unavailable_falls_back_with_warning(monkeypatch, caplog):
    _force_availability(monkeypatch, mlx=False, fw=True)
    with caplog.at_level("WARNING"):
        name, _reason = select.resolve_engine_name("mlx")
    assert name == "faster-whisper"
    assert any("falling back" in r.message for r in caplog.records)


def test_no_engine_available_raises(monkeypatch):
    _force_availability(monkeypatch, mlx=False, fw=False)
    with pytest.raises(ScriptoError) as excinfo:
        select.resolve_engine_name("auto")
    assert excinfo.value.key == "errors.no_engine"


def test_create_engine_returns_instance(monkeypatch):
    _force_availability(monkeypatch, mlx=False, fw=True)
    engine = select.create_engine("auto")
    assert isinstance(engine, FasterWhisperEngine)
