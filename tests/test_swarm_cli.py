from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import threading

import pytest

from cli.swarm import build_parser, get_swarm_service, swarm_command
from cli.swarm_host import (
    OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
    SidekickSwarmService,
)
import nova.swarm_runtime_bridge as nova_bridge
from swarm_core.engine import SwarmEngine
from swarm_core.models import ModelCatalogSnapshot, ModelRequest, ModelResponse
from swarm_core.packs import PackDefinition
from swarm_core.store import ProjectSwarmStore
from swarm_core.transport import ModelTransport


_NOVA_CLI_MODELS = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "kimi-k2.6",
    "minimax-m3",
    "glm-5.2",
    "kimi-k2.7-code",
    "nemotron-3-super",
)


class _CliNovaKernel:
    def __init__(self, project_root: Path) -> None:
        self.space_dir = project_root
        self.actions = type(
            "Actions",
            (),
            {"space_dir": project_root},
        )()
        self.policy = type(
            "Policy",
            (),
            {"action_tier": lambda _self, _action: "internal"},
        )()
        self.govern_calls: list[dict[str, object]] = []
        self.act_calls: list[dict[str, object]] = []

    @staticmethod
    def is_yolo_enabled() -> bool:
        return False

    def govern(self, intent):
        self.govern_calls.append(copy.deepcopy(intent))
        return {
            "intent": copy.deepcopy(intent),
            "policy": {"allowed": True, "tier": intent["tier"]},
            "state": {},
            "autonomy": {},
        }

    def act(self, decision):
        self.act_calls.append(copy.deepcopy(decision))
        return {"executed": True}


def _paused_cli_nova_run(project: Path):
    nova_bridge.configure_nova_bridge(project, enabled=True)
    ProjectSwarmStore(project).save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=_NOVA_CLI_MODELS,
            healthy=True,
            source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
        )
    )
    kernel = _CliNovaKernel(project)
    context = nova_bridge._create_nova_bridge_context(
        project, validator=lambda candidate: candidate
    )
    paused = threading.Event()

    def pause_dispatch(project_root: Path, run_id: str) -> None:
        ProjectSwarmStore(project_root).set_run_status(run_id, "paused")
        paused.set()

    result = nova_bridge.NovaSwarmRuntimeBridge(
        kernel,
        project_root=project,
        trusted_project_root=context,
        dispatcher=pause_dispatch,
    ).submit(
        {
            "action": "mind_diary",
            "need": "continuity",
            "title": "Resume the attached Nova bridge",
            "why": "Exercise the same-process CLI continuation.",
            "target": {},
            "payload": {"content": "A deterministic CLI bridge note."},
            "priority": 0.8,
        },
        source_slot=900,
    )
    assert result.run_id is not None
    assert paused.wait(timeout=1)
    run = ProjectSwarmStore.open_read_only(project).get_run(result.run_id)
    assert run is not None and run.status == "paused"
    return kernel, context, run


def _cli_nova_service(model_calls: list[dict[str, object]]) -> SidekickSwarmService:
    @contextmanager
    def provider_slot(_run_id: str, _provider: str):
        yield

    def call_llm(**kwargs):
        model_calls.append(dict(kwargs))
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "work": "deterministic CLI bridge work",
                                "evidence": ["task8:cli"],
                                "decision": "approved",
                                "approved": True,
                            }
                        )
                    }
                }
            ]
        }

    return SidekickSwarmService(
        call_llm=call_llm,
        provider_slot=provider_slot,
        execution_options_resolver=nova_bridge.nova_execution_options_for_run,
        pause_poll_seconds=0.001,
    )


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser(subparsers)
    return parser.parse_args(argv)


def test_cli_service_installs_the_durable_nova_options_resolver():
    """Catches CLI resume bypassing the Nova required hook resolver."""
    service = get_swarm_service()
    assert service._execution_options_resolver.__name__ == "nova_execution_options_for_run"


