from __future__ import annotations

from pathlib import Path


def test_atomic_replace_retries_transient_permission_error(monkeypatch, tmp_path):
    from shared.utils import atomic_replace

    source = tmp_path / "source.tmp"
    target = tmp_path / "target.json"
    source.write_text("updated", encoding="utf-8")
    target.write_text("old", encoding="utf-8")

    calls: list[tuple[str, str]] = []
    attempts = {"count": 0}

    def fake_replace(src, dest):
        calls.append((str(src), str(dest)))
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("target file is busy")
        src_path = Path(src)
        dest_path = Path(dest)
        dest_path.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")
        src_path.unlink()

    monkeypatch.setattr("shared.utils.os.replace", fake_replace)

    result = atomic_replace(source, target)

    assert result == str(target)
    assert target.read_text(encoding="utf-8") == "updated"
    assert not source.exists()
    assert len(calls) == 3
