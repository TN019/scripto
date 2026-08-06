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
    assert out.output_path(src, language="zh", fmt="srt").name == "lecture.zh.srt"
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
    (tmp_path / "lecture.zh.srt").write_text("x", encoding="utf-8")
    assert out.existing_transcript(src, fmt="srt", language=None) is not None


def test_language_from_suffix_reads_names_the_way_we_write_them():
    assert out.language_from_suffix(".zh") == "zh"
    assert out.language_from_suffix(".cn") == "zh"      # alias spelling
    assert out.language_from_suffix(".ENG") == "en"     # case-insensitive
    assert out.language_from_suffix("") == "en"         # no suffix: English
    assert out.language_from_suffix(".fr") == "fr"      # unregistered, well-formed
    assert out.language_from_suffix(".pt-br") == "pt-br"
    assert out.language_from_suffix(".part2") is None   # a filename, not a language
    assert out.language_from_suffix(".final") is None


def test_sibling_transcripts_maps_every_language_beside_the_video(tmp_path):
    src = tmp_path / "lecture.mp4"
    src.write_bytes(b"x")
    for name in ("lecture.srt", "lecture.zh.srt", "lecture.ja.srt",
                 "lecture.part2.srt", "lecture-draft.srt", "other.zh.srt"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    found = out.sibling_transcripts(src, fmt="srt")
    assert {k: v.name for k, v in found.items()} == {
        "en": "lecture.srt",       # no suffix counts as English
        "zh": "lecture.zh.srt",
        "ja": "lecture.ja.srt",
    }
    # Registry order, so callers get a stable first choice.
    assert list(found) == ["en", "zh", "ja"]


def test_sibling_transcripts_ignores_other_formats_and_missing_dirs(tmp_path):
    src = tmp_path / "lecture.mp4"
    (tmp_path / "lecture.zh.srt").write_text("x", encoding="utf-8")
    (tmp_path / "lecture.en.txt").write_text("x", encoding="utf-8")
    assert list(out.sibling_transcripts(src, fmt="srt")) == ["zh"]
    assert list(out.sibling_transcripts(src, fmt="txt")) == ["en"]
    assert out.sibling_transcripts(tmp_path / "gone" / "x.mp4", fmt="srt") == {}


def test_existing_transcript_accepts_a_suffixless_subtitle(tmp_path):
    src = tmp_path / "lecture.mp4"
    (tmp_path / "lecture.srt").write_text("x", encoding="utf-8")
    # The shape subtitles arrive in from elsewhere: no language in the name.
    assert out.existing_transcript(src, fmt="srt", language=None) is not None
    assert out.existing_transcript(src, fmt="srt", language="en") is not None
    assert out.existing_transcript(src, fmt="srt", language="zh") is None


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


def test_existing_transcript_accepts_alias_suffixes(tmp_path):
    from scripto.core.output import existing_transcript

    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"x")
    (tmp_path / "lecture.cn.srt").write_text("1\n", encoding="utf-8")

    forced = existing_transcript(source, fmt="srt", language="zh")
    assert forced is not None and forced.name == "lecture.cn.srt"
    auto = existing_transcript(source, fmt="srt", language=None)
    assert auto is not None and auto.name == "lecture.cn.srt"
    assert existing_transcript(source, fmt="srt", language="ja") is None
