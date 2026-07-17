"""GUI-logic end-to-end smoke (opt-in): real engine through the viewmodel —
scan → start_batch → rows reach done → outputs on disk → history grouped.
Complements the CLI e2e in test_pipeline_e2e.py; together they cover the
"CLI 与 GUI 主流程" acceptance item.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

import pytest

from scripto.core.config import ConfigService
from scripto.core.history import HistoryStore
from scripto.gui.viewmodel import GuiViewModel

smoke = pytest.mark.skipif(
    not os.environ.get("SCRIPTO_ENGINE_SMOKE"),
    reason="set SCRIPTO_ENGINE_SMOKE=1 to run the GUI-logic e2e smoke",
)


@smoke
def test_viewmodel_end_to_end(tmp_path):
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs macOS `say` and ffmpeg")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "hello from the gui smoke test"], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff), "-c:a", "aac", str(media_dir / "gui talk.m4a")],
        check=True, capture_output=True,
    )

    config = ConfigService(tmp_path / "config.json")
    config.update(whisper_model="tiny", transcribe_language="en", language="en")
    vm = GuiViewModel(config_service=config, history=HistoryStore(tmp_path / "history.json"))

    count, _ = vm.scan_inputs(str(media_dir))
    assert count == 1

    finished = threading.Event()
    assert vm.start_batch(on_finished=finished.set)
    assert finished.wait(timeout=180)

    for _ in range(40):
        result = vm.drain()
        if result.finished:
            break
    row = vm.rows[vm.row_order[0]]
    assert row.status == "done", row.error

    produced = media_dir / "gui talk.en.srt"
    assert produced.exists()
    assert "hello" in produced.read_text(encoding="utf-8").lower()

    groups = vm.history_groups()
    assert len(groups) == 1
    assert "en" in groups[0].existing
