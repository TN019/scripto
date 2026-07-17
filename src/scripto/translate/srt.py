"""SRT parsing/rebuilding and the marker batch protocol.

Guarantees:
- Rebuild preserves structure exactly: block count, indices, timestamps and
  blank-line layout are untouched; only text is replaced.
- Parsing is tolerant of real-world files: BOM, extra blank lines, CRLF.
- The marker protocol ([[n]] lines) survives model noise: <think> blocks are
  stripped, missing/blank markers come back as None so the caller can retry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BLOCK_SEP = re.compile(r"\n\s*\n")
_MARKER_RE = re.compile(r"\[\[(\d+)\]\]")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

DEFAULT_BATCH_BLOCKS = 40
DEFAULT_BATCH_MAX_CHARS = 3000


@dataclass
class SrtBlock:
    index: str
    timestamp: str
    text: str


def parse_srt(content: str) -> list[SrtBlock]:
    content = content.lstrip("﻿").replace("\r\n", "\n")
    blocks: list[SrtBlock] = []
    for raw in _BLOCK_SEP.split(content.strip()):
        lines = raw.splitlines()
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        blocks.append(
            SrtBlock(
                index=lines[0].strip(),
                timestamp=lines[1].strip(),
                text="\n".join(lines[2:]).strip(),
            )
        )
    return blocks


def build_srt(blocks: list[SrtBlock]) -> str:
    out: list[str] = []
    for block in blocks:
        out.extend([block.index, block.timestamp, block.text, ""])
    return "\n".join(out)


def batch_ranges(
    texts: list[str],
    max_blocks: int = DEFAULT_BATCH_BLOCKS,
    max_chars: int = DEFAULT_BATCH_MAX_CHARS,
) -> list[tuple[int, int]]:
    """[start, end) ranges respecting both the block-count and char limits."""
    ranges: list[tuple[int, int]] = []
    start = 0
    count = 0
    chars = 0
    for i, text in enumerate(texts):
        length = len(text)
        if count > 0 and (count >= max_blocks or chars + length > max_chars):
            ranges.append((start, i))
            start, count, chars = i, 0, 0
        count += 1
        chars += length
    if count > 0:
        ranges.append((start, len(texts)))
    return ranges


def build_marker_prompt(texts: list[str], target_language: str) -> str:
    lines = [
        "You are a professional subtitle translator.",
        f"Translate each marked subtitle segment into {target_language}.",
        "Rules:",
        "- Keep every marker line such as [[1]] exactly as-is, on its own line.",
        "- Put the translation on the line(s) right after its marker.",
        "- Translate ONLY the text. Do not add notes, explanations or extra markers.",
        "- Keep the translation natural, concise and faithful.",
        f"- If a segment is already in {target_language}, keep it unchanged.",
        "- Output the same number of markers you received, in the same order.",
        "",
    ]
    for i, text in enumerate(texts, start=1):
        lines.append(f"[[{i}]]")
        lines.append(text)
    return "\n".join(lines)


def build_single_prompt(text: str, target_language: str) -> str:
    return (
        f"Translate the following subtitle text into {target_language}. "
        "Output only the translation, with no notes or quotes.\n\n" + text
    )


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text)
    # Some reasoning models (qwen3 via older Ollama) emit their thinking as
    # plain text terminated by a lone closing tag — keep only what follows.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


def parse_marker_response(response: str, count: int) -> list[str | None]:
    """Map [[n]] markers back to texts; None for missing/blank entries."""
    response = strip_think(response)
    parts = _MARKER_RE.split(response)
    mapping: dict[int, str] = {}
    it = iter(parts[1:])
    for number, text in zip(it, it):
        try:
            cleaned = text.strip()
            if cleaned:
                mapping[int(number)] = cleaned
        except ValueError:
            continue
    return [mapping.get(i) for i in range(1, count + 1)]
