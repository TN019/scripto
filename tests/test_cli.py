import pytest

from scripto import __version__, app, cli


def test_info_prints_locations(capsys):
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "config.json" in out


def test_info_respects_chinese_language(capsys, isolated_data_dir):
    from scripto.core.config import ConfigService

    ConfigService().update(language="zh")
    cli.main(["info"])
    out = capsys.readouterr().out
    assert "配置文件" in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "scripto-cli" in capsys.readouterr().out


def test_gui_entry_importable():
    # app.main() launches the Flet GUI; here we only verify the entry exists.
    assert callable(app.main)
