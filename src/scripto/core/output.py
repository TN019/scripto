"""Output rules and writers (R4).

Naming: every product carries a language suffix — ``lecture.en.srt`` for an
English transcript, ``lecture.zh.srt`` for the Chinese translation. The suffix
map is configurable; a language with no mapping gets ``.<code>`` so new
languages work without code changes (R3).

Reading that naming back is just as important: a video usually arrives with
subtitles beside it, and those are named the same way by everyone else too.
``sibling_transcripts`` finds them and names their languages, so an already
subtitled file is recognised for what it is instead of re-transcribed, and
history can show every language the file actually has.

Location: next to the source file by default; a user-chosen export directory
collects outputs instead (filename collisions across source folders get a
numeric disambiguator).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..engines.base import TranscribeResult
from .languages import alias_suffixes, known_languages, suffix_map

DEFAULT_SUFFIXES = suffix_map()  # single source of truth: core/languages.py
FORMATS = ("srt", "txt", "vtt", "json")

# ``lecture.srt`` next to ``lecture.mp4`` — no language in the name at all —
# is how subtitles ship from everywhere that isn't us, and they are English
# nearly every time. Better a named default than an "unknown" bucket.
UNSUFFIXED_LANGUAGE = "en"

# What we accept in the language position: "zh", "eng", "pt-BR". Anything
# else there (``lecture.part2.srt``, ``lecture.final.srt``) is part of the
# file's name, not a language, and must not be read as one.
_LANG_TAG_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z]{2,4})?$")


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


def language_from_suffix(
    suffix: str, suffix_map: dict[str, str] | None = None
) -> str | None:
    """The language a filename's middle suffix names, or None if it names none.

    ``.zh`` → ``zh``, an alias like ``.cn`` → ``zh``, nothing at all → English
    (see ``UNSUFFIXED_LANGUAGE``), a well-formed but unregistered tag → itself
    (writing does the mirror image: an unknown code becomes ``.<code>``), and
    ``.part2`` → None, because that is a filename, not a language.
    """
    tag = suffix.lstrip(".").lower()
    if not tag:
        return UNSUFFIXED_LANGUAGE
    for code, mapped in (suffix_map or DEFAULT_SUFFIXES).items():
        if tag == mapped.lstrip(".").lower():
            return code
    for spec in known_languages():
        if any(tag == alias.lstrip(".").lower() for alias in spec.aliases):
            return spec.code
    return tag if _LANG_TAG_RE.match(tag) else None


def sibling_transcripts(
    source: Path,
    *,
    fmt: str,
    suffix_map: dict[str, str] | None = None,
    export_dir: Path | None = None,
) -> dict[str, Path]:
    """Language code → same-stem transcript already on disk beside ``source``.

    Matches ``<stem>.<fmt>`` and ``<stem>.<lang>.<fmt>`` — one directory
    listing, ordered by the language registry so callers get a stable
    preference rather than whatever order the filesystem hands back.
    """
    directory = export_dir if export_dir is not None else source.parent
    prefix, tail = source.stem, f".{fmt}"
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return {}

    found: dict[str, Path] = {}
    for path in entries:
        name = path.name
        if not name.startswith(prefix) or not name.lower().endswith(tail.lower()):
            continue
        middle = name[len(prefix):len(name) - len(tail)]
        if middle and not middle.startswith("."):
            continue  # `lecture-draft.srt` is a different file, not a language
        language = language_from_suffix(middle, suffix_map)
        if language is None or language in found:
            continue
        try:
            if path.is_file():
                found[language] = path
        except OSError:
            continue

    order = {spec.code: i for i, spec in enumerate(known_languages())}
    return dict(
        sorted(found.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))
    )


def existing_transcript(
    source: Path,
    *,
    fmt: str,
    language: str | None,
    suffix_map: dict[str, str] | None = None,
    export_dir: Path | None = None,
) -> Path | None:
    """Skip-check before transcribing (overwrite=False).

    With a forced language the exact path is checked, plus that language's
    alias spellings — and, for English, the suffix-less name. With auto-detect
    the language isn't known yet, so any sibling transcript counts as done.
    """
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
        if language == UNSUFFIXED_LANGUAGE:
            bare = directory / f"{source.stem}.{fmt}"
            if bare.exists():
                return bare
        return None
    found = sibling_transcripts(
        source, fmt=fmt, suffix_map=suffix_map, export_dir=export_dir
    )
    return next(iter(found.values()), None)


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
