from __future__ import annotations

from pathlib import Path

from nova.actions import ActionRegistry


def test_legacy_aces_cycle_requires_host_admission_and_never_runs_runner(tmp_path: Path) -> None:
    calls: list[object] = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("legacy ACES runner must remain unreachable")

    result = ActionRegistry(tmp_path, runner=runner).execute(
        {
            "action": "aces_cycle",
            "yolo_enabled": True,
            "payload": {"apply": True},
        },
        {},
    )

    assert result["ok"] is False
    assert result["status"] == "host_admission_required"
    assert calls == []
