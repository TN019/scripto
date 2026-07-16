import json

from scripto.core.config import DEFAULTS, ConfigService


def test_load_returns_defaults_when_file_missing(tmp_path):
    service = ConfigService(tmp_path / "config.json")
    assert service.load() == DEFAULTS


def test_save_and_load_roundtrip(tmp_path):
    service = ConfigService(tmp_path / "config.json")
    service.update(language="zh", overwrite=True)
    config = service.load()
    assert config["language"] == "zh"
    assert config["overwrite"] is True
    assert config["output_format"] == DEFAULTS["output_format"]


def test_unknown_keys_are_preserved(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"future_key": 42}), encoding="utf-8")
    service = ConfigService(path)
    assert service.load()["future_key"] == 42
    service.update(language="en")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_key"] == 42


def test_null_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"output_format": None}), encoding="utf-8")
    service = ConfigService(path)
    assert service.load()["output_format"] == DEFAULTS["output_format"]


def test_corrupt_file_quarantined_and_defaults_returned(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    service = ConfigService(path)
    assert service.load() == DEFAULTS
    backups = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_save_is_atomic_leaves_no_temp_file(tmp_path):
    service = ConfigService(tmp_path / "config.json")
    service.save(dict(DEFAULTS))
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
