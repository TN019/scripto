import json

from scripto.core.history import HistoryEntry, HistoryStore


def _entry(source="/tmp/a.mp4", status="done"):
    return HistoryEntry(
        source=source,
        outputs=[{"lang": "en", "format": "srt", "path": "/tmp/a.en.srt"}],
        model="tiny",
        engine="mlx",
        status=status,
        duration_sec=12.5,
    )


def test_append_and_read_newest_first(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.append(_entry(source="/one.mp4"))
    store.append(_entry(source="/two.mp4"))
    entries = store.entries()
    assert [e.source for e in entries] == ["/two.mp4", "/one.mp4"]
    assert entries[0].created_at  # stamped automatically


def test_remove_by_id(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.append(_entry(source="/one.mp4"))
    store.append(_entry(source="/two.mp4"))
    keep, drop = store.entries()[0], store.entries()[1]
    assert store.remove({drop.id}) == 1
    remaining = store.entries()
    assert [e.id for e in remaining] == [keep.id]


def test_corrupt_history_quarantined(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{broken", encoding="utf-8")
    store = HistoryStore(path)
    assert store.entries() == []
    assert list(tmp_path.glob("history.json.corrupt-*"))
    store.append(_entry())
    assert len(store.entries()) == 1


def test_unknown_fields_tolerated(tmp_path):
    path = tmp_path / "history.json"
    store = HistoryStore(path)
    store.append(_entry())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"][0]["from_the_future"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    assert len(store.entries()) == 1
