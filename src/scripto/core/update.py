"""In-app update: git-pull the checkout and restart.

The launchers run Scripto straight from the git checkout via ``uv run``, so
updating is exactly: fetch, fast-forward pull, relaunch — ``uv run`` syncs
dependencies on the next start. Everything here refuses to touch a checkout
it does not fully understand (dirty tree, no upstream, non-ff history): the
update button must never wreck a dev environment.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path | None:
    """The git checkout this code runs from; None when not in a checkout."""
    root = Path(__file__).resolve().parents[3]
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        return root
    return None


@dataclass
class UpdateStatus:
    ok: bool  # the check itself succeeded
    behind: int = 0  # commits behind (upstream, or the release branch)
    dirty: bool = False  # local uncommitted changes
    detail: str = ""  # short human-readable error when not ok
    branch: str = ""  # currently checked-out branch
    release_branch: str = "main"  # the branch in-app updates track


def check(root: Path) -> UpdateStatus:
    """Fetches upstream and reports how far behind this checkout is.

    Updates track the release branch: on any other branch (a dev checkout)
    the count is informational — HEAD vs the release branch — and the GUI
    says "switch to main" instead of pretending everything is up to date.
    """
    dirty = _is_dirty(root)
    branch_proc = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""
    fetch = _git(root, "fetch", "--quiet", timeout=120)
    if fetch.returncode != 0:
        return UpdateStatus(ok=False, dirty=dirty, branch=branch,
                            detail=_last_line(fetch.stderr))

    release = _release_branch(root)
    if branch != release:
        behind = _git(root, "rev-list", "--count", f"HEAD..origin/{release}")
        count = int(behind.stdout.strip() or "0") if behind.returncode == 0 else 0
        return UpdateStatus(ok=True, behind=count, dirty=dirty,
                            branch=branch, release_branch=release)

    behind = _git(root, "rev-list", "--count", "HEAD..@{upstream}")
    if behind.returncode != 0:
        return UpdateStatus(ok=False, dirty=dirty, branch=branch,
                            release_branch=release,
                            detail=_last_line(behind.stderr))
    return UpdateStatus(ok=True, behind=int(behind.stdout.strip() or "0"),
                        dirty=dirty, branch=branch, release_branch=release)


def _release_branch(root: Path) -> str:
    """The remote's default branch (what updates follow); main as fallback."""
    proc = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().split("/", 1)[-1]
    return "main"


def pull(root: Path) -> tuple[bool, str]:
    """Fast-forwards the checkout; (ok, last output/error line)."""
    if _is_dirty(root):  # re-checked here: state may have changed since check()
        return False, "local changes"
    proc = _git(root, "pull", "--ff-only", timeout=300)
    if proc.returncode != 0:
        return False, _last_line(proc.stderr) or _last_line(proc.stdout)
    return True, _last_line(proc.stdout)


def spawn_restart(root: Path) -> None:
    """Starts a fresh detached ``uv run scripto``; the caller then closes
    this instance. The new process re-resolves dependencies, so a pulled
    lockfile change is picked up without any extra step."""
    subprocess.Popen(
        ["uv", "run", "scripto"],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ},
    )


def _is_dirty(root: Path) -> bool:
    return bool((_git(root, "status", "--porcelain").stdout or "").strip())


def _git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _last_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""
