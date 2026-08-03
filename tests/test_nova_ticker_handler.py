from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.ticker_handler import consume_pending_events
from swarm_core.store import ProjectSwarmStore


def _governance(root: Path, *, enrolled: bool = True) -> ManagedSpaceGovernance:
    return ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()),
        canonical_root=root,
        root_fingerprint="",
        yolo=True,
        enrolled=enrolled,
        revision=1,
        policy_identity="space-governance:1",
    )


def test_consumer_dispatches_pending_event_once_and_marks_feed_terminal(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records.get(target),
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
    )

    assert runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )
    first = consume_pending_events(supervisor=supervisor, runtime=runtime)
    second = consume_pending_events(supervisor=supervisor, runtime=runtime)

    assert first.pending_spaces == ("alpha",)
    assert [item.status for item in first.outcomes] == ["started"]
    assert second.outcomes == ()
    assert len(dispatched) == 1
    log = (tmp_path / "ticker_events.jsonl").read_text(encoding="utf-8")
    assert '"status":"pending"' in log
    assert '"status":"handled"' in log


def test_resonance_projection_advances_to_terminal_outcome(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records.get(target),
    )
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda *_: None)
    assert runtime.ingest_signal("alpha", source="git", event_id="commit-2", reason_code="git_change")

    consume_pending_events(supervisor=supervisor, runtime=runtime)

    from nova.resonance_memory import TickerResonanceMemory
    events = TickerResonanceMemory(supervisor=supervisor).events()
    assert len(events) == 1
    assert events[0]["stage"] == "handled"
    assert events[0]["status"] == "handled"
    assert events[0]["reason"] == "started"


def test_numeric_heartbeat_uses_same_terminal_resonance_identity(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records.get(target),
    )
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda *_: None)
    assert runtime.ingest_signal(
        "alpha", source="heartbeat", event_id="heartbeat:1", reason_code="periodic_check"
    )

    result = consume_pending_events(supervisor=supervisor, runtime=runtime)
    assert result.outcomes
    from nova.resonance_memory import TickerResonanceMemory
    events = TickerResonanceMemory(supervisor=supervisor).events()
    assert len(events) == 1
    assert events[0]["source"] == "heartbeat"
    assert events[0]["stage"] == "handled"
    assert events[0]["status"] == "handled"


def test_entity_sink_receives_only_redacted_space_signal(monkeypatch) -> None:
    captured = []
    class FakeKernel:
        def perceive(self, event):
            captured.append(event)
            return {"event_id": event["event_id"]}
    import nova.entity_kernel
    monkeypatch.setattr(nova.entity_kernel, "EntityKernel", FakeKernel)
    from nova.ticker_handler import _publish_to_nova_entity
    assert _publish_to_nova_entity({
        "event_id": "d" * 64, "space": "alpha", "source": "ci",
        "stage": "handled", "status": "failed", "reason": "ci_failed",
        "observed_at": 4.0, "path": "C:/secret", "token": "secret",
    })
    assert captured[0]["payload"] == {
        "space": "alpha", "source": "ci", "stage": "handled",
        "status": "failed", "reason": "ci_failed",
    }
    assert "secret" not in str(captured[0])


def test_consumer_skips_non_enrolled_event_without_dispatch(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha", enrolled=False)}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records.get(target),
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
    )
    path = tmp_path / "ticker_events.jsonl"
    path.write_text(
        '{"event_id":"e1","space":"alpha","source":"git","reason":"git_change","stage":"observed","status":"pending"}\n',
        encoding="utf-8",
    )

    result = consume_pending_events(supervisor=supervisor, runtime=runtime)

    assert result.pending_spaces == ()
    assert result.outcomes == ()
    assert dispatched == []

def test_consumer_ignores_forged_observed_line_without_ledger_identity(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records.get(target),
    )
    path = tmp_path / "ticker_events.jsonl"
    path.write_text(
        '{"event_id":"e1","space":"alpha","source":"git","reason":"git_change","stage":"observed","status":"pending"}\n',
        encoding="utf-8",
    )

    # A local ticker file is not an authority: only the durable signal digest
    # accepted by ingest_signal may wake an enrolled Space.
    from nova.ticker_handler import _pending_events_from_log

    assert _pending_events_from_log(supervisor) == {}

def test_consumer_pulses_snapshot_provider_when_ledger_has_no_pending_rows(tmp_path: Path) -> None:
    """A quiet enrolled YOLO Space gets its first periodic intent on a host tick."""
    root = tmp_path / "alpha"
    records = {"alpha": _governance(root)}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda project_root, run_id: dispatched.append((project_root, run_id)),
        governance_snapshots=lambda: records,
    )

    result = consume_pending_events(supervisor=supervisor, runtime=runtime)

    assert [outcome.status for outcome in result.outcomes] == ["started"]
    assert result.outcomes[0].target_key == "alpha"
    assert dispatched and dispatched[0][0] == root

    # The next host tick does not duplicate the run while its global slot is active.
    assert consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes == ()
    assert len(dispatched) == 1

def test_identical_snapshot_intent_is_not_duplicated_across_two_heartbeats(tmp_path: Path) -> None:
    """A completed periodic check is not replayed by the next host heartbeat."""
    root = tmp_path / "alpha"
    records = {"alpha": _governance(root)}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    dispatched: list[tuple[Path, str]] = []

    def fake_dispatch(project_root: Path, run_id: str) -> None:
        # Completion is reported after host admission returns so the start
        # boundary can observe the child in its required running state.
        dispatched.append((project_root, run_id))

    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=fake_dispatch,
        governance_snapshots=lambda: records,
    )

    first = consume_pending_events(supervisor=supervisor, runtime=runtime)
    assert [item.status for item in first.outcomes] == ["started"]
    assert len(dispatched) == 1
    ProjectSwarmStore(root).set_run_status(dispatched[0][1], "completed")
    assert supervisor.record_completion(dispatched[0][1]) is True
    second = consume_pending_events(supervisor=supervisor, runtime=runtime)
    assert second.outcomes == ()
    assert len(dispatched) == 1
    # Even at the 15-minute quiet boundary, the same governance/intent digest
    # is recorded as unchanged rather than starting an identical run.
    quiet = runtime.pulse(now_epoch=(runtime.status()[0].last_started_at or 0) + 900.0)
    assert [item.status for item in quiet] == ["unchanged"]
    assert len(dispatched) == 1