from pathlib import Path

import pytest

from scripto.core.errors import OperationStopped
from scripto.translate import srt
from scripto.translate.stage import OllamaTranslateStage

SAMPLE = """1
00:00:00,000 --> 00:00:02,000
Hello there.

2
00:00:02,000 --> 00:00:04,000
How are you?

3
00:00:04,000 --> 00:00:06,000
Goodbye.
"""


class FakeClient:
    """Translates by wrapping text in 「」; can fail batches on demand."""

    def __init__(self, *, fail_batches: int = 0, fail_single_for: set[str] | None = None):
        self.fail_batches = fail_batches
        self.fail_single_for = fail_single_for or set()
        self.calls: list[int] = []  # marker count per batched call (0 = single)
        self.unloaded = False

    def generate(self, prompt, *, model, keep_alive="10m", stop_check=None):
        import re

        markers = re.findall(r"\[\[(\d+)\]\]", prompt)
        if markers:
            self.calls.append(len(markers))
            if self.fail_batches > 0 and len(markers) > 1:
                self.fail_batches -= 1
                return "[[1]]\ngarbled"  # misaligned → BatchAlignmentError
            lines = []
            texts = self._texts_from_prompt(prompt)
            for i, text in enumerate(texts, start=1):
                lines.append(f"[[{i}]]")
                lines.append(f"「{text}」")
            return "\n".join(lines)
        self.calls.append(0)
        text = prompt.rsplit("\n\n", 1)[-1]
        if text in self.fail_single_for:
            raise RuntimeError("single failed")
        return f"「{text}」"

    @staticmethod
    def _texts_from_prompt(prompt: str) -> list[str]:
        import re

        parts = re.split(r"\[\[\d+\]\]\n", prompt)
        return [p.strip() for p in parts[1:]]

    def unload(self, model):
        self.unloaded = True


def make_stage(client, tmp_path, **kwargs) -> tuple[OllamaTranslateStage, Path, Path]:
    stage = OllamaTranslateStage(client, model="fake", target="zh", **kwargs)
    src = tmp_path / "talk.mp4"
    srt_path = tmp_path / "talk.en.srt"
    srt_path.write_text(SAMPLE, encoding="utf-8")
    return stage, src, srt_path


def test_happy_path_preserves_structure(tmp_path):
    client = FakeClient()
    stage, src, srt_path = make_stage(client, tmp_path)
    produced = stage.translate(srt_path, src, stop_check=None, progress=None)
    assert produced == [tmp_path / "talk.cn.srt"]
    blocks = srt.parse_srt(produced[0].read_text(encoding="utf-8"))
    original = srt.parse_srt(SAMPLE)
    assert len(blocks) == 3
    for got, ref in zip(blocks, original):
        assert got.index == ref.index
        assert got.timestamp == ref.timestamp
        assert got.text == f"「{ref.text}」"


def test_batch_failure_splits_then_single_fallback(tmp_path):
    client = FakeClient(fail_batches=10)  # every multi-block batch misaligns
    stage, src, srt_path = make_stage(client, tmp_path)
    produced = stage.translate(srt_path, src, stop_check=None, progress=None)
    blocks = srt.parse_srt(produced[0].read_text(encoding="utf-8"))
    assert [b.text for b in blocks] == ["「Hello there.」", "「How are you?」", "「Goodbye.」"]
    assert 0 in client.calls  # ended up in single-block prompts


def test_single_failure_keeps_original_text(tmp_path):
    client = FakeClient(fail_batches=10, fail_single_for={"How are you?"})
    stage, src, srt_path = make_stage(client, tmp_path)
    produced = stage.translate(srt_path, src, stop_check=None, progress=None)
    blocks = srt.parse_srt(produced[0].read_text(encoding="utf-8"))
    assert blocks[1].text == "How are you?"  # never break the file
    assert blocks[0].text == "「Hello there.」"


def test_existing_output_skipped_without_overwrite(tmp_path):
    client = FakeClient()
    stage, src, srt_path = make_stage(client, tmp_path)
    existing = tmp_path / "talk.cn.srt"
    existing.write_text("old", encoding="utf-8")
    produced = stage.translate(srt_path, src, stop_check=None, progress=None)
    assert produced == [existing]
    assert existing.read_text(encoding="utf-8") == "old"
    assert client.calls == []


def test_overwrite_regenerates(tmp_path):
    client = FakeClient()
    stage, src, srt_path = make_stage(client, tmp_path, overwrite=True)
    (tmp_path / "talk.cn.srt").write_text("old", encoding="utf-8")
    produced = stage.translate(srt_path, src, stop_check=None, progress=None)
    assert "「" in produced[0].read_text(encoding="utf-8")


def test_stop_at_batch_boundary(tmp_path):
    client = FakeClient()
    stage, src, srt_path = make_stage(client, tmp_path, batch_blocks=1)
    calls = {"n": 0}

    def stop_after_first_batch():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(OperationStopped):
        stage.translate(srt_path, src, stop_check=stop_after_first_batch, progress=None)
    assert not (tmp_path / "talk.cn.srt").exists()  # no half-written output


def test_progress_reports_blocks(tmp_path):
    client = FakeClient()
    stage, src, srt_path = make_stage(client, tmp_path, batch_blocks=2)
    ticks: list[tuple[int, int]] = []
    stage.translate(srt_path, src, stop_check=None, progress=lambda d, t: ticks.append((d, t)))
    assert ticks == [(2, 3), (3, 3)]


def test_same_language_transcript_is_noop(tmp_path):
    client = FakeClient()
    stage = OllamaTranslateStage(client, model="fake", target="en")
    src = tmp_path / "talk.mp4"
    srt_path = tmp_path / "talk.en.srt"
    srt_path.write_text(SAMPLE, encoding="utf-8")
    assert stage.translate(srt_path, src, stop_check=None, progress=None) == []
    assert client.calls == []


def test_release_unloads_model(tmp_path):
    client = FakeClient()
    stage, _src, _srt = make_stage(client, tmp_path)
    stage.release()
    assert client.unloaded
