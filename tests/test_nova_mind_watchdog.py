from pathlib import Path
import json

import pytest

from nova.mind_watchdog import check_and_recover


class Proc:
    pid = 4321


def setup_files(tmp_path: Path, *, pid=1234, status="alive", age=0):
    root = tmp_path / "spaces" / "nova"
    (root / "nova_data" / "runtime").mkdir(parents=True)
    (root / "nova-site").mkdir()
    (root / "nova_mind.py").write_text("# test", encoding="utf-8")
    (root / "nova_data" / "runtime" / "nova_mind.pid.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
    status_path = root / "nova-site" / "nova-status.json"
    status_path.write_text(json.dumps({"status": status}), encoding="utf-8")
    if age:
        import os
        os.utime(status_path, (age, age))


def test_healthy_does_not_spawn(tmp_path):
    setup_files(tmp_path)
    calls = []
    result = check_and_recover(home=tmp_path, lease_owned=True, now=100, pid_alive=lambda pid, target: True, popen=lambda *a, **k: calls.append(a))
    assert result.status == "healthy"
    assert calls == []


def test_dead_without_lease_is_not_restarted(tmp_path):
    setup_files(tmp_path)
    result = check_and_recover(home=tmp_path, lease_owned=False, now=100, pid_alive=lambda pid, target: False)
    assert result.status == "not_lease_holder"


def test_dead_with_lease_restarts_fixed_command(tmp_path):
    setup_files(tmp_path)
    calls = []
    result = check_and_recover(home=tmp_path, lease_owned=True, now=100, pid_alive=lambda pid, target: False, popen=lambda *a, **k: (calls.append((a, k)) or Proc()))
    assert result.status == "restarted"
    assert calls[0][0][0][-1].endswith("nova_mind.py")
    assert calls[0][1]["cwd"].endswith("spaces\\nova") or calls[0][1]["cwd"].endswith("spaces/nova")


def test_three_recent_crashes_escalate(tmp_path):
    setup_files(tmp_path)
    state = tmp_path / "state" / "nova-mind-watchdog.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"crash_timestamps": [1, 50, 99]}), encoding="utf-8")
    result = check_and_recover(home=tmp_path, lease_owned=True, now=100, pid_alive=lambda pid, target: False, popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert result.status == "escalated"


def test_old_crashes_are_pruned(tmp_path):
    setup_files(tmp_path)
    state = tmp_path / "state" / "nova-mind-watchdog.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"crash_timestamps": [-1000]}), encoding="utf-8")
    result = check_and_recover(home=tmp_path, lease_owned=True, now=100, pid_alive=lambda pid, target: False, popen=lambda *a, **k: Proc())
    assert result.status == "restarted" and result.crash_count == 1


def test_missing_target(tmp_path):
    setup_files(tmp_path)
    (tmp_path / "spaces" / "nova" / "nova_mind.py").unlink()
    result = check_and_recover(home=tmp_path, lease_owned=True, now=100, pid_alive=lambda pid, target: False)
    assert result.status == "missing_target"


def test_atomic_json_cleans_up_tmp_when_destination_locked(tmp_path):
    """Regression: _atomic_json left the tmp file behind when os.replace
    failed with PermissionError (destination held open by another process
    on Windows). The watchdog writes state on every crash/restart event,
    so each write under a locked destination leaked a full copy."""
    import sys

    from nova.mind_watchdog import _atomic_json

    if sys.platform != "win32":
        pytest.skip("exclusive-handle replace blocking is a Windows behaviour")
    import ctypes

    target = tmp_path / "nova-mind-watchdog.json"
    target.write_text('{"old": 1}', encoding="utf-8")

    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = -1
    handle = ctypes.windll.kernel32.CreateFileW(
        str(target), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None
    )
    assert handle != INVALID_HANDLE_VALUE, "could not open exclusive test handle"
    try:
        with pytest.raises(PermissionError):
            _atomic_json(target, {"crash_timestamps": [1, 2, 3], "status": "restarted"})

        leftovers = [p for p in tmp_path.iterdir() if p.name != target.name]
        assert leftovers == [], f"tmp leak after failed replace: {[p.name for p in leftovers]}"
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    # After the lock is released the next write must succeed and publish.
    _atomic_json(target, {"crash_timestamps": [1], "status": "restarted"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"crash_timestamps": [1], "status": "restarted"}
    assert [p.name for p in tmp_path.iterdir()] == ["nova-mind-watchdog.json"]


def test_atomic_json_success_leaves_no_tmp_behind(tmp_path):
    from nova.mind_watchdog import _atomic_json

    target = tmp_path / "state.json"
    _atomic_json(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
