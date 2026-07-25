"""Branded viewer: bundle rebranding and the build fallback contract."""

import os
import plistlib
import sys
from pathlib import Path

import pytest

from scripto.core import viewer


def _fake_bundle(tmp_path: Path) -> Path:
    app = tmp_path / "Flet.app"
    (app / "Contents/Resources").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Flet",
        "CFBundleIdentifier": "com.appveyor.flet",
        "CFBundleIconFile": "AppIcon",
    }))
    (app / "Contents/Resources/AppIcon.icns").write_bytes(b"old")
    return app


def test_rebrand_renames_and_swaps_icon(tmp_path):
    app = _fake_bundle(tmp_path)
    icon = tmp_path / "new.icns"
    icon.write_bytes(b"new")

    viewer.rebrand_bundle(app, icon)

    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleName"] == "Scripto"
    assert info["CFBundleDisplayName"] == "Scripto"
    assert info["CFBundleIdentifier"] == "local.scripto.viewer"
    assert (app / "Contents/Resources/AppIcon.icns").read_bytes() == b"new"


def test_rebrand_keeps_stock_icon_without_replacement(tmp_path):
    app = _fake_bundle(tmp_path)
    viewer.rebrand_bundle(app, None)
    assert (app / "Contents/Resources/AppIcon.icns").read_bytes() == b"old"


@pytest.mark.skipif(
    sys.platform != "darwin" or not os.environ.get("SCRIPTO_VIEWER_SMOKE"),
    reason="macOS-only; set SCRIPTO_VIEWER_SMOKE=1 (copies + re-signs the real client)",
)
def test_build_produces_a_signed_scripto_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIPTO_DATA_DIR", str(tmp_path))

    dest = viewer.ensure_branded_viewer()

    assert dest is not None
    app = dest / viewer.VIEWER_APP_NAME
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleName"] == "Scripto"
    import subprocess
    subprocess.run(["codesign", "--verify", str(app)], check=True)
