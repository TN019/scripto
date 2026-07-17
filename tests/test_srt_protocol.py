from scripto.translate import srt

SAMPLE = """1
00:00:00,000 --> 00:00:02,000
Hello there.

2
00:00:02,000 --> 00:00:04,500
Second line
spanning two rows.

3
00:00:04,500 --> 00:00:06,000
Third.
"""


def test_parse_and_rebuild_preserves_structure():
    blocks = srt.parse_srt(SAMPLE)
    assert len(blocks) == 3
    assert blocks[1].text == "Second line\nspanning two rows."
    rebuilt = srt.build_srt(blocks)
    assert srt.parse_srt(rebuilt) == blocks
    assert rebuilt.count("-->") == 3


def test_parse_tolerates_bom_crlf_and_extra_blanks():
    messy = "﻿" + SAMPLE.replace("\n", "\r\n").replace("\r\n\r\n", "\r\n\r\n\r\n")
    blocks = srt.parse_srt(messy)
    assert len(blocks) == 3
    assert blocks[0].timestamp == "00:00:00,000 --> 00:00:02,000"


def test_parse_skips_garbage_blocks():
    content = SAMPLE + "\nnot a real block\n"
    assert len(srt.parse_srt(content)) == 3


def test_batch_ranges_respect_count_and_chars():
    texts = ["x" * 10] * 10
    assert srt.batch_ranges(texts, max_blocks=4, max_chars=1000) == [(0, 4), (4, 8), (8, 10)]
    assert srt.batch_ranges(texts, max_blocks=100, max_chars=25) == [
        (0, 2), (2, 4), (4, 6), (6, 8), (8, 10),
    ]
    assert srt.batch_ranges([]) == []


def test_marker_prompt_and_response_roundtrip():
    texts = ["Hello.", "World."]
    prompt = srt.build_marker_prompt(texts, "Simplified Chinese")
    assert "[[1]]" in prompt and "[[2]]" in prompt
    response = "[[1]]\n你好。\n[[2]]\n世界。"
    assert srt.parse_marker_response(response, 2) == ["你好。", "世界。"]


def test_marker_response_strips_think_noise():
    response = "<think>let me consider…</think>[[1]]\n译文一\n[[2]]\n译文二"
    assert srt.parse_marker_response(response, 2) == ["译文一", "译文二"]


def test_strip_think_handles_closing_tag_only():
    # qwen3 on older Ollama: reasoning as plain text + lone closing tag
    response = "Okay, so I need to translate this.\nLet me think.\n</think>\n\n你好，世界。"
    assert srt.strip_think(response) == "你好，世界。"
    assert srt.parse_marker_response(
        "reasoning text…</think>[[1]]\n你好", 1
    ) == ["你好"]


def test_marker_response_missing_and_blank_are_none():
    assert srt.parse_marker_response("[[1]]\nonly one", 3) == ["only one", None, None]
    assert srt.parse_marker_response("[[1]]\n\n[[2]]\nok", 2) == [None, "ok"]
