"""Branded macOS viewer: the GUI window carries Scripto's name and icon.

flet's desktop runtime opens the window with a cached, generic "Flet.app"
client — so the Dock shows a second, foreign icon next to the launcher.
Before starting the GUI we copy that client once per flet-desktop version,
rename it to Scripto, swap in our icon, re-sign it ad-hoc and point
``FLET_VIEW_PATH`` at the copy. Every failure falls back to the stock
viewer: branding must never block a launch.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import update
from .paths import data_dir

logger = logging.getLogger(__name__)

VIEWER_APP_NAME = "Scripto.app"
VIEWER_BUNDLE_ID = "local.scripto.viewer"
STOCK_BUNDLE_ID = "com.appveyor.flet"
# Bump to force a rebuild of existing branded copies (a new bundle path
# also sidesteps macOS icon caches).
BRAND_REVISION = 2


def ensure_branded_viewer() -> Path | None:
    """Directory to use as FLET_VIEW_PATH; None = keep the stock viewer."""
    if sys.platform != "darwin":
        return None
    try:
        return _build_if_needed()
    except Exception:
        logger.warning("viewer branding failed; using the stock Flet viewer",
                       exc_info=True)
        return None


def rebrand_bundle(app: Path, icon: Path | None) -> None:
    """Renames a copied viewer bundle to Scripto and swaps its icon file.

    Pure file surgery (no signing) so it is testable on any platform.
    """
    info_path = app / "Contents/Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info["CFBundleName"] = "Scripto"
    info["CFBundleDisplayName"] = "Scripto"
    info["CFBundleIdentifier"] = VIEWER_BUNDLE_ID
    if icon is not None:
        icon_name = str(info.get("CFBundleIconFile", "AppIcon"))
        if not icon_name.endswith(".icns"):
            icon_name += ".icns"
        shutil.copyfile(icon, app / "Contents/Resources" / icon_name)
        # With CFBundleIconName present, macOS takes the icon from the
        # compiled Assets.car (stock Flet art) and ignores the .icns —
        # drop the key so CFBundleIconFile wins.
        info.pop("CFBundleIconName", None)
    info_path.write_bytes(plistlib.dumps(info))


def hide_viewer_app(
    bundle_ids: tuple[str, ...] = (VIEWER_BUNDLE_ID, STOCK_BUNDLE_ID),
) -> bool:
    """Hides the whole viewer app, ⌘H-style: the window disappears (no
    minimized thumbnail) while the Dock icon stays, and clicking the Dock
    icon makes macOS unhide it — no permissions involved. Unlike hiding
    the *window* (``page.window.visible = False``), which macOS treats as
    closing it and thereby ends the flet session, hiding the *app* leaves
    the window open underneath, so jobs keep running.

    Returns False when unavailable (non-macOS, viewer not found) — the
    caller falls back to minimizing.
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSRunningApplication

        # Hide every matching instance: after crashes or dev restarts a
        # stale (possibly already-hidden) copy can linger, and picking just
        # the first match could target the wrong one and fail spuriously.
        hidden_any = False
        found_any = False
        for bundle_id in bundle_ids:
            for app in NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                bundle_id
            ):
                found_any = True
                if app.isHidden() or app.hide():
                    hidden_any = True
        if not found_any:
            logger.warning("no viewer app found to hide (bundle ids: %s)",
                           ", ".join(bundle_ids))
        return hidden_any
    except Exception:
        logger.warning("hiding the viewer app failed", exc_info=True)
    return False


def _build_if_needed() -> Path | None:
    from flet_desktop import ensure_client_cached

    source_dir = Path(ensure_client_cached())
    source_app = next(
        (source_dir / name for name in os.listdir(source_dir)
         if name.endswith(".app")),
        None,
    )
    if source_app is None:
        return None

    # Keyed by the cache-dir name (e.g. flet-desktop-full-0.86.0) plus the
    # branding revision: a flet upgrade via `uv sync` or a branding change
    # gets a fresh copy, old versions are pruned.
    viewers = data_dir() / "viewer"
    dest = viewers / f"{source_dir.name}-r{BRAND_REVISION}"
    if (dest / VIEWER_APP_NAME / "Contents/MacOS").is_dir():
        return dest

    work = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    app = work / VIEWER_APP_NAME
    # ditto preserves signatures/xattrs of everything we do not touch.
    _run("ditto", str(source_app), str(app))
    rebrand_bundle(app, _make_icns(work))
    # The plist edit invalidated the signature; a fresh ad-hoc one is
    # required for the bundle to launch at all on Apple silicon.
    _run("codesign", "--force", "--deep", "-s", "-", str(app))

    shutil.rmtree(dest, ignore_errors=True)
    work.rename(dest)
    for old in viewers.iterdir():
        if old != dest:
            shutil.rmtree(old, ignore_errors=True)
    return dest


def _make_icns(work: Path) -> Path | None:
    """Scripto's icon as .icns, generated by the repo's icon script; None
    (keep the stock icon) when not running from a checkout."""
    root = update.repo_root()
    generator = (root / "launchers/macos/make_icon.py") if root else None
    if generator is None or not generator.is_file():
        return None
    iconset = work / "AppIcon.iconset"
    _run(sys.executable, str(generator), str(iconset))
    icns = work / "AppIcon.icns"
    _run("iconutil", "-c", "icns", str(iconset), "-o", str(icns))
    return icns


def _run(*args: str) -> None:
    subprocess.run(list(args), check=True, capture_output=True, timeout=120)
