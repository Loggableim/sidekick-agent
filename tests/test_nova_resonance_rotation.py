from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from nova.resonance_memory import TickerResonanceMemory
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def _governance(root: Path) -> ManagedSpaceGovernance:
    return ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()), canonical_root=root, root_fingerprint="",
        yolo=True, enrolled=True, revision=1, policy_identity="space-governance:1",
    )


def _event(event_id: str, at: int) -> str:
    return json.dumps({
        "event_id": event_id, "space": "aquarium-zentrum", "source": "ci",
        "stage": "handled", "status": "failed", "reason": "ci_failed", "at": at,
    }) + "\n"


def test_rotation_without_inode_restarts_cursor_without_replaying_seen_events(
    monkeypatch, tmp_path: Path,
) -> None:
    """Windows-style zero inode rotation cannot lose the new file prefix."""
    root = tmp_path / "aquarium-zentrum"
    ticker = tmp_path / "ticker_events.jsonl"
    ticker.write_text(_event("a" * 64, 1), encoding="utf-8")
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda key: _governance(root) if key == "aquarium-zentrum" else None,
    )
    memory = TickerResonanceMemory(
        supervisor=supervisor, ticker_path=ticker, memory_path=tmp_path / "resonance.sqlite"
    )
    original_stat = Path.stat
    original_resolve = Path.resolve
    generation = {"value": 1}

    def windows_stat(self: Path, *args, **kwargs):
        stat = original_stat(self, *args, **kwargs)
        if self == ticker:
            return SimpleNamespace(
                st_ino=0,
                st_size=stat.st_size,
                st_ctime_ns=generation["value"],
            )
        return stat

    def stable_resolve(self: Path, *args, **kwargs):
        return self if self == ticker else original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", windows_stat)
    monkeypatch.setattr(Path, "resolve", stable_resolve)
    assert memory.consume().accepted == 1

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(_event("b" * 64, 2) + _event("c" * 64, 3), encoding="utf-8")
    replacement.replace(ticker)
    generation["value"] = 2

    assert memory.consume(max_events=4).accepted == 2
    assert [item["event_id"] for item in memory.events()] == ["c" * 64, "b" * 64, "a" * 64]
