import pytest

from scripto.core.errors import OperationStopped
from scripto.core import scanner


@pytest.fixture
def media_tree(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "sub").mkdir()
    (tmp_path / "b").mkdir()
    files = {
        "top": tmp_path / "a" / "lecture one.mp4",
        "nested": tmp_path / "a" / "sub" / "deep.m4a",
        "other_dir": tmp_path / "b" / "talk.mkv",
        "unsupported": tmp_path / "a" / "notes.txt",
    }
    for f in files.values():
        f.write_bytes(b"x")
    return tmp_path, files


def test_mixed_files_and_dirs_from_different_roots(media_tree):
    root, files = media_tree
    result = scanner.scan([str(root / "a"), str(files["other_dir"])])
    assert files["top"] in result.files
    assert files["nested"] in result.files
    assert files["other_dir"] in result.files
    assert files["unsupported"] not in result.files


def test_non_recursive_skips_subfolders(media_tree):
    root, files = media_tree
    result = scanner.scan([str(root / "a")], recursive=False)
    assert files["top"] in result.files
    assert files["nested"] not in result.files


def test_deduplicates_across_specs(media_tree):
    root, files = media_tree
    result = scanner.scan([str(root / "a"), str(files["top"])])
    assert result.files.count(files["top"]) == 1


def test_shell_escaped_and_quoted_paths(media_tree):
    _root, files = media_tree
    escaped = str(files["top"]).replace(" ", "\\ ")
    quoted = f'"{files["top"]}"'
    for raw in (escaped, quoted):
        result = scanner.scan([raw])
        assert result.files == [files["top"]]


def test_file_url_normalization(media_tree):
    _root, files = media_tree
    result = scanner.scan([files["top"].as_uri()])
    assert result.files == [files["top"]]


def test_missing_and_unsupported_produce_warnings(media_tree, tmp_path):
    _root, files = media_tree
    result = scanner.scan(["/no/such/path.mp4", str(files["unsupported"])])
    assert result.files == []
    assert any(w.startswith("scan.missing:") for w in result.warnings)
    assert any(w.startswith("scan.unsupported:") for w in result.warnings)


def test_empty_dir_warning(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = scanner.scan([str(empty)])
    assert any(w.startswith("scan.empty_dir:") for w in result.warnings)


def test_stop_check_aborts(media_tree):
    root, _files = media_tree
    with pytest.raises(OperationStopped):
        scanner.scan([str(root)], stop_check=lambda: True)


def test_stable_order(media_tree):
    root, _files = media_tree
    first = scanner.scan([str(root)]).files
    second = scanner.scan([str(root)]).files
    assert first == second
