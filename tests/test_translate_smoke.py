"""Real Ollama translation smoke: opt-in via SCRIPTO_OLLAMA_SMOKE=1.

Needs a running local Ollama with the configured model (default qwen3:4b for
speed). Verifies the full stage against a real model: structure preserved and
the output actually contains CJK text.
"""

from __future__ import annotations

import os

import pytest

from scripto.translate import srt
from scripto.translate.ollama import OllamaClient
from scripto.translate.stage import OllamaTranslateStage

smoke = pytest.mark.skipif(
    not os.environ.get("SCRIPTO_OLLAMA_SMOKE"),
    reason="set SCRIPTO_OLLAMA_SMOKE=1 to run the real Ollama translation smoke",
)

SAMPLE = """1
00:00:00,000 --> 00:00:02,000
Hello everyone, welcome to the lecture.

2
00:00:02,000 --> 00:00:05,000
Today we will talk about operating systems.

3
00:00:05,000 --> 00:00:08,000
Let's get started.
"""


def has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


@smoke
def test_real_ollama_translation(tmp_path):
    client = OllamaClient()
    if not client.is_reachable():
        pytest.skip("local Ollama is not running")
    model = os.environ.get("SCRIPTO_OLLAMA_MODEL", "qwen3:4b")
    if model not in client.list_models():
        pytest.skip(f"model {model} not pulled locally")

    stage = OllamaTranslateStage(client, model=model, target="zh")
    src = tmp_path / "lecture.mp4"
    srt_path = tmp_path / "lecture.en.srt"
    srt_path.write_text(SAMPLE, encoding="utf-8")

    produced = stage.translate(srt_path, src, stop_check=None, progress=None)
    assert produced == [tmp_path / "lecture.zh.srt"]

    blocks = srt.parse_srt(produced[0].read_text(encoding="utf-8"))
    original = srt.parse_srt(SAMPLE)
    assert [b.index for b in blocks] == [b.index for b in original]
    assert [b.timestamp for b in blocks] == [b.timestamp for b in original]
    assert any(has_cjk(b.text) for b in blocks), [b.text for b in blocks]

    stage.release()
