#!/usr/bin/env python3
"""Throughput benchmark: per-stage timing breakdown over a media set.

Usage:
    uv run python benchmarks/bench.py <media-dir> [--model tiny] [--mode balanced|low]
                                      [--translate] [--runs 1]

Reports per-file extract/transcribe/translate wall times (collected from the
pipeline's own events), the batch total, and the PLAN §5 metric-1 target
(total ≈ max(transcribe_sum, translate_sum) + first extract). Results include
parameters and platform so runs are comparable; JSON is written next to the
console table.
"""

from __future__ import annotations

import argparse
import json
import platform
import threading
import time
from collections import defaultdict
from pathlib import Path

from scripto.core import scanner
from scripto.core.config import ConfigService
from scripto.core.events import EventBus, StatusEvent
from scripto.core.history import HistoryStore
from scripto.core.pipeline import Pipeline, PipelineSettings
from scripto.engines.models import get_spec
from scripto.engines.select import create_engine, resolve_engine_name
from scripto.translate.ollama import OllamaClient
from scripto.translate.stage import OllamaTranslateStage


def run_once(files, *, model, mode, translate, config) -> dict:
    bus = EventBus()
    stage_starts: dict[tuple[int, str], float] = {}
    durations: dict[str, dict[int, float]] = defaultdict(dict)

    def watch(event) -> None:
        if not isinstance(event, StatusEvent) or not event.subject.startswith("job:"):
            return
        job = int(event.subject.split(":")[1])
        now = time.monotonic()
        stage_map = {"extracting": "extract", "transcribing": "transcribe",
                     "translating": "translate"}
        stage = stage_map.get(event.status)
        if stage:
            stage_starts[(job, stage)] = now
            return
        for (j, s), started in list(stage_starts.items()):
            if j == job:
                durations[s][j] = durations[s].get(j, 0.0) + (now - started)
                del stage_starts[(j, s)]

    bus.subscribe(watch)

    translate_stage = None
    if translate:
        client = OllamaClient(config["ollama_url"])
        if client.is_reachable():
            translate_stage = OllamaTranslateStage(
                client, model=config["ollama_model"], target="zh", overwrite=True,
            )

    engine_name, _ = resolve_engine_name(config["engine"])
    settings = PipelineSettings(
        model=get_spec(model), overwrite=True, memory_mode=mode,
        engine_label=engine_name,
    )
    pipeline = Pipeline(
        engine=create_engine(config["engine"]), bus=bus,
        history=HistoryStore(Path("/tmp/scripto-bench-history.json")),
        settings=settings, translate_stage=translate_stage,
    )
    started = time.monotonic()
    _jobs, stats = pipeline.run(files, threading.Event())
    total = time.monotonic() - started

    sums = {stage: sum(v.values()) for stage, v in durations.items()}
    first_extract = min(durations.get("extract", {0: 0.0}).values() or [0.0])
    target = max(sums.get("transcribe", 0.0), sums.get("translate", 0.0)) + first_extract
    return {
        "total_sec": round(total, 2),
        "stage_sums_sec": {k: round(v, 2) for k, v in sums.items()},
        "metric1_target_sec": round(target, 2),
        "metric1_overhead_pct": round((total - target) / target * 100, 1) if target else None,
        "stats": stats.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media_dir")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--mode", default="balanced", choices=["balanced", "low"])
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    config = ConfigService().load()
    files = scanner.scan([args.media_dir]).files
    if not files:
        print("no media found")
        return 1

    report = {
        "params": vars(args),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "files": len(files),
        "runs": [],
    }
    for i in range(args.runs):
        result = run_once(
            files, model=args.model, mode=args.mode,
            translate=args.translate, config=config,
        )
        report["runs"].append(result)
        print(f"run {i + 1}: total={result['total_sec']}s "
              f"stages={result['stage_sums_sec']} "
              f"metric1: target={result['metric1_target_sec']}s "
              f"overhead={result['metric1_overhead_pct']}%")

    out = Path("benchmarks/results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"bench-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
