"""In-app update: repo detection, behind-count check, fast-forward pull."""

import subprocess
from pathlib import Path

from scripto.core import update


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _commit(root: Path, name: str) -> None:
    (root / name).write_text(name, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", f"add {name}")


def _make_pair(tmp_path: Path) -> tuple[Path, Path]:
    """An upstream repo and a clone of it (the 'installed checkout')."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-b", "main")
    _commit(upstream, "one.txt")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(upstream), str(clone))
    return upstream, clone


def test_repo_root_finds_this_checkout():
    root = update.repo_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "src/scripto").is_dir()


def test_check_reports_behind_and_dirty(tmp_path):
    upstream, clone = _make_pair(tmp_path)

    status = update.check(clone)
    assert status.ok and status.behind == 0 and not status.dirty

    _commit(upstream, "two.txt")
    _commit(upstream, "three.txt")
    (clone / "local.txt").write_text("wip", encoding="utf-8")

    status = update.check(clone)
    assert status.ok and status.behind == 2 and status.dirty


def test_pull_fast_forwards_a_clean_clone(tmp_path):
    upstream, clone = _make_pair(tmp_path)
    _commit(upstream, "two.txt")

    ok, _detail = update.pull(clone)
    assert ok
    assert (clone / "two.txt").is_file()
    assert update.check(clone).behind == 0


def test_pull_refuses_a_dirty_clone(tmp_path):
    upstream, clone = _make_pair(tmp_path)
    _commit(upstream, "two.txt")
    (clone / "local.txt").write_text("wip", encoding="utf-8")

    ok, detail = update.pull(clone)
    assert not ok
    assert detail == "local changes"
    assert not (clone / "two.txt").exists()


def test_check_flags_a_non_release_branch(tmp_path):
    upstream, clone = _make_pair(tmp_path)
    _git(clone, "checkout", "-b", "feature")
    _commit(upstream, "two.txt")

    status = update.check(clone)
    assert status.ok
    assert status.branch == "feature"
    assert status.release_branch == "main"
    assert status.behind == 1  # informational: main moved on without us
