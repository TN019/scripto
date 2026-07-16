import os
import sys

import pytest

from scripto.core.errors import ScriptoError
from scripto.media import access


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


def test_icloud_placeholder_detected(tmp_path, monkeypatch):
    f = tmp_path / "cloud.mp4"
    f.write_bytes(b"data")
    real = f.stat()

    class FakeStat:
        st_size = real.st_size
        st_blocks = 0
        st_mode = real.st_mode

    monkeypatch.setattr(access.Path, "stat", lambda self: FakeStat())
    with pytest.raises(ScriptoError) as excinfo:
        access.check_readable(f)
    assert _key_of(excinfo) == "errors.icloud_placeholder"


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
