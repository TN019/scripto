"""`scripto-cli` entry point.

Commands:
- ``info`` — data locations and current settings
- ``run``  — transcribe files/folders through the staged pipeline

Ctrl+C during ``run`` requests a graceful stop: the pipeline runs on a worker
thread, the stop event reaches every stage, and remaining files are reported
as unprocessed instead of failed.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from . import __version__
from .core import paths, scanner
from .core.config import ConfigService
from .core.errors import ScriptoError
from .core.events import BatchEvent, Event, EventBus, StatusEvent
from .core.history import HistoryStore
from .core.jobs import BatchStats, Job
from .core.languages import known_languages
from .core.logs import setup_logging
from .core.pipeline import Pipeline, PipelineSettings
from .engines.models import get_spec
from .engines.select import create_engine, resolve_engine_name
from .i18n import I18n


def build_parser(i18n: I18n) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripto-cli", description=i18n.t("cli.description")
    )
    parser.add_argument(
        "--version", action="version", version=f"scripto {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("info", help=i18n.t("cli.info.help"))
    subparsers.add_parser("doctor", help=i18n.t("cli.doctor.help"))

    run = subparsers.add_parser("run", help=i18n.t("cli.run.help"))
    run.add_argument("inputs", nargs="+", help="media files and/or folders")
    run.add_argument("--model", default=None, help="whisper model key (e.g. tiny, large-v3-turbo)")
    lang_codes = [spec.code for spec in known_languages()]
    run.add_argument("--language", default=None, choices=["auto", *lang_codes])
    run.add_argument("--format", dest="fmt", default=None, choices=["srt", "txt", "vtt", "json"])
    run.add_argument("--overwrite", action="store_true", default=None)
    run.add_argument("--no-recursive", dest="recursive", action="store_false", default=None)
    run.add_argument("--export", dest="export_dir", default=None, help="collect outputs here instead of next to sources")
    run.add_argument("--translate", action="store_true", default=None,
                     help="also translate subtitles via local Ollama")
    run.add_argument("--no-translate", dest="translate", action="store_false")
    run.add_argument("--target", default=None, choices=lang_codes,
                     help="translation target language")
    return parser


def run_info(config_service: ConfigService, i18n: I18n) -> int:
    config = config_service.load()
    language = config.get("language") or i18n.t("cli.language.not_set")
    print(i18n.t("cli.info.header", version=__version__))
    print(i18n.t("cli.info.config", path=config_service.path))
    print(i18n.t("cli.info.data_dir", path=paths.data_dir()))
    print(i18n.t("cli.info.log_dir", path=paths.log_dir()))
    print(i18n.t("cli.info.language", value=language))
    return 0


def run_doctor_cmd(config_service: ConfigService, i18n: I18n) -> int:
    from .core.doctor import doctor_ok, run_doctor

    results = run_doctor(config_service.load())
    print(i18n.t("doctor.header"))
    for result in results:
        if result.ok:
            mark, verdict = "✓", i18n.t("doctor.ok")
        elif result.required:
            mark, verdict = "✗", i18n.t("doctor.fail")
        else:
            mark, verdict = "!", i18n.t("doctor.warn")
        name = i18n.t(f"doctor.{result.key}", detail=result.detail)
        detail = f"  ({result.detail})" if result.ok and result.detail else ""
        print(f"  {mark} {name}: {verdict}{detail}")
        if not result.ok and result.hint:
            print(i18n.t("doctor.fix", hint=result.hint))
    ok = doctor_ok(results)
    print(i18n.t("doctor.all_good" if ok else "doctor.has_problems"))
    return 0 if ok else 1


def run_batch(args: argparse.Namespace, config_service: ConfigService, i18n: I18n) -> int:
    config = config_service.load()
    model_key = args.model or config["whisper_model"]
    language = args.language if args.language is not None else config["transcribe_language"]
    fmt = args.fmt or config["output_format"]
    overwrite = config["overwrite"] if args.overwrite is None else args.overwrite
    recursive = config["recursive_scan"] if args.recursive is None else args.recursive
    export_dir = Path(args.export_dir).expanduser() if args.export_dir else None

    result = scanner.scan(args.inputs, recursive=recursive)
    for warning in result.warnings:
        key, _, value = warning.partition(":")
        print(i18n.t(key, value=value))
    if not result.files:
        print(i18n.t("run.none"))
        return 1
    print(i18n.t("run.discovered", count=len(result.files)))

    engine_name, _reason = resolve_engine_name(config["engine"])
    print(i18n.t("run.engine", engine=engine_name, model=model_key, fmt=fmt))

    # Translation stage (M4): only for srt output and a reachable Ollama;
    # anything else degrades to transcription-only with a clear message.
    do_translate = (
        config["translate_enabled"] if args.translate is None else args.translate
    )
    target = args.target or config["translate_target"]
    translate_stage = None
    if do_translate and fmt != "srt":
        print(i18n.t("run.translate_needs_srt"))
    elif do_translate:
        from .translate.ollama import OllamaClient
        from .translate.stage import OllamaTranslateStage

        client = OllamaClient(config["ollama_url"])
        if not client.is_reachable():
            print(i18n.t("run.ollama_unreachable", url=config["ollama_url"]))
        else:
            translate_stage = OllamaTranslateStage(
                client,
                model=config["ollama_model"],
                target=target,
                overwrite=overwrite,
                batch_blocks=int(config["translate_batch_blocks"]),
                batch_max_chars=int(config["translate_batch_max_chars"]),
            )
            print(i18n.t("run.translate_on", model=config["ollama_model"], target=target))

    bus = EventBus()
    total = len(result.files)
    names = {job_id: src.name for job_id, src in enumerate(result.files, start=1)}

    def printer(event: Event) -> None:
        if isinstance(event, StatusEvent) and event.subject.startswith("job:"):
            job_id = int(event.subject.split(":", 1)[1])
            print(i18n.t(
                "run.job_status",
                id=job_id, total=total,
                name=names.get(job_id, "?"),
                status=i18n.t(f"status.{event.status}"),
            ))

    bus.subscribe(printer)

    settings = PipelineSettings(
        model=get_spec(model_key),
        fmt=fmt,
        language=None if language == "auto" else language,
        overwrite=overwrite,
        export_dir=export_dir,
        suffix_map=dict(config["lang_suffixes"]),
        memory_mode=config["memory_mode"],
        icloud_evict=config["icloud_evict"],
        engine_label=engine_name,
        segment_threshold_sec=float(config["segment_threshold_sec"]),
        segment_chunk_sec=float(config["segment_chunk_sec"]),
    )
    pipeline = Pipeline(
        engine=create_engine(config["engine"]),
        bus=bus,
        history=HistoryStore(),
        settings=settings,
        translate_stage=translate_stage,
    )

    stop = threading.Event()
    outcome: dict = {}

    def work() -> None:
        outcome["jobs"], outcome["stats"] = pipeline.run(result.files, stop)

    worker = threading.Thread(target=work, name="scripto-batch")
    worker.start()
    try:
        while worker.is_alive():
            worker.join(timeout=0.2)
    except KeyboardInterrupt:
        print("\n" + i18n.t("run.stopping"))
        stop.set()
        worker.join()

    jobs: list[Job] = outcome.get("jobs", [])
    stats: BatchStats = outcome.get("stats", BatchStats())
    print(i18n.t(
        "run.summary",
        done=stats.done, skipped=stats.skipped, failed=stats.failed,
        unprocessed=stats.unprocessed, sec=round(stats.elapsed_sec, 1),
    ))
    for job in jobs:
        if job.status.value == "failed":
            print(i18n.t("run.failed_item", name=job.source.name, reason=job.error))
    return 0 if stats.failed == 0 else 2


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    config_service = ConfigService()
    i18n = I18n(lambda: config_service.load().get("language", ""))
    parser = build_parser(i18n)
    args = parser.parse_args(argv)

    try:
        if args.command == "info":
            return run_info(config_service, i18n)
        if args.command == "doctor":
            return run_doctor_cmd(config_service, i18n)
        if args.command == "run":
            return run_batch(args, config_service, i18n)
    except ScriptoError as exc:
        if exc.key:
            print(i18n.t(exc.key, **exc.params))
        else:
            print(str(exc))
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
