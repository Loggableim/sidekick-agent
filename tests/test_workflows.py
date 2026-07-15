from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def test_workflow_store_persists_versioned_approval_and_execution(tmp_path):
    from runtime.workflows import WorkflowConflictError, WorkflowStore

    store = WorkflowStore(profile_home=tmp_path)
    draft = store.create_plan("session-alpha", "Add a safe workflow")

    assert draft["mode"] == "plan"
    assert draft["phase"] == "drafting"
    assert draft["version"] == 1
    assert draft["plan_id"]

    awaiting = store.record_plan(
        "session-alpha",
        draft["plan_id"],
        version=1,
        plan_markdown="# Plan\n\n1. Test it.",
    )
    assert awaiting["phase"] == "awaiting_approval"
    assert awaiting["plan_markdown"].startswith("# Plan")

    approved = store.approve(
        "session-alpha", awaiting["plan_id"], version=awaiting["version"], approver="webui"
    )
    assert approved["approval_version"] == 1
    assert approved["approved_at"]
    assert approved["approver"] == "webui"

    executing = store.begin_execution(
        "session-alpha", approved["plan_id"], version=approved["version"], stream_id="stream-1"
    )
    assert executing["mode"] == "execute"
    assert executing["phase"] == "executing"
    assert executing["execution_stream_id"] == "stream-1"

    reloaded = WorkflowStore(profile_home=tmp_path).get_session("session-alpha")
    assert reloaded == executing

    with pytest.raises(WorkflowConflictError):
        store.begin_execution("session-alpha", approved["plan_id"], version=0, stream_id="stream-2")

    persisted = next((tmp_path / "workflows").glob("*.json"))
    assert json.loads(persisted.read_text(encoding="utf-8"))["plan_id"] == draft["plan_id"]


def test_revision_invalidates_approval_and_requires_current_version(tmp_path):
    from runtime.workflows import WorkflowConflictError, WorkflowStore

    store = WorkflowStore(profile_home=tmp_path)
    draft = store.create_plan("session-beta", "Plan the change")
    awaiting = store.record_plan("session-beta", draft["plan_id"], 1, "Initial plan")
    store.approve("session-beta", awaiting["plan_id"], 1, approver="cli")

    revised = store.revise("session-beta", awaiting["plan_id"], 1, "Include migrations")
    assert revised["phase"] == "drafting"
    assert revised["version"] == 2
    assert revised["approval_version"] is None
    assert "Include migrations" in revised["request"]

    with pytest.raises(WorkflowConflictError):
        store.record_plan("session-beta", draft["plan_id"], 1, "stale")


def test_workflow_failure_is_persisted_and_cannot_be_executed(tmp_path):
    from runtime.workflows import WorkflowConflictError, WorkflowStore

    store = WorkflowStore(profile_home=tmp_path)
    draft = store.create_plan("session-failed", "Create a plan")
    failed = store.fail("session-failed", draft["plan_id"], reason="planner stream failed")

    assert failed["phase"] == "failed"
    assert failed["failure_reason"] == "planner stream failed"
    with pytest.raises(WorkflowConflictError):
        store.begin_execution("session-failed", draft["plan_id"], 1, stream_id="stream-failed")

    persisted_plan = store.create_plan("session-plan-persisted", "Create another plan")
    awaiting = store.record_plan(
        "session-plan-persisted", persisted_plan["plan_id"], 1, "A plan that cannot be delivered"
    )
    failed_after_delivery = store.fail(
        "session-plan-persisted", awaiting["plan_id"], reason="stream finalization failed"
    )
    assert failed_after_delivery["phase"] == "failed"


def test_plan_mode_allows_inspection_and_blocks_mutating_tools():
    from runtime.workflows import plan_tool_block_reason, workflow_context

    with workflow_context(mode="plan", session_id="session-guard"):
        assert plan_tool_block_reason("read_file") is None
        assert plan_tool_block_reason("search_files") is None
        assert "Plan mode" in plan_tool_block_reason("write_file")
        assert "Plan mode" in plan_tool_block_reason("terminal")
        assert "Plan mode" in plan_tool_block_reason("execute_code")
        assert "Plan mode" in plan_tool_block_reason("delegate_task")

    assert plan_tool_block_reason("write_file") is None


def test_dispatcher_refuses_mutating_plan_mode_tool_before_registry(monkeypatch):
    import model_tools
    from runtime.workflows import workflow_context

    def should_not_run(*args, **kwargs):
        raise AssertionError("registry dispatch must not run in Plan Mode")

    monkeypatch.setattr(model_tools.registry, "dispatch", should_not_run)

    with workflow_context(mode="plan", session_id="session-dispatch"):
        result = json.loads(model_tools.handle_function_call("write_file", {"path": "x", "content": "x"}))

    assert "Plan mode blocks" in result["error"]


