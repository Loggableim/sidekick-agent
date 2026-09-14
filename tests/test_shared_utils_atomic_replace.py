from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


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


def test_atomic_json_write_cleans_up_tmp_when_serialization_fails(tmp_path):
    """Regression: tmp_name was only assigned after json.dump succeeded, so a
    serialization error (TypeError) hit the cleanup handler before the variable
    was bound — the handler raised UnboundLocalError (swallowed by the bare
    except) and the already-created tmp file leaked on disk."""
    from shared.utils import atomic_json_write

    target = tmp_path / "config.json"
    target.write_text('{"old": 1}', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_json_write(target, {"bad": object()})

    leftovers = [p for p in tmp_path.iterdir() if p.name != target.name]
    assert leftovers == [], f"tmp leak after failed serialization: {[p.name for p in leftovers]}"
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": 1}


def test_atomic_yaml_write_cleans_up_tmp_when_serialization_fails(tmp_path):
    """Same unbound-tmp_name defect in atomic_yaml_write: yaml.safe_dump
    raises RepresenterError for unrepresentable objects and leaked the tmp."""
    from shared.utils import atomic_yaml_write

    target = tmp_path / "hub.yaml"
    target.write_text("old: 1\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        atomic_yaml_write(target, {"bad": object()})

    leftovers = [p for p in tmp_path.iterdir() if p.name != target.name]
    assert leftovers == [], f"tmp leak after failed serialization: {[p.name for p in leftovers]}"
    assert target.read_text(encoding="utf-8") == "old: 1\n"


def test_atomic_json_write_success_path_still_publishes(tmp_path):
    from shared.utils import atomic_json_write

    target = tmp_path / "state.json"
    atomic_json_write(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
