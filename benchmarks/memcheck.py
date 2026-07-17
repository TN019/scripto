#!/usr/bin/env python3
"""Memory-curve check (PLAN §5 metric 2): RSS must stay flat across a batch.

Usage:
    uv run python benchmarks/memcheck.py <media-dir> [--model tiny] [--interval 0.5]

Records this process's RSS at every completed file (plus a time-based curve
for the JSON), skips the first file as model-load warmup, and compares the
mean of the last five completions against the first five after warmup.
Verdict fails when growth exceeds 15% — the my-transcriptor staircase leak
was far beyond that. Robust to batch duration, unlike time-thirds.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from scripto.core import scanner
from scripto.core.config import ConfigService
from scripto.core.events import EventBus, StatusEvent
from scripto.core.history import HistoryStore
from scripto.core.pipeline import Pipeline, PipelineSettings
from scripto.engines.models import get_spec
from scripto.engines.select import create_engine, resolve_engine_name


def rss_mb() -> float:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(out) / 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media_dir")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    config = ConfigService().load()
    files = scanner.scan([args.media_dir]).files
    if not files:
        print("no media found")
        return 1

    samples: list[tuple[float, float]] = []
    stop_sampling = threading.Event()

    def sampler() -> None:
        start = time.monotonic()
        while not stop_sampling.is_set():
            samples.append((round(time.monotonic() - start, 1), round(rss_mb(), 1)))
            time.sleep(args.interval)

    # RSS at each completed file — robust to batch duration.
    per_done: list[float] = []
    bus = EventBus()
    bus.subscribe(lambda e: per_done.append(rss_mb())
                  if isinstance(e, StatusEvent) and e.status == "done" else None)

    engine_name, _ = resolve_engine_name(config["engine"])
    settings = PipelineSettings(
        model=get_spec(args.model), overwrite=True, engine_label=engine_name,
    )
    pipeline = Pipeline(
        engine=create_engine(config["engine"]), bus=bus,
        history=HistoryStore(Path("/tmp/scripto-memcheck-history.json")),
        settings=settings,
    )

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    _jobs, stats = pipeline.run(files, threading.Event())
    stop_sampling.set()
    thread.join()

    steady = per_done[1:]  # first file carries model-load warmup
    if len(steady) < 6:
        print("need at least 7 completed files for a meaningful verdict")
        return 1
    early_mean = sum(steady[:5]) / 5
    late_mean = sum(steady[-5:]) / 5
    growth_pct = (late_mean - early_mean) / early_mean * 100

    verdict = "PASS" if growth_pct <= 15 else "FAIL"
    print(f"files={len(files)} done={stats.done} completions sampled={len(per_done)}")
    print(f"rss per-done: first5(after warmup)={early_mean:.0f}MB "
          f"last5={late_mean:.0f}MB peak={max(per_done):.0f}MB growth={growth_pct:+.1f}%")
    print(f"verdict: {verdict} (threshold +15%)")

    out = Path("benchmarks/results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"memcheck-{stamp}.json"
    path.write_text(json.dumps({
        "params": vars(args), "platform": platform.platform(),
        "files": len(files), "growth_pct": round(growth_pct, 1),
        "verdict": verdict, "per_done_mb": per_done, "samples": samples,
    }, indent=2), encoding="utf-8")
    print(f"written: {path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