def test_agent_direct_tool_path_refuses_plan_mode_delegation():
    from run_agent import AIAgent
    from runtime.workflows import workflow_context

    agent = object.__new__(AIAgent)
    agent._memory_manager = None
    agent.session_id = "session-agent"
    agent._dispatch_delegate_task = lambda args: (_ for _ in ()).throw(
        AssertionError("delegate_task must not run in Plan Mode")
    )

    with workflow_context(mode="plan", session_id="session-agent"):
        result = agent._invoke_tool("delegate_task", {"goal": "change files"}, "session-agent")

    assert "Plan mode blocks" in result


def test_workflow_plan_api_creates_server_plan_and_starts_plan_stream(monkeypatch, tmp_path):
    from web.api import routes
    from runtime.workflows import WorkflowStore

    session = SimpleNamespace(
        session_id="session-api",
        profile="default",
        workspace=str(tmp_path),
        model="test-model",
        model_provider="test-provider",
    )
    store = WorkflowStore(profile_home=tmp_path)
    captured = {}

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "_workflow_store_for_session", lambda s: store)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda path: path)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda *args: ("test-model", "test-provider", False))
    monkeypatch.setattr(routes, "_game_mode_nova_remote_model_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "_emit_workflow_stream_event", lambda *args: None)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200: {"status": status, "payload": payload})

    def fake_start(s, **kwargs):
        captured.update(kwargs)
        return {"stream_id": "stream-plan"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start)

    response = routes._handle_workflow_plan(
        None,
        {"session_id": "session-api", "request": "Create a migration plan", "workspace": str(tmp_path)},
    )

    assert response["status"] == 200
    assert response["payload"]["workflow"]["phase"] == "drafting"
    assert captured["mode"] == "plan"
    assert captured["workflow_plan_id"] == response["payload"]["workflow"]["plan_id"]
    assert captured["workflow_version"] == 1


def test_workflow_approval_api_requires_current_plan_and_starts_execute_stream(monkeypatch, tmp_path):
    from runtime.workflows import WorkflowStore
    from web.api import routes

    session = SimpleNamespace(
        session_id="session-approve-api",
        profile="default",
        workspace=str(tmp_path),
        model="test-model",
        model_provider="test-provider",
    )
    store = WorkflowStore(profile_home=tmp_path)
    draft = store.create_plan(session.session_id, "Implement the reviewed change")
    awaiting = store.record_plan(session.session_id, draft["plan_id"], 1, "# Approved plan\n\n1. Implement safely.")
    captured = {}

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "_workflow_store_for_session", lambda s: store)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda path: path)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda *args: ("test-model", "test-provider", False))
    monkeypatch.setattr(routes, "_game_mode_nova_remote_model_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "_emit_workflow_stream_event", lambda *args: None)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200: {"status": status, "payload": payload})

    def fake_start(s, **kwargs):
        persisted = store.get_session(session.session_id)
        assert persisted["phase"] == "executing"
        assert persisted["execution_stream_id"] == kwargs["stream_id"]
        captured.update(kwargs)
        return {"stream_id": kwargs["stream_id"]}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start)

    response = routes._handle_workflow_approve(
        None,
        {
            "session_id": session.session_id,
            "plan_id": awaiting["plan_id"],
            "version": awaiting["version"],
            "workspace": str(tmp_path),
        },
    )

    assert response["status"] == 200
    assert response["payload"]["workflow"]["phase"] == "executing"
    assert captured["mode"] == "execute"
    assert "# Approved plan" in captured["msg"]


def test_execution_runtime_authorization_requires_current_executing_plan(tmp_path):
    from runtime.workflows import WorkflowApprovalError, WorkflowStore

    store = WorkflowStore(profile_home=tmp_path)
    draft = store.create_plan("session-execution-guard", "Implement only after approval")
    awaiting = store.record_plan("session-execution-guard", draft["plan_id"], 1, "# Plan")
    store.approve("session-execution-guard", awaiting["plan_id"], awaiting["version"], approver="webui")

    with pytest.raises(WorkflowApprovalError):
        store.assert_execution_authorized(
            "session-execution-guard", awaiting["plan_id"], awaiting["version"], stream_id="stream-guard"
        )

    executing = store.begin_execution(
        "session-execution-guard", awaiting["plan_id"], awaiting["version"], stream_id="stream-guard"
    )
    assert store.assert_execution_authorized(
        "session-execution-guard", executing["plan_id"], executing["version"], stream_id="stream-guard"
    ) == executing

    with pytest.raises(WorkflowApprovalError):
        store.assert_execution_authorized(
            "session-execution-guard", executing["plan_id"], executing["version"], stream_id="forged-stream"
        )


def test_generic_chat_execute_cannot_bypass_server_issued_workflow_stream(monkeypatch, tmp_path):
    from runtime.workflows import WorkflowStore
    from web.api import routes

    session = SimpleNamespace(session_id="session-generic-execute", profile="default", active_stream_id=None)
    store = WorkflowStore(profile_home=tmp_path)
    draft = store.create_plan(session.session_id, "Implement only after review")
    awaiting = store.record_plan(session.session_id, draft["plan_id"], 1, "# Plan")
    store.approve(session.session_id, awaiting["plan_id"], awaiting["version"], approver="webui")
    monkeypatch.setattr(routes, "_workflow_store_for_session", lambda s: store)

    response = routes._start_chat_stream_for_session(
        session,
        msg="Bypass execute",
        workspace=str(tmp_path),
        model="test-model",
        mode="execute",
        workflow_plan_id=awaiting["plan_id"],
        workflow_version=awaiting["version"],
    )

    assert response["_status"] == 409
    assert "server-issued workflow stream" in response["error"]


