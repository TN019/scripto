import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_windows_launchers_exist_and_are_sane():
    bat = (REPO / "launchers/windows/Scripto.bat").read_text(encoding="utf-8")
    assert "uv run scripto" in bat
    assert "where uv" in bat  # missing-uv guidance path
    vbs = (REPO / "launchers/windows/Scripto.vbs").read_text(encoding="utf-8")
    assert "Scripto.bat" in vbs and ", 0," in vbs  # hidden-window run


def test_icon_generator_produces_valid_pngs(tmp_path):
    subprocess.run(
        [sys.executable, str(REPO / "launchers/macos/make_icon.py"), str(tmp_path / "i.iconset")],
        check=True, capture_output=True,
    )
    files = sorted((tmp_path / "i.iconset").glob("*.png"))
    assert len(files) == 10
    for png in files:
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="bundle build needs macOS tools")
def test_build_app_produces_valid_bundle(tmp_path, monkeypatch):
    subprocess.run(
        ["bash", str(REPO / "launchers/macos/build_app.sh")],
        check=True, capture_output=True, cwd=REPO,
    )
    app = REPO / "dist/Scripto.app"
    launcher = app / "Contents/MacOS/scripto-launcher"
    assert launcher.exists()
    assert launcher.stat().st_mode & 0o111, "launcher must be executable"
    with (app / "Contents/Info.plist").open("rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleExecutable"] == "scripto-launcher"
    assert (app / "Contents/Resources/AppIcon.icns").exists()
    body = launcher.read_text(encoding="utf-8")
    assert "uv run scripto" in body
    assert str(REPO) in body  # repo path embedded at build time