def test_cli_resume_completes_a_same_process_nova_run_after_explicit_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Catches CLI resume discarding an explicitly reattached Nova capability."""
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/v1")
    kernel, context, run = _paused_cli_nova_run(project)
    nova_bridge._unregister_runtime_binding(project, run.run_id)
    nova_bridge.NovaSwarmRuntimeBridge(
        kernel,
        project_root=project,
        trusted_project_root=context,
    ).attach_admitted_run(run)
    model_calls: list[dict[str, object]] = []
    resume = _parse(
        ["swarm", "--project", str(project), "--json", "resume", run.run_id]
    )

    assert swarm_command(resume, service=_cli_nova_service(model_calls)) == 0

    payload = json.loads(capsys.readouterr().out)
    persisted = ProjectSwarmStore.open_read_only(project).get_run(run.run_id)
    assert payload["status"] == "completed"
    assert persisted is not None and persisted.status == "completed"
    assert len(model_calls) == 8
    assert len(kernel.govern_calls) == 1
    assert len(kernel.act_calls) == 1


def test_cli_resume_fresh_process_nova_run_blocks_before_model_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Catches durable Nova metadata minting a capability in a fresh process."""
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/v1")
    kernel, _context, run = _paused_cli_nova_run(project)
    nova_bridge._unregister_runtime_binding(project, run.run_id)
    model_calls: list[dict[str, object]] = []
    resume = _parse(
        ["swarm", "--project", str(project), "--json", "resume", run.run_id]
    )

    assert swarm_command(resume, service=_cli_nova_service(model_calls)) == 0

    payload = json.loads(capsys.readouterr().out)
    events = ProjectSwarmStore.open_read_only(project).list_events(run.run_id)
    assert payload["status"] == "paused"
    assert payload["pause_reason"] == "nova_bridge_unavailable"
    assert model_calls == []
    assert kernel.govern_calls == []
    assert kernel.act_calls == []
    assert [
        event.payload
        for event in events
        if event.event_type == "run.execution_blocked"
    ] == [{"reason": "nova_bridge_unavailable"}]


def test_cli_init_is_explicit_and_status_on_missing_project_is_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches a harmless CLI read creating .swarm before the user asks for init."""
    project = tmp_path / "project"
    project.mkdir()

    missing_status = _parse(["swarm", "--project", str(project), "--json", "status"])
    assert swarm_command(missing_status) == 1
    assert not (project / ".swarm").exists()

    initialized = _parse(["swarm", "--project", str(project), "--json", "init"])
    assert swarm_command(initialized) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_root"] == str(project.resolve())
    assert (project / ".swarm" / "swarm.yaml").is_file()


def test_cli_run_and_status_never_trigger_model_refresh(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches a convenient CLI dispatch path silently mutating the catalog."""
    calls: list[tuple[str, object]] = []

    class FakeService:
        def run(self, goal, project_root, *, pack):
            calls.append(("run", (goal, project_root, pack)))
            return {"run_id": "run-1", "status": "paused"}

        def status(self, project_root, run_id=None):
            calls.append(("status", (project_root, run_id)))
            return {"runs": []}

        def refresh_models(self, _project_root):
            raise AssertionError("run/status must not refresh models")

        def list_packs(self, _project_root):
            return []

        def pause(self, *_args):
            raise AssertionError("not expected")

        def resume(self, *_args):
            raise AssertionError("not expected")

        def record_human_approval(self, *_args, **_kwargs):
            raise AssertionError("not expected")

    project = tmp_path / "project"
    project.mkdir()
    service = FakeService()

    run_args = _parse(["swarm", "--project", str(project), "--json", "run", "inspect"])
    status_args = _parse(["swarm", "--project", str(project), "--json", "status"])

    assert swarm_command(run_args, service=service) == 0
    assert swarm_command(status_args, service=service) == 0
    assert [name for name, _value in calls] == ["run", "status"]
    assert '"run_id": "run-1"' in capsys.readouterr().out


def test_cli_approval_accepts_only_a_human_decision(
    tmp_path: Path,
):
    """Catches CLI flags forging a verifier/model quorum or caller identity."""
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(SystemExit):
        _parse(
            [
                "swarm",
                "--project",
                str(project),
                "approve",
                "run-1",
                "proposal-1",
                "--approval-type",
                "model",
            ]
        )

    approvals: list[tuple] = []

    class FakeService:
        def record_human_approval(
            self, project_root, run_id, proposal_id, *, actor_id, approved
        ):
            approvals.append((project_root, run_id, proposal_id, actor_id, approved))
            return {"approval_type": "human", "approved": approved}

    args = _parse(
        [
            "swarm",
            "--project",
            str(project),
            "--json",
            "approve",
            "run-1",
            "proposal-1",
            "--deny",
        ]
    )

    assert (
        swarm_command(
            args,
            service=FakeService(),
            actor_factory=lambda: "os:uid:4242",
        )
        == 0
    )
    assert approvals == [
        (project.resolve(), "run-1", "proposal-1", "os:uid:4242", False)
    ]


