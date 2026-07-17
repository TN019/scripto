#!/usr/bin/env python3
"""First-try translation batch success rate (PLAN §5 metric 3, target >95%).

Usage:
    uv run python benchmarks/translate_stats.py [--blocks 120] [--model qwen3:4b]

Synthesizes a lecture-like English SRT, runs the real translate stage against
local Ollama, and counts how many marker batches align on the FIRST attempt
(split-retries and single-block fallbacks count as failures for this metric,
even though they still produce a correct file).
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

from scripto.translate import srt
from scripto.translate.ollama import OllamaClient
from scripto.translate.stage import BatchAlignmentError, OllamaTranslateStage

SENTENCES = [
    "Today we are going to cover the basics of operating systems.",
    "A process is a program in execution, together with its state.",
    "The scheduler decides which process runs on the CPU next.",
    "Semaphores help us coordinate access to shared resources.",
    "Remember that deadlocks require four conditions to hold at once.",
    "Virtual memory lets each process pretend it owns the whole address space.",
    "Page faults are handled transparently by the kernel.",
    "File systems organize blocks into directories and inodes.",
    "Caching is the single most important performance trick in systems.",
    "Let's pause here and take a couple of questions from the audience.",
]


def build_sample(blocks: int) -> str:
    parts = []
    for i in range(blocks):
        start = i * 3
        parts += [
            str(i + 1),
            f"00:{start // 60:02d}:{start % 60:02d},000 --> 00:{(start + 2) // 60:02d}:{(start + 2) % 60:02d},500",
            SENTENCES[i % len(SENTENCES)],
            "",
        ]
    return "\n".join(parts)


class CountingStage(OllamaTranslateStage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_try = 0
        self.first_fail = 0
        self._depth = 0

    def _translate_span(self, chunk, stop_check):
        if self._depth == 0 and len(chunk) > 1:
            self._depth += 1
            try:
                try:
                    result = self._translate_marked(chunk, stop_check)
                    self.first_try += 1
                    return result
                except (BatchAlignmentError, Exception):
                    self.first_fail += 1
                    return super()._translate_span(chunk, stop_check)
            finally:
                self._depth -= 1
        return super()._translate_span(chunk, stop_check)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocks", type=int, default=120)
    parser.add_argument("--model", default="qwen3:4b")
    args = parser.parse_args()

    client = OllamaClient()
    if not client.is_reachable():
        print("Ollama is not running (ollama serve)")
        return 1

    stage = CountingStage(client, model=args.model, target="zh")
    content = build_sample(args.blocks)
    started = time.monotonic()
    translated = stage.translate_content(content)
    elapsed = time.monotonic() - started
    stage.release()

    blocks_out = srt.parse_srt(translated)
    total_batches = stage.first_try + stage.first_fail
    rate = stage.first_try / total_batches * 100 if total_batches else 0.0
    verdict = "PASS" if rate > 95 else "FAIL"
    print(f"blocks={args.blocks} batches={total_batches} "
          f"first-try ok={stage.first_try} fail={stage.first_fail} "
          f"rate={rate:.1f}% time={elapsed:.1f}s")
    print(f"output blocks={len(blocks_out)} (structure preserved: "
          f"{len(blocks_out) == args.blocks})")
    print(f"verdict: {verdict} (target >95%)")

    out = Path("benchmarks/results")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"translate-stats-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({
        "params": vars(args), "platform": platform.platform(),
        "batches": total_batches, "first_try_rate_pct": round(rate, 1),
        "elapsed_sec": round(elapsed, 1), "verdict": verdict,
    }, indent=2), encoding="utf-8")
    print(f"written: {path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
