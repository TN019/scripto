import json

from scripto.core import output as out
from scripto.engines.base import Segment, TranscribeResult


def _result():
    return TranscribeResult(
        segments=[
            Segment(0.0, 1.5, "Hello there."),
            Segment(1.5, 3.0, "Second line."),
        ],
        language="en",
        duration=3.0,
    )


def test_language_suffix_mapping(tmp_path):
    src = tmp_path / "lecture.mp4"
    assert out.output_path(src, language="en", fmt="srt").name == "lecture.en.srt"
    assert out.output_path(src, language="zh", fmt="srt").name == "lecture.cn.srt"
    # Unmapped language still works (R3 extensibility).
    assert out.output_path(src, language="ja", fmt="srt").name == "lecture.ja.srt"


def test_output_next_to_source_by_default(tmp_path):
    src = tmp_path / "deep" / "lecture.mp4"
    src.parent.mkdir()
    assert out.output_path(src, language="en", fmt="srt").parent == src.parent


def test_export_dir_collects_and_disambiguates(tmp_path):
    export = tmp_path / "export"
    src_a = tmp_path / "a" / "talk.mp4"
    src_b = tmp_path / "b" / "talk.mp4"
    first = out.output_path(src_a, language="en", fmt="srt", export_dir=export)
    assert first == export / "talk.en.srt"
    first.write_text("x", encoding="utf-8")
    second = out.output_path(src_b, language="en", fmt="srt", export_dir=export)
    assert second == export / "talk-1.en.srt"


def test_existing_transcript_forced_language(tmp_path):
    src = tmp_path / "lecture.mp4"
    (tmp_path / "lecture.en.srt").write_text("x", encoding="utf-8")
    assert out.existing_transcript(src, fmt="srt", language="en") is not None
    assert out.existing_transcript(src, fmt="srt", language="zh") is None


def test_existing_transcript_auto_checks_known_suffixes(tmp_path):
    src = tmp_path / "lecture.mp4"
    assert out.existing_transcript(src, fmt="srt", language=None) is None
    (tmp_path / "lecture.cn.srt").write_text("x", encoding="utf-8")
    assert out.existing_transcript(src, fmt="srt", language=None) is not None


def test_srt_writer_structure(tmp_path):
    target = tmp_path / "o.srt"
    out.write_result(_result(), target, "srt")
    text = target.read_text(encoding="utf-8")
    blocks = [b for b in text.split("\n\n") if b.strip()]
    assert len(blocks) == 2
    assert blocks[0].splitlines() == [
        "1",
        "00:00:00,000 --> 00:00:01,500",
        "Hello there.",
    ]


def test_vtt_txt_json_writers(tmp_path):
    result = _result()
    out.write_result(result, tmp_path / "o.vtt", "vtt")
    assert (tmp_path / "o.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    out.write_result(result, tmp_path / "o.txt", "txt")
    assert "Hello there. Second line." in (tmp_path / "o.txt").read_text(encoding="utf-8")
    out.write_result(result, tmp_path / "o.json", "json")
    payload = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert payload["language"] == "en"
    assert len(payload["segments"]) == 2
