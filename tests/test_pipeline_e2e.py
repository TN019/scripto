"""Real end-to-end smoke: media file → scripto-cli run → .en.srt next to it.

Opt-in via SCRIPTO_ENGINE_SMOKE=1 (downloads the tiny model, needs macOS
`say` + ffmpeg to synthesize test media).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from scripto import cli

smoke = pytest.mark.skipif(
    not os.environ.get("SCRIPTO_ENGINE_SMOKE"),
    reason="set SCRIPTO_ENGINE_SMOKE=1 to run the real end-to-end smoke test",
)


@smoke
def test_cli_run_end_to_end(tmp_path, capsys):
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs macOS `say` and ffmpeg")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    aiff = tmp_path / "speech.aiff"
    media = media_dir / "hello talk.m4a"
    subprocess.run(
        ["say", "-o", str(aiff), "hello world, welcome to the scripto pipeline test"],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff), "-c:a", "aac", str(media)],
        check=True, capture_output=True,
    )

    exit_code = cli.main(["run", str(media_dir), "--model", "tiny", "--language", "en"])
    out = capsys.readouterr().out
    assert exit_code == 0, out

    produced = media_dir / "hello talk.en.srt"
    assert produced.exists(), out
    content = produced.read_text(encoding="utf-8")
    assert "-->" in content
    assert "hello" in content.lower()

    # Second run without --overwrite must skip.
    exit_code = cli.main(["run", str(media_dir), "--model", "tiny", "--language", "en"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "skip" in out.lower() or "跳过" in out
