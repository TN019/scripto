import pytest

from scripto.core.errors import ScriptoError
from scripto.engines import models


def test_preset_keys_are_unique_and_repos_set():
    keys = [spec.key for spec in models.PRESETS]
    assert len(keys) == len(set(keys))
    for spec in models.PRESETS:
        assert spec.mlx_repo and spec.fw_repo and spec.size_hint and spec.label


def test_default_config_model_exists_in_registry():
    from scripto.core.config import DEFAULTS

    assert models.get_spec(DEFAULTS["whisper_model"])


def test_get_spec_unknown_raises():
    with pytest.raises(ScriptoError) as excinfo:
        models.get_spec("nope")
    assert excinfo.value.key == "errors.unknown_model"


def test_repo_for_selects_per_engine():
    spec = models.get_spec("tiny")
    assert models.repo_for(spec, "mlx") == spec.mlx_repo
    assert models.repo_for(spec, "faster-whisper") == spec.fw_repo
    with pytest.raises(ScriptoError):
        models.repo_for(spec, "unknown-engine")


def test_installed_keys_empty_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert models.installed_keys("mlx") == set()
    assert models.installed_keys("faster-whisper") == set()
