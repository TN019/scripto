import os
import sys
import time

import pytest

from scripto.core.errors import OperationStopped, ScriptoError
from scripto.media import access

UF_COMPRESSED = 0x00000020


class FakeStat:
    """Stand-in for os.stat_result carrying only the fields access.py reads."""

    def __init__(self, size: int, *, flags: int = 0, blocks: int = 8):
        self.st_size = size
        self.st_flags = flags
        self.st_blocks = blocks
        self.st_mode = 0o100644


def _key_of(excinfo) -> str:
    return excinfo.value.key


def test_readable_file_passes(tmp_path):
    f = tmp_path / "ok.mp4"
    f.write_bytes(b"data")
    access.check_readable(f)  # must not raise


def test_missing_file(tmp_path):
    with pytest.raises(ScriptoError) as excinfo:
        access.check_readable(tmp_path / "gone.mp4")
    assert _key_of(excinfo) == "errors.file_missing"


def test_empty_file(tmp_path):
    f = tmp_path / "empty.mp4"
    f.write_bytes(b"")
    with pytest.raises(ScriptoError) as excinfo:
        access.check_readable(f)
    assert _key_of(excinfo) == "errors.file_empty"


def _fake_stat(monkeypatch, stat: FakeStat) -> None:
    monkeypatch.setattr(access.Path, "stat", lambda self: stat)


def test_dataless_flag_marks_a_file_as_not_downloaded(tmp_path, monkeypatch):
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"data")
    _fake_stat(monkeypatch, FakeStat(999, flags=access.UF_DATALESS, blocks=0))
    assert access.needs_download(f)


def test_compressed_local_file_is_not_mistaken_for_a_cloud_file(tmp_path, monkeypatch):
    """APFS-compressed files also report zero blocks — the old false positive."""
    f = tmp_path / "local.mp4"
    f.write_bytes(b"data")
    _fake_stat(monkeypatch, FakeStat(999, flags=UF_COMPRESSED, blocks=0))
    assert not access.needs_download(f)


def test_check_readable_lets_a_cloud_file_through(tmp_path, monkeypatch):
    """Not downloaded is a download to do, not a failure to report."""
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"data")
    _fake_stat(monkeypatch, FakeStat(4, flags=access.UF_DATALESS, blocks=0))
    access.check_readable(f)  # must not raise


def test_materialize_pulls_the_file_down_by_reading_it(tmp_path, monkeypatch):
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"x" * 64)
    opened: list = []
    real_open = access.Path.open

    def spy_open(self, *args, **kwargs):
        opened.append(self)
        return real_open(self, *args, **kwargs)

    _fake_stat(monkeypatch, FakeStat(64, flags=access.UF_DATALESS, blocks=0))
    monkeypatch.setattr(access.Path, "open", spy_open)

    seen: list[tuple[int, int]] = []
    access.materialize(f, on_progress=lambda done, total: seen.append((done, total)))
    assert opened == [f]          # reading is what triggers the download
    assert seen[-1] == (64, 64)   # and it finishes reported as complete


def test_materialize_skips_a_file_already_on_disk(tmp_path, monkeypatch):
    f = tmp_path / "local.mp4"
    f.write_bytes(b"x" * 64)
    opened: list = []
    monkeypatch.setattr(
        access.Path, "open", lambda self, *a, **k: opened.append(self)
    )
    _fake_stat(monkeypatch, FakeStat(64, flags=0, blocks=8))
    access.materialize(f)
    assert opened == []


def test_materialize_times_out_instead_of_hanging(tmp_path, monkeypatch):
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"x")
    _fake_stat(monkeypatch, FakeStat(1, flags=access.UF_DATALESS, blocks=0))
    monkeypatch.setattr(
        access.Path, "open", lambda self, *a, **k: time.sleep(1.0)
    )
    with pytest.raises(ScriptoError) as excinfo:
        access.materialize(f, timeout_sec=0.1)
    assert _key_of(excinfo) == "errors.icloud_timeout"


def test_materialize_honours_a_stop_request(tmp_path, monkeypatch):
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"x")
    _fake_stat(monkeypatch, FakeStat(1, flags=access.UF_DATALESS, blocks=0))
    monkeypatch.setattr(
        access.Path, "open", lambda self, *a, **k: time.sleep(1.0)
    )
    with pytest.raises(OperationStopped):
        access.materialize(f, stop_check=lambda: True)


def test_materialize_reports_a_failed_download(tmp_path, monkeypatch):
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"x")
    _fake_stat(monkeypatch, FakeStat(1, flags=access.UF_DATALESS, blocks=0))

    def offline(self, *args, **kwargs):
        raise OSError("network is down")

    monkeypatch.setattr(access.Path, "open", offline)
    with pytest.raises(ScriptoError) as excinfo:
        access.materialize(f)
    assert _key_of(excinfo) == "errors.icloud_failed"


def test_evict_never_raises_on_an_ordinary_file(tmp_path):
    f = tmp_path / "local.mp4"
    f.write_bytes(b"data")
    assert access.evict(f) is False   # not an iCloud item: nothing to do
    assert f.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_permission_denied_plain(tmp_path):
    f = tmp_path / "locked.mp4"
    f.write_bytes(b"data")
    f.chmod(0)
    try:
        with pytest.raises(ScriptoError) as excinfo:
            access.check_readable(f)
        assert _key_of(excinfo) == "errors.file_permission"
    finally:
        f.chmod(0o644)


@pytest.mark.skipif(sys.platform != "darwin", reason="TCC hint is macOS-only")
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_permission_denied_under_desktop_hints_tcc(tmp_path):
    desktop = tmp_path / "Desktop" / "test"
    desktop.mkdir(parents=True)
    f = desktop / "video.mp4"
    f.write_bytes(b"data")
    f.chmod(0)
    try:
        with pytest.raises(ScriptoError) as excinfo:
            access.check_readable(f)
        assert _key_of(excinfo) == "errors.tcc_denied"
        assert "Privacy & Security" in str(excinfo.value)
    finally:
        f.chmod(0o644)
