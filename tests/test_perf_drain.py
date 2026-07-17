"""UI-fluency proxy (PLAN §5 metric 4): the viewmodel drain must stay cheap
with 500 files and event floods — the UI tick applies only per-row deltas, so
a fast drain plus per-row updates is what keeps the interface responsive.
"""

from __future__ import annotations

import time

from scripto.core.config import ConfigService
from scripto.core.events import ProgressEvent, StatusEvent
from scripto.core.history import HistoryStore
from scripto.gui.viewmodel import GuiViewModel


def make_vm_with_rows(tmp_path, count: int) -> GuiViewModel:
    vm = GuiViewModel(
        config_service=ConfigService(tmp_path / "config.json"),
        history=HistoryStore(tmp_path / "history.json"),
    )
    for i in range(count):
        (tmp_path / f"clip{i:03d}.mp4").write_bytes(b"x")
    vm.scan_inputs(str(tmp_path))
    assert len(vm.row_order) == count
    vm._id_map = {i: rid for i, rid in enumerate(vm.row_order, start=1)}
    return vm


def test_drain_stays_fast_with_500_files_and_event_flood(tmp_path):
    vm = make_vm_with_rows(tmp_path, 500)

    # a busy tick: 100 files advance, each with status + 10 progress events
    for job in range(1, 101):
        vm.bus.emit(StatusEvent(subject=f"job:{job}", status="transcribing"))
        for step in range(10):
            vm.bus.emit(ProgressEvent(scope=f"transcribe:{job}", done=step, total=10))

    started = time.perf_counter()
    result = vm.drain()
    elapsed = time.perf_counter() - started

    assert len(result.changed_rows) == 100  # deltas only, deduplicated
    assert elapsed < 0.05, f"drain took {elapsed * 1000:.1f}ms for one tick"


def test_scan_of_500_files_is_subsecond(tmp_path):
    started = time.perf_counter()
    make_vm_with_rows(tmp_path, 500)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"scan+row build took {elapsed:.2f}s"
