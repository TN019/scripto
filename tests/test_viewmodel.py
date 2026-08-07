import threading
import time
from pathlib import Path

from scripto.core.config import ConfigService
from scripto.core.events import ProgressEvent, StatusEvent
from scripto.core.history import HistoryEntry, HistoryStore
from scripto.gui.viewmodel import LOG_LIMIT, GuiViewModel


def make_vm(tmp_path) -> GuiViewModel:
    return GuiViewModel(
        config_service=ConfigService(tmp_path / "config.json"),
        history=HistoryStore(tmp_path / "history.json"),
    )


def make_media(tmp_path, count=3):
    files = []
    for i in range(count):
        f = tmp_path / f"clip{i}.mp4"
        f.write_bytes(b"x")
        files.append(f)
    return files


def test_scan_inputs_builds_rows(tmp_path):
    vm = make_vm(tmp_path)
    files = make_media(tmp_path)
    count, warnings = vm.scan_inputs("\n".join(str(f) for f in files) + "\n/no/such.mp4")
    assert count == 3
    assert any(w.startswith("scan.missing") for w in warnings)
    assert [vm.rows[i].name for i in vm.row_order] == ["clip0.mp4", "clip1.mp4", "clip2.mp4"]


def test_drain_applies_status_and_progress(tmp_path):
    vm = make_vm(tmp_path)
    make_media(tmp_path, 2)
    vm.scan_inputs(str(tmp_path))
    vm._id_map = {1: vm.row_order[0], 2: vm.row_order[1]}

    vm.bus.emit(StatusEvent(subject=f"job:1", status="transcribing"))
    vm.bus.emit(ProgressEvent(scope="transcribe:1", done=50, total=100))
    result = vm.drain()
    row = vm.rows[vm.row_order[0]]
    assert result.changed_rows == [row.id]
    assert row.status == "transcribing"
    assert row.progress == 0.5
    assert row.stage == "transcribe"

    vm.bus.emit(StatusEvent(subject="job:1", status="failed", detail="boom"))
    result = vm.drain()
    assert vm.rows[row.id].error == "boom"
    assert vm.failed_rows()[0].id == row.id
    assert any("boom" in line for line in result.log_lines)


def _running_batch(vm, tmp_path, count: int, *, elapsed: float):
    make_media(tmp_path, count)
    vm.scan_inputs(str(tmp_path))
    vm.running = True
    vm._batch_started = time.monotonic() - elapsed
    vm._id_map = {i: rid for i, rid in enumerate(vm.row_order, start=1)}


def test_eta_extrapolates_wall_clock_throughput(tmp_path):
    vm = make_vm(tmp_path)
    _running_batch(vm, tmp_path, 4, elapsed=60.0)

    vm.bus.emit(StatusEvent(subject="job:1", status="done"))
    snap = vm.drain().snapshot
    assert snap.done == 1 and snap.total == 4
    assert 170 < snap.eta_sec < 195   # a minute bought one file, three to go


def test_eta_treats_instantly_skipped_files_as_cheap(tmp_path):
    """The shape that produced a 3000-minute estimate: most files skip.

    Timing transcriptions individually and multiplying by the files left
    charged every already-subtitled file a full transcription. Wall-clock
    throughput lets them cost what they actually cost.
    """
    vm = make_vm(tmp_path)
    _running_batch(vm, tmp_path, 10, elapsed=5.0)

    for job_id in range(1, 6):   # five files already had subtitles
        vm.bus.emit(StatusEvent(subject=f"job:{job_id}", status="skipped"))
    snap = vm.drain().snapshot
    assert snap.done == 5
    assert snap.eta_sec < 10     # seconds, not five full transcriptions


def test_eta_waits_for_the_first_finished_file(tmp_path):
    vm = make_vm(tmp_path)
    _running_batch(vm, tmp_path, 3, elapsed=30.0)
    vm.bus.emit(StatusEvent(subject="job:1", status="downloading"))
    snap = vm.drain().snapshot
    assert snap.eta_sec is None            # nothing to extrapolate from yet
    assert snap.current_status == "downloading"   # but the row is shown as busy


