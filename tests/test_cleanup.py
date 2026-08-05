"""Hallucination cleanup: repetition tails, spam segments, identical runs.

The nasty inputs are taken from a real lecture transcript where whisper
derailed in silence ("…assignment 1, 2, 3, 3, 3, 4, 4, …" followed by a
full 30-second window of "9, 9, 9, …").
"""

from scripto.core.cleanup import clean_segments, is_spam, trim_repetition_tail
from scripto.engines.base import Segment

REAL_SPEECH = (
    "resources that you can use for your assignment. And so these days in "
    "every subject talks about what about AI use policy. I would like you "
    "to do programming yourself. Don't use help of AI to do assignment"
)
LOOP_TAIL = " 1, 2, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 7, 7, " + \
    ", ".join(["8"] * 40) + ", " + ", ".join(["9"] * 30)
PURE_LOOP = ", ".join(["9"] * 111)


def seg(text: str, start: float = 0.0) -> Segment:
    return Segment(start=start, end=start + 30.0, text=text)


def test_trailing_loop_is_cut_but_speech_kept():
    cleaned = trim_repetition_tail(REAL_SPEECH + LOOP_TAIL)
    assert cleaned.startswith("resources that you can use")
    assert "programming yourself" in cleaned
    assert "9" not in cleaned and "8, 8" not in cleaned


def test_pure_loop_segment_is_dropped():
    segments, touched = clean_segments([seg(REAL_SPEECH + LOOP_TAIL), seg(PURE_LOOP)])
    assert len(segments) == 1
    assert touched == 2
    assert segments[0].text.startswith("resources")


def test_normal_speech_is_untouched():
    texts = [
        "One, two, three, only a few.",           # legit short enumeration
        "So each tutorial has 30 students approximately 30 28 to 30.",
        "So, so my best consulting time would be just after the class.",
    ]
    segments, touched = clean_segments([seg(t) for t in texts])
    assert [s.text for s in segments] == texts
    assert touched == 0


def test_identical_runs_are_capped_at_two():
    segments, touched = clean_segments(
        [seg("Thank you.", start=i * 30) for i in range(5)]
        + [seg("Good afternoon.", start=200)]
    )
    assert [s.text for s in segments] == ["Thank you.", "Thank you.", "Good afternoon."]
    assert touched == 3


def test_cjk_spam_is_dropped():
    assert is_spam("啊" * 40)
    segments, touched = clean_segments([seg("啊" * 40), seg("正常的中文字幕内容")])
    assert [s.text for s in segments] == ["正常的中文字幕内容"]
    assert touched == 1
