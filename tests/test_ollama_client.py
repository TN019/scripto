import urllib.error

import pytest

from scripto.core.errors import OperationStopped, ScriptoError
from scripto.translate.ollama import (
    MAX_NUM_CTX,
    MIN_NUM_CTX,
    OllamaClient,
    required_num_ctx,
)


def test_num_ctx_scales_with_prompt_and_clamps():
    assert required_num_ctx("short") == MIN_NUM_CTX
    medium = required_num_ctx("x" * 20_000)
    assert MIN_NUM_CTX < medium < MAX_NUM_CTX
    assert medium % 1024 == 0
    assert required_num_ctx("x" * 500_000) == MAX_NUM_CTX


def make_client(stream_factory):
    client = OllamaClient("http://fake:11434")
    client._post_stream = stream_factory  # type: ignore[method-assign]
    return client


def test_generate_concatenates_stream_and_sets_options():
    captured = {}

    def fake_stream(path, payload, timeout=None):
        captured.update(payload)
        yield {"response": "你"}
        yield {"response": "好"}
        yield {"done": True}

    client = make_client(fake_stream)
    text = client.generate("hello", model="qwen3:8b")
    assert text == "你好"
    assert captured["options"]["num_ctx"] == required_num_ctx("hello")
    assert captured["think"] is False
    assert captured["keep_alive"] == "10m"


def test_generate_retries_without_think_on_400():
    calls = []

    def fake_stream(path, payload, timeout=None):
        calls.append(dict(payload))
        if "think" in payload:
            raise urllib.error.HTTPError("u", 400, "bad", None, None)
        yield {"response": "ok"}

    client = make_client(fake_stream)
    assert client.generate("hi", model="m") == "ok"
    assert "think" in calls[0] and "think" not in calls[1]


def test_generate_stop_mid_stream():
    def fake_stream(path, payload, timeout=None):
        yield {"response": "a"}
        yield {"response": "b"}

    client = make_client(fake_stream)
    calls = {"n": 0}

    def stop_after_first():
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(OperationStopped):
        client.generate("hi", model="m", stop_check=stop_after_first)


def test_generate_surfaces_ollama_error_payload():
    def fake_stream(path, payload, timeout=None):
        yield {"error": "model not found"}

    client = make_client(fake_stream)
    with pytest.raises(ScriptoError) as excinfo:
        client.generate("hi", model="missing")
    assert "model not found" in str(excinfo.value)


def test_unreachable_wrapped_as_scripto_error():
    def fake_stream(path, payload, timeout=None):
        raise urllib.error.URLError("connection refused")
        yield  # pragma: no cover

    client = make_client(fake_stream)
    with pytest.raises(ScriptoError) as excinfo:
        client.generate("hi", model="m")
    assert excinfo.value.key == "errors.ollama_unreachable"


def test_pull_reports_progress_and_errors():
    events = []

    def fake_stream(path, payload, timeout=None):
        yield {"status": "pulling layer", "total": 100, "completed": 50}
        yield {"status": "success"}

    client = make_client(fake_stream)
    client.pull("m", progress=lambda detail, frac: events.append((detail, frac)))
    assert events[0] == ("pulling layer", 0.5)

    def failing_stream(path, payload, timeout=None):
        yield {"error": "no such model"}

    client = make_client(failing_stream)
    with pytest.raises(ScriptoError):
        client.pull("m")