def test_cli_execution_failure_marks_shared_workflow_failed(monkeypatch, tmp_path):
    from cli.cli import SidekickCLI
    import runtime.workflows as workflows
    from runtime.workflows import WorkflowStore

    monkeypatch.setattr(workflows, "_default_profile_home", lambda: tmp_path)
    store = WorkflowStore()
    draft = store.create_plan("session-cli-failure", "Implement safely")
    awaiting = store.record_plan("session-cli-failure", draft["plan_id"], 1, "# Plan")
    approved = store.approve("session-cli-failure", awaiting["plan_id"], awaiting["version"], approver="cli")
    executing = store.begin_execution(
        "session-cli-failure", approved["plan_id"], approved["version"], stream_id="cli-stream"
    )
    cli = object.__new__(SidekickCLI)
    cli.session_id = "session-cli-failure"

    cli._finalize_workflow_turn(
        {"mode": "execute", "plan_id": executing["plan_id"], "version": executing["version"]},
        result=None,
        error=RuntimeError("agent crashed"),
    )

    failed = store.get_session("session-cli-failure")
    assert failed["phase"] == "failed"
    assert "agent crashed" in failed["failure_reason"]


def test_workflow_configuration_defaults_to_enabled_and_requires_approval():
    from cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["workflows"] == {
        "enabled": True,
        "require_explicit_approval": True,
    }


def test_cli_registers_plan_and_execute_commands():
    from cli.commands import resolve_command

    plan = resolve_command("plan")
    execute = resolve_command("execute")

    assert plan is not None and plan.subcommands == ("revise", "reject")
    assert execute is not None and execute.args_hint == "[plan-id]"


def test_webui_uses_versioned_workflow_endpoints_and_persisted_plan_fields():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    messages = (root / "web" / "static" / "messages.js").read_text(encoding="utf-8")
    ui = (root / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "'/api/workflows/plan'" in messages
    assert "plan_markdown" in messages
    assert "workflow_version" not in messages  # API field is named version
    assert "'/api/workflows/'+encodeURIComponent(planId)+'/approve'" in ui
    assert "'/api/workflows/'+encodeURIComponent(plan.id)+'/revise'" in ui
    assert "'/api/workflows/'+encodeURIComponent(planId)+'/reject'" in ui


def test_webui_plan_card_declares_its_collapse_state():
    from pathlib import Path

    ui = (Path(__file__).resolve().parents[1] / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "const _PLAN_COLLAPSED = new Set();" in ui


def test_webui_workflow_card_and_status_strip_render_execution_phase():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web" / "static"
    messages = (root / "messages.js").read_text(encoding="utf-8")
    ui = (root / "ui.js").read_text(encoding="utf-8")

    assert "function _workflowStatusText(phase)" in messages
    assert "Execution completed" in messages
    assert "window._workflowStatusSticky" in messages
    assert "planPhase==='executing'" in ui
    assert "planPhase==='completed'" in ui
    assert "_workflowStatusSticky" in ui


def test_all_sidekick_superpowers_skills_are_bundled_and_self_contained():
    from pathlib import Path

    names = {
        "brainstorming",
        "dispatching-parallel-agents",
        "executing-plans",
        "finishing-a-development-branch",
        "receiving-code-review",
        "requesting-code-review",
        "subagent-driven-development",
        "systematic-debugging",
        "test-driven-development",
        "using-git-worktrees",
        "using-superpowers",
        "verification-before-completion",
        "writing-plans",
        "writing-skills",
    }
    root = Path(__file__).resolve().parents[1] / "skills" / "superpowers"

    assert {path.parent.name for path in root.glob("*/SKILL.md")} == names
    for name in names:
        content = (root / name / "SKILL.md").read_text(encoding="utf-8")
        assert content.startswith("---\nname:")
        assert "description: Use when" in content
        assert "C:\\Users\\" not in content
        assert "Codex-only" not in content


def test_superpowers_bundle_syncs_into_profile_and_is_discoverable(monkeypatch, tmp_path):
    import tools.skills_sync as skills_sync
    import tools.skills_tool as skills_tool

    profile_skills = tmp_path / "skills"
    monkeypatch.setattr(skills_sync, "SKILLS_DIR", profile_skills)
    monkeypatch.setattr(skills_sync, "MANIFEST_FILE", profile_skills / ".bundled_manifest")

    result = skills_sync.sync_skills(quiet=True)
    assert set(result["copied"]) >= {"brainstorming", "using-superpowers", "writing-skills"}

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", profile_skills)
    index = json.loads(skills_tool.skills_list(category="superpowers"))
    assert len(index["skills"]) == 14
    loaded = json.loads(skills_tool.skill_view("superpowers/using-superpowers", preprocess=False))
    assert loaded["success"] is True
    assert "Sidekick" in loaded["content"]
