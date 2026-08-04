"""Output rules and writers (R4).

Naming: every product carries a language suffix — ``lecture.en.srt`` for an
English transcript, ``lecture.zh.srt`` for the Chinese translation. The suffix
map is configurable; a language with no mapping gets ``.<code>`` so new
languages work without code changes (R3).

Location: next to the source file by default; a user-chosen export directory
collects outputs instead (filename collisions across source folders get a
numeric disambiguator).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..engines.base import TranscribeResult
from .languages import alias_suffixes, known_languages, suffix_map

DEFAULT_SUFFIXES = suffix_map()  # single source of truth: core/languages.py
FORMATS = ("srt", "txt", "vtt", "json")


def lang_suffix(language: str | None, suffix_map: dict[str, str] | None = None) -> str:
    suffixes = suffix_map or DEFAULT_SUFFIXES
    if not language:
        return ""
    return suffixes.get(language, f".{language}")


def output_path(
    source: Path,
    *,
    language: str | None,
    fmt: str,
    suffix_map: dict[str, str] | None = None,
    export_dir: Path | None = None,
) -> Path:
    name = f"{source.stem}{lang_suffix(language, suffix_map)}.{fmt}"
    if export_dir is None:
        return source.with_name(name)
    export_dir.mkdir(parents=True, exist_ok=True)
    candidate = export_dir / name
    counter = 1
    while candidate.exists() and counter < 1000:
        candidate = export_dir / f"{source.stem}-{counter}{lang_suffix(language, suffix_map)}.{fmt}"
        counter += 1
    return candidate


def existing_transcript(
    source: Path,
    *,
    fmt: str,
    language: str | None,
    suffix_map: dict[str, str] | None = None,
    export_dir: Path | None = None,
) -> Path | None:
    """Skip-check before transcribing (overwrite=False).

    With a forced language the exact path is checked. With auto-detect the
    language isn't known yet, so any known-suffix sibling counts as done.
    Export-dir runs always re-check the exact default name only.
    """
    suffixes = suffix_map or DEFAULT_SUFFIXES
    directory = export_dir if export_dir is not None else source.parent
    if language:
        path = output_path(
            source, language=language, fmt=fmt,
            suffix_map=suffix_map, export_dir=export_dir,
        )
        if path.exists():
            return path
        for suffix in alias_suffixes(language):
            candidate = directory / f"{source.stem}{suffix}.{fmt}"
            if candidate.exists():
                return candidate
        return None
    detect = list(suffixes.values())
    detect += [a for spec in known_languages() for a in spec.aliases]
    for suffix in detect:
        candidate = directory / f"{source.stem}{suffix}.{fmt}"
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _timestamp(seconds: float, *, comma: bool) -> str:
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def write_result(result: TranscribeResult, out_path: Path, fmt: str) -> None:
    if fmt == "srt":
        lines: list[str] = []
        for i, seg in enumerate(result.segments, start=1):
            lines += [
                str(i),
                f"{_timestamp(seg.start, comma=True)} --> {_timestamp(seg.end, comma=True)}",
                seg.text,
                "",
            ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
    elif fmt == "vtt":
        lines = ["WEBVTT", ""]
        for seg in result.segments:
            lines += [
                f"{_timestamp(seg.start, comma=False)} --> {_timestamp(seg.end, comma=False)}",
                seg.text,
                "",
            ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
    elif fmt == "txt":
        out_path.write_text(result.text + "\n", encoding="utf-8")
    elif fmt == "json":
        payload = {
            "language": result.language,
            "duration": result.duration,
            "segments": [
                {"start": seg.start, "end": seg.end, "text": seg.text}
                for seg in result.segments
            ],
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        raise ValueError(f"unknown format: {fmt}")
