import pytest

from scripto.core import paths


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test runs against a throwaway data dir, never the real one."""
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "data"))
    return tmp_path / "data"
