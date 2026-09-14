"""Regression tests for _write_json tmp-file lifecycle in web/api/nova_lifecycle.py.

Bug (observed 2026-09-14, 15 orphaned files / ~1.8 GB in a Nova space's
``.lifecycle/`` directory): when ``tmp.replace(path)`` keeps failing with
``PermissionError`` — the normal Windows symptom of another process holding
the destination file open — the retry loop re-raises after 5 attempts and the
fully-written tmp copy (up to ~174 MB for ``events.json``) is never deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _locked_replace_factory(target: Path, fail_times: int):
    """Return a Path.replace stand-in that fails for *target* like Windows does
    when another process holds the destination open (PermissionError), then
    lets the real replace through."""
    real_replace = Path.replace
    attempts = {"n": 0}

    def _replace(self: Path, other):
        if Path(other) == target and attempts["n"] < fail_times:
            attempts["n"] += 1
            raise PermissionError(13, "Der Prozess kann nicht auf die Datei zugreifen")
        return real_replace(self, other)

    return _replace, attempts


def test_write_json_cleans_up_tmp_when_destination_stays_locked(tmp_path):
    from web.api import nova_lifecycle as lifecycle

    target = tmp_path / "events.json"
    target.write_text('{"v": 1}', encoding="utf-8")
    locked_replace, attempts = _locked_replace_factory(target, fail_times=10**9)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "replace", locked_replace)
        with pytest.raises(PermissionError):
            lifecycle._write_json(target, {"v": 2})

    # The bug: every failed replace left a fully written events.json.<uuid>.tmp
    leftovers = list(tmp_path.glob("events.json.*.tmp"))
    assert leftovers == [], f"tmp leak after failed replace: {[p.name for p in leftovers]}"
    assert attempts["n"] == 5  # retry loop exhausted before re-raising
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}  # destination untouched


def test_write_json_success_leaves_no_tmp_behind(tmp_path):
    from web.api import nova_lifecycle as lifecycle

    target = tmp_path / "state.json"
    lifecycle._write_json(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_json_retries_until_lock_is_released(tmp_path):
    from web.api import nova_lifecycle as lifecycle

    target = tmp_path / "events.json"
    target.write_text('{"v": 1}', encoding="utf-8")
    flaky_replace, attempts = _locked_replace_factory(target, fail_times=2)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "replace", flaky_replace)
        lifecycle._write_json(target, {"v": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}
    assert attempts["n"] == 2  # two locked attempts, third one succeeded
    assert list(tmp_path.glob("*.tmp")) == []