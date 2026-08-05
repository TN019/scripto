"""Transcript hallucination cleanup — engine-agnostic safety net.

Whisper's failure mode in silence/noise is degenerate repetition: a real
sentence trails off into ``1, 2, 3, 3, 4, 4, ...`` and the next 30-second
window is pure ``9, 9, 9, ...`` (decoding derails inside a window, and
conditioning on previous text carries the loop forward). The engines get
robustness flags too, but whatever still slips through is scrubbed here,
between transcription and output.

Rules, deliberately conservative:
- A trailing run of tokens with almost no variety is cut off the segment.
- A segment that is degenerate wall-to-wall (or CJK char-spam) is dropped.
- Runs of *identical* consecutive segments are capped at two — the
  "Thank you. / Thank you. / Thank you." silence filler.
"""

from __future__ import annotations

import logging
import re

from ..engines.base import Segment

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Loop detection: a window of tokens with almost no vocabulary variety.
# Real speech runs ~10-12 distinct words per 12 tokens; a repetition loop
# (even an "ascending" one like 1,2,3,3,4,4,…) stays at or below 5.
MIN_LOOP_TOKENS = 10
LOOP_WINDOW = 12
LOOP_WINDOW_UNIQUE_MAX = 5
# Spaceless spam (CJK "啊啊啊…"): long text drawn from a tiny alphabet.
MIN_SPAM_CHARS = 30
SPAM_UNIQUE_CHARS = 4
# Identical consecutive segments: keep the first, drop from this count on.
MAX_IDENTICAL_RUN = 2


def trim_repetition_tail(text: str) -> str:
    """Cut the trailing repetition loop off ``text``; '' if it is all loop.

    Walks windows of ``LOOP_WINDOW`` tokens back from the end while their
    vocabulary stays tiny; the leftmost such window marks where the loop
    begins, and the cut lands on that token — right at the boundary between
    real speech (fresh vocabulary) and the loop (recycled vocabulary).
    """
    matches = list(_TOKEN_RE.finditer(text))
    count = len(matches)
    if count < MIN_LOOP_TOKENS:
        return text
    tokens = [m.group().lower() for m in matches]
    window = min(LOOP_WINDOW, count)

    def loopy(i: int) -> bool:
        return len(set(tokens[i:i + window])) <= LOOP_WINDOW_UNIQUE_MAX

    start = count - window
    if not loopy(start):
        return text
    while start > 0 and loopy(start - 1):
        start -= 1
    if count - start < MIN_LOOP_TOKENS:
        return text
    if start == 0:
        return ""
    return text[: matches[start].start()].rstrip(" ,.;:、，。").rstrip()


def is_spam(text: str) -> bool:
    """Long text drawn from a tiny character set (spaceless CJK loops)."""
    chars = [c for c in text if not c.isspace() and _TOKEN_RE.match(c)]
    return len(chars) >= MIN_SPAM_CHARS and len(set(chars)) <= SPAM_UNIQUE_CHARS


def clean_segments(segments: list[Segment]) -> tuple[list[Segment], int]:
    """(cleaned segments, number of segments dropped or trimmed)."""
    cleaned: list[Segment] = []
    touched = 0
    identical_run = 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        trimmed = "" if is_spam(text) else trim_repetition_tail(text)
        if not trimmed:
            touched += 1
            continue
        if cleaned and trimmed == cleaned[-1].text:
            identical_run += 1
            if identical_run >= MAX_IDENTICAL_RUN:
                touched += 1
                continue
        else:
            identical_run = 0
        if trimmed != text:
            touched += 1
        cleaned.append(Segment(start=seg.start, end=seg.end, text=trimmed))
    if touched:
        logger.info("hallucination cleanup: %d segment(s) trimmed or dropped",
                    touched)
    return cleaned, touched