def test_cli_models_refresh_and_packs_list_are_explicit_commands(
    tmp_path: Path,
):
    """Catches refresh or pack loading being hidden in unrelated command dispatch."""
    project = tmp_path / "project"
    project.mkdir()
    calls: list[str] = []

    class FakeService:
        def refresh_models(self, project_root):
            calls.append(f"refresh:{project_root}")
            return {"healthy": True}

        def list_packs(self, project_root):
            calls.append(f"packs:{project_root}")
            return [{"id": "coding-team"}]

    refresh = _parse(["swarm", "--project", str(project), "models", "refresh"])
    packs = _parse(["swarm", "--project", str(project), "packs", "list"])

    assert swarm_command(refresh, service=FakeService()) == 0
    assert swarm_command(packs, service=FakeService()) == 0
    assert calls == [f"refresh:{project.resolve()}", f"packs:{project.resolve()}"]


def test_cli_resume_starts_the_durable_continuation_not_just_a_status_transition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches `sidekick swarm resume` leaving a restart-paused run inert."""
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, object]] = []

    class FakeService:
        def resume(self, project_root, run_id):
            calls.append(("resume", (project_root, run_id)))
            return {"run_id": run_id, "status": "running"}

        def execute_run(self, project_root, run_id):
            calls.append(("execute", (project_root, run_id)))
            return {"run_id": run_id, "status": "completed", "call_count": 9}

    args = _parse(
        ["swarm", "--project", str(project), "--json", "resume", "run-restart"]
    )

    assert swarm_command(args, service=FakeService()) == 0
    assert calls == [
        ("resume", (project.resolve(), "run-restart")),
        ("execute", (project.resolve(), "run-restart")),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "call_count": 9,
        "run_id": "run-restart",
        "status": "completed",
    }


def test_cli_resume_records_a_durable_pause_when_continuation_crashes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """A failed continuation must not strand its run in the running state."""
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, object]] = []

    class FakeService:
        def resume(self, project_root, run_id):
            calls.append(("resume", (project_root, run_id)))
            return {"run_id": run_id, "status": "running"}

        def execute_run(self, project_root, run_id):
            calls.append(("execute", (project_root, run_id)))
            raise RuntimeError("corrupt durable history")

        def record_execution_failure(self, project_root, run_id, *, error_type):
            calls.append(("failure", (project_root, run_id, error_type)))
            return {"run_id": run_id, "status": "paused"}

    args = _parse(
        ["swarm", "--project", str(project), "--json", "resume", "run-restart"]
    )

    assert swarm_command(args, service=FakeService()) == 1
    assert calls == [
        ("resume", (project.resolve(), "run-restart")),
        ("execute", (project.resolve(), "run-restart")),
        ("failure", (project.resolve(), "run-restart", "RuntimeError")),
    ]
    assert json.loads(capsys.readouterr().err) == {
        "error": "Swarm execution failed; inspect durable run status",
        "ok": False,
    }


def test_cli_resume_failure_writes_a_sanitized_durable_failure_event(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches CLI-only failures leaking provider text or stranding a run."""
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    run = store.create_run(
        run_id="cli-durable-failure",
        status="paused",
        metadata={"goal": "resume", "pack": "coding-team"},
    )

    class FailingService(SidekickSwarmService):
        def execute_run(self, _project_root, _run_id, **_callbacks):
            raise RuntimeError("provider response contained secret details")

    args = _parse(
        ["swarm", "--project", str(project), "--json", "resume", run.run_id]
    )

    assert swarm_command(args, service=FailingService()) == 1

    reader = ProjectSwarmStore.open_read_only(project)
    persisted = reader.get_run(run.run_id)
    assert persisted is not None
    assert persisted.status == "paused"
    assert [
        event.payload
        for event in reader.list_events(run.run_id)
        if event.event_type == "run.execution_failed"
    ] == [{"error_type": "RuntimeError"}]
    output = capsys.readouterr()
    assert "secret details" not in output.err