def test_batch_runs_via_fake_pipeline(tmp_path, monkeypatch):
    vm = make_vm(tmp_path)
    make_media(tmp_path, 2)
    vm.scan_inputs(str(tmp_path))

    release = threading.Event()

    class FakePipeline:
        def __init__(self, bus):
            self.bus = bus

        def run(self, files, stop):
            release.wait(timeout=5)
            for i, _f in enumerate(files, start=1):
                self.bus.emit(StatusEvent(subject=f"job:{i}", status="done"))
            return [], None

    monkeypatch.setattr(
        GuiViewModel, "_build_pipeline",
        lambda self, config, overwrite=None: FakePipeline(self.bus),
    )
    finished = threading.Event()
    assert vm.start_batch(on_finished=finished.set)
    assert not vm.start_batch()  # guarded while the batch is running
    release.set()
    assert finished.wait(timeout=5)
    for _ in range(20):
        result = vm.drain()
        if result.finished:
            break
        time.sleep(0.02)
    assert not vm.running
    assert all(r.status == "done" for r in vm.rows.values())


def test_retry_maps_single_row_to_job_one(tmp_path, monkeypatch):
    vm = make_vm(tmp_path)
    make_media(tmp_path, 3)
    vm.scan_inputs(str(tmp_path))
    target = vm.row_order[2]
    seen = {}

    class FakePipeline:
        def __init__(self, bus):
            self.bus = bus

        def run(self, files, stop):
            seen["files"] = list(files)
            self.bus.emit(StatusEvent(subject="job:1", status="done"))
            return [], None

    monkeypatch.setattr(
        GuiViewModel, "_build_pipeline",
        lambda self, config, overwrite=None: FakePipeline(self.bus),
    )
    finished = threading.Event()
    vm.start_batch(only_ids=[target], on_finished=finished.set)
    assert finished.wait(timeout=5)
    vm.drain()
    assert seen["files"] == [vm.rows[target].path]
    assert vm.rows[target].status == "done"  # job:1 mapped back to row 3


def test_log_ring_is_capped(tmp_path):
    vm = make_vm(tmp_path)
    for i in range(LOG_LIMIT + 100):
        vm._log(f"line {i}")
    assert len(vm.log_lines) == LOG_LIMIT
    assert "line 599" in vm.log_lines[-1]


def test_history_rows_and_clean(tmp_path):
    vm = make_vm(tmp_path)
    alive = tmp_path / "alive.srt"
    alive.write_text("x", encoding="utf-8")
    vm.history.append(HistoryEntry(
        source="/a.mp4", outputs=[{"lang": "en", "format": "srt", "path": str(alive)}],
        model="tiny", engine="mlx", status="done",
    ))
    vm.history.append(HistoryEntry(
        source="/b.mp4", outputs=[{"lang": "en", "format": "srt", "path": "/gone.srt"}],
        model="tiny", engine="mlx", status="done",
    ))
    rows = vm.history_rows()
    assert [(Path(e.source).name, exists) for e, exists in rows] == [
        ("b.mp4", False), ("a.mp4", True),
    ]
    assert vm.history_clean_missing() == 1
    assert len(vm.history_rows()) == 1


def test_history_groups_merge_languages_per_source(tmp_path):
    vm = make_vm(tmp_path)
    en_srt = tmp_path / "talk.en.srt"
    zh_srt = tmp_path / "talk.zh.srt"
    en_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    zh_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    source = str(tmp_path / "talk.mp4")
    vm.history.append(HistoryEntry(
        source=source, outputs=[{"lang": "en", "format": "srt", "path": str(en_srt)}],
        model="tiny", engine="mlx", status="done",
    ))
    vm.history.append(HistoryEntry(
        source=source, outputs=[{"lang": "zh", "format": "srt", "path": str(zh_srt)}],
        model="ollama/qwen3:8b", engine="translate", status="done",
    ))
    groups = vm.history_groups()
    assert len(groups) == 1  # one entry per source file, not per production
    group = groups[0]
    assert set(group.existing) == {"en", "zh"}
    assert group.missing == ["ja", "ko"]
    assert not group.deleted


