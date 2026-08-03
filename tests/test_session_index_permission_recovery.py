from pathlib import Path

def test_locked_session_index_fails_safe_without_overwriting(monkeypatch, tmp_path: Path):
    from web.api import models
    index = tmp_path / "_index.json"
    index.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(models, "_session_index_file", lambda: index)
    original = models._read_index_entries_cached
    monkeypatch.setattr(models, "_read_index_entries_cached", lambda *args: (_ for _ in ()).throw(PermissionError("WinError 5")))
    assert models._read_index_entry_map() == {}
    assert index.read_text(encoding="utf-8") == "[]"
    monkeypatch.setattr(models, "_read_index_entries_cached", original)