def test_cli_recover_audits_a_stale_lease_then_separate_resume_executes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches recovery auto-running or leaving the abandoned lease in place."""

    class CompletingTransport(ModelTransport):
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            return ModelResponse(
                model=request.model,
                content=f"{request.role} completed",
                data={
                    "work": f"{request.role} completed",
                    "evidence": [f"{request.role}:{request.model}"],
                    "decision": "approve",
                    "approved": True,
                },
            )

    project = tmp_path / "project"
    project.mkdir()
    transport = CompletingTransport()
    run = SwarmEngine(transport).start_run("Recover before a fresh resume", project)
    store = ProjectSwarmStore(project)
    abandoned_attempt = store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    assert store.claim_run_execution_lease(run.run_id, "abandoned-owner")

    class RecoveryService(SidekickSwarmService):
        def execute_run(self, project_root, run_id, **_callbacks):
            return SwarmEngine(transport).execute_run(run_id, project_root)

    service = RecoveryService()
    recover = _parse(
        ["swarm", "--project", str(project), "--json", "recover", run.run_id]
    )

    assert (
        swarm_command(
            recover,
            service=service,
            actor_factory=lambda: "os:uid:4242",
        )
        == 0
    )
    recovered = ProjectSwarmStore.open_read_only(project).get_run(run.run_id)
    assert recovered is not None
    assert recovered.status == "paused"
    assert transport.requests == []
    assert [
        event.payload
        for event in ProjectSwarmStore.open_read_only(project).list_events(run.run_id)
        if event.event_type == "run.execution_lease_recovered_by_human"
    ] == [{"actor_id": "os:uid:4242"}]
    assert [
        event.payload
        for event in ProjectSwarmStore.open_read_only(project).list_events(run.run_id)
        if event.event_type == "model.attempt_replay_authorized_by_human"
    ] == [
        {
            "actor_id": "os:uid:4242",
            "original_attempt_sequence": abandoned_attempt.sequence,
            "role": "scout",
            "model": "deepseek-v4-flash",
        }
    ]

    resume = _parse(
        ["swarm", "--project", str(project), "--json", "resume", run.run_id]
    )
    assert swarm_command(resume, service=service) == 0
    resumed = ProjectSwarmStore.open_read_only(project).get_run(run.run_id)
    assert resumed is not None
    assert resumed.status == "completed"
    assert [request.role for request in transport.requests].count("scout") == 1
    scout_attempts = [
        event
        for event in ProjectSwarmStore.open_read_only(project).list_events(run.run_id)
        if event.event_type == "model.attempt_started"
        and event.payload == {"role": "scout", "model": "deepseek-v4-flash"}
    ]
    assert len(scout_attempts) == 2
    assert scout_attempts[0].sequence == abandoned_attempt.sequence
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["status"] == "completed"
    assert payload["call_count"] == 9


def test_cli_packs_list_serializes_immutable_role_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches CLI pack listing deep-copying MappingProxyType role metadata."""
    project = tmp_path / "project"
    project.mkdir()

    class FakeService:
        def list_packs(self, project_root):
            assert project_root == project.resolve()
            return [
                PackDefinition(
                    pack_id="coding-team",
                    description="Coding workflow",
                    workflow="scout -> builder",
                    roles={"scout": "discover"},
                )
            ]

    args = _parse(["swarm", "--project", str(project), "--json", "packs", "list"])

    assert swarm_command(args, service=FakeService()) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "pack_id": "coding-team",
            "description": "Coding workflow",
            "workflow": "scout -> builder",
            "roles": {"scout": "discover"},
        }
    ]


def test_cli_shared_flags_work_before_or_after_the_action(tmp_path: Path):
    """Catches conventional `swarm run ... --project` invocations being rejected."""
    project = tmp_path / "project"
    project.mkdir()

    before = _parse(["swarm", "--project", str(project), "--json", "init"])
    after = _parse(["swarm", "init", "--project", str(project), "--json"])

    assert Path(before.project) == project
    assert before.json is True
    assert Path(after.project) == project
    assert after.json is True