def test_history_group_offers_missing_language(tmp_path):
    vm = make_vm(tmp_path)
    en_srt = tmp_path / "talk.en.srt"
    en_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    vm.history.append(HistoryEntry(
        source=str(tmp_path / "talk.mp4"),
        outputs=[{"lang": "en", "format": "srt", "path": str(en_srt)}],
        model="tiny", engine="mlx", status="done",
    ))
    group = vm.history_groups()[0]
    assert group.missing == ["zh", "ja", "ko"]
    assert group.translate_from == str(en_srt)


def test_history_group_deleted_when_all_outputs_gone(tmp_path):
    vm = make_vm(tmp_path)
    vm.history.append(HistoryEntry(
        source="/gone/talk.mp4",
        outputs=[{"lang": "en", "format": "srt", "path": "/gone/talk.en.srt"}],
        model="tiny", engine="mlx", status="done",
    ))
    group = vm.history_groups()[0]
    assert group.deleted
    assert group.missing == []  # nothing to translate from


def test_translate_history_produces_and_records(tmp_path, monkeypatch):
    vm = make_vm(tmp_path)
    en_srt = tmp_path / "talk.en.srt"
    en_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    source = str(tmp_path / "talk.mp4")
    vm.history.append(HistoryEntry(
        source=source, outputs=[{"lang": "en", "format": "srt", "path": str(en_srt)}],
        model="tiny", engine="mlx", status="done",
    ))

    class FakeStage:
        label = "ollama/fake"

        def __init__(self, *a, **k):
            pass

        def translate(self, srt_path, src, *, stop_check, progress):
            out = src.with_name(src.stem + ".zh.srt")
            out.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
            return [out]

        def release(self):
            pass

    import scripto.gui.viewmodel as vmod

    monkeypatch.setattr(vmod, "OllamaTranslateStage", FakeStage)
    group = vm.history_groups()[0]
    produced = vm.translate_history(group, "zh")
    assert produced and produced[0].name == "talk.zh.srt"
    regrouped = vm.history_groups()[0]
    assert set(regrouped.existing) == {"en", "zh"}
    assert regrouped.missing == ["ja", "ko"]


def test_first_run_detection(tmp_path):
    vm = make_vm(tmp_path)
    assert vm.is_first_run()
    vm.update_settings(language="zh")
    assert not vm.is_first_run()


def test_gui_module_imports():
    import scripto.gui_qt.main_window  # noqa: F401  (catches Qt API drift at import time)


def test_translation_queue_processes_records_and_dedupes(tmp_path, monkeypatch):
    vm = make_vm(tmp_path)
    en_srt = tmp_path / "talk.en.srt"
    en_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    vm.history.append(HistoryEntry(
        source=str(tmp_path / "talk.mp4"),
        outputs=[{"lang": "en", "format": "srt", "path": str(en_srt)}],
        model="tiny", engine="mlx", status="done",
    ))
    gate = threading.Event()

    class FakeStage:
        label = "ollama/fake"

        def __init__(self, _client, **kwargs):
            self.kwargs = kwargs

        def translate(self, srt_path, source, stop_check=None, progress=None):
            gate.wait(timeout=5)
            if progress is not None:
                progress(40, 40)
            out = source.with_name(f"{source.stem}.{self.kwargs['target']}.srt")
            out.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
            return [out]

        def release(self):
            pass

    import scripto.gui.viewmodel as vmod

    monkeypatch.setattr(vmod, "OllamaTranslateStage", FakeStage)
    group = vm.history_groups()[0]
    assert vm.queue_translation(group, "zh")
    assert not vm.queue_translation(group, "zh")   # duplicate while queued
    assert vm.queue_translation(group, "ja")       # second job queues fine
    gate.set()

    deadline = time.time() + 5
    while time.time() < deadline:
        jobs = vm.translation_snapshot()
        if len(jobs) == 2 and all(j.status in ("done", "failed") for j in jobs):
            break
        time.sleep(0.02)
    jobs = vm.translation_snapshot()
    assert [j.status for j in jobs] == ["done", "done"]
    assert (jobs[0].done, jobs[0].total) == (40, 40)  # progress reached the job

    regrouped = vm.history_groups()[0]
    assert {"en", "zh", "ja"} <= set(regrouped.existing)
    assert not vm.queue_translation(regrouped, "zh")  # target already exists
