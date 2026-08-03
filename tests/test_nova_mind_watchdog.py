from pathlib import Path
import json

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
