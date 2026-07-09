from __future__ import annotations


def test_cli_goal_resume_preserves_budget_progress_and_resets_parse_failure_counter(monkeypatch):
    from cli.goals import GoalManager, GoalState

    monkeypatch.setattr("cli.goals.save_goal", lambda *args, **kwargs: None)

    mgr = GoalManager("goal-session")
    mgr._state = GoalState(
        goal="Ship it",
        status="paused",
        turns_used=4,
        max_turns=12,
        paused_reason="judge paused",
        consecutive_parse_failures=3,
    )

    resumed = mgr.resume()

    assert resumed is not None
    assert resumed.status == "active"
    assert resumed.turns_used == 4
    assert resumed.consecutive_parse_failures == 0


def test_cli_goal_resume_keeps_paused_when_budget_exhausted(monkeypatch):
    from cli.goals import GoalManager, GoalState

    monkeypatch.setattr("cli.goals.save_goal", lambda *args, **kwargs: None)

    mgr = GoalManager("goal-session")
    mgr._state = GoalState(
        goal="Ship it",
        status="paused",
        turns_used=20,
        max_turns=20,
        paused_reason="turn budget exhausted (20/20)",
        consecutive_parse_failures=2,
    )

    resumed = mgr.resume()

    assert resumed is not None
    assert resumed.status == "paused"
    assert resumed.turns_used == 20
    assert resumed.consecutive_parse_failures == 2


def test_cli_goal_budget_defaults_custom_and_unlimited(monkeypatch):
    from cli.goals import GoalManager

    monkeypatch.setattr("cli.goals.save_goal", lambda *args, **kwargs: None)
    monkeypatch.setattr("cli.goals.judge_goal", lambda *args, **kwargs: ("continue", "still working", False))

    mgr = GoalManager("goal-session")

    default_state = mgr.set("Ship it")
    custom_state = mgr.set("Ship it faster", max_turns=7)
    unlimited_state = mgr.set("Keep going", unlimited=True)

    assert default_state.max_turns == 20
    assert custom_state.max_turns == 7
    assert unlimited_state.max_turns is None

    mgr._state = unlimited_state
    for _ in range(25):
        decision = mgr.evaluate_after_turn("still working")
        assert decision["status"] == "active"
        assert decision["should_continue"] is True


def test_webui_goal_resume_preserves_budget_progress_and_resets_parse_failure_counter(monkeypatch, tmp_path):
    from cli.goals import GoalState
    from web.api.goals import _ProfileGoalManager

    mgr = _ProfileGoalManager("goal-session", profile_home=tmp_path)
    mgr._state = GoalState(
        goal="Ship it",
        status="paused",
        turns_used=4,
        max_turns=12,
        paused_reason="judge paused",
        consecutive_parse_failures=3,
    )

    saved = {}

    monkeypatch.setattr(mgr, "_save", lambda state: saved.setdefault("state", state))

    resumed = mgr.resume()

    assert resumed is not None
    assert resumed.status == "active"
    assert resumed.turns_used == 4
    assert resumed.consecutive_parse_failures == 0
    assert saved["state"].consecutive_parse_failures == 0


def test_webui_goal_resume_keeps_paused_when_budget_exhausted(monkeypatch, tmp_path):
    from cli.goals import GoalState
    from web.api.goals import _ProfileGoalManager

    mgr = _ProfileGoalManager("goal-session", profile_home=tmp_path)
    mgr._state = GoalState(
        goal="Ship it",
        status="paused",
        turns_used=20,
        max_turns=20,
        paused_reason="turn budget exhausted (20/20)",
        consecutive_parse_failures=2,
    )

    saved = {}

    monkeypatch.setattr(mgr, "_save", lambda state: saved.setdefault("state", state))

    resumed = mgr.resume()

    assert resumed is not None
    assert resumed.status == "paused"
    assert resumed.turns_used == 20
    assert resumed.consecutive_parse_failures == 2
    assert saved == {}


def test_webui_goal_command_resume_uses_budget_exhausted_message(monkeypatch):
    from cli.goals import GoalState
    from web.api import goals as goal_api

    class FakeManager:
        def __init__(self):
            self.state = GoalState(
                goal="Ship it",
                status="paused",
                turns_used=20,
                max_turns=20,
                paused_reason="turn budget exhausted (20/20)",
                consecutive_parse_failures=0,
            )

        def resume(self):
            return self.state

    fake_mgr = FakeManager()
    monkeypatch.setattr(goal_api, "_manager", lambda *args, **kwargs: fake_mgr)

    payload = goal_api.goal_command_payload("goal-session", "resume")

    assert payload["goal"]["status"] == "paused"
    assert payload["message_key"] == "goal_paused_budget_exhausted"
    assert payload["message_args"] == [20, "20"]
    assert "turns used" in payload["message"].lower()


def test_webui_goal_command_passes_custom_and_unlimited_budget(monkeypatch):
    from web.api import goals as goal_api
    from web.api import routes

    class Session:
        session_id = "goal-session"
        profile = "default"
        workspace = "workspace"
        workspace_slug = None
        space_slug = None
        space = None
        active_stream_id = None
        model = "model"
        model_provider = "provider"

    captured = {}

    monkeypatch.setattr(routes, "require", lambda body, key: None)
    monkeypatch.setattr(routes, "get_session", lambda session_id: Session())
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: workspace)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda model, provider: (model, provider, model))
    monkeypatch.setattr(goal_api, "goal_state_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(goal_api, "restore_goal_state", lambda *args, **kwargs: None)

    def fake_goal_command_payload(session_id, goal_args, **kwargs):
        captured["session_id"] = session_id
        captured["goal_args"] = goal_args
        captured["kwargs"] = kwargs
        return {"ok": True, "message": "ok", "goal": {"goal": goal_args}, "kickoff_prompt": ""}

    monkeypatch.setattr(goal_api, "goal_command_payload", fake_goal_command_payload)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=None: {"payload": payload, "status": status})
    monkeypatch.setattr(routes, "bad", lambda handler, message, status=400: {"error": message, "status": status})

    result = routes._handle_goal_command(object(), {
        "session_id": "goal-session",
        "args": "Build it",
        "goal_steps": "37",
    })

    assert captured["session_id"] == "goal-session"
    assert captured["goal_args"] == "Build it"
    assert captured["kwargs"]["max_turns"] == 37
    assert captured["kwargs"]["unlimited"] is False
    assert result["status"] is None

    captured.clear()
    result = routes._handle_goal_command(object(), {
        "session_id": "goal-session",
        "args": "Keep going",
        "goal_unlimited": True,
    })

    assert captured["kwargs"]["max_turns"] is None
    assert captured["kwargs"]["unlimited"] is True
    assert result["status"] is None


def test_webui_goal_command_ignores_blank_max_turns_when_goal_steps_present(monkeypatch):
    from web.api import goals as goal_api
    from web.api import routes

    class Session:
        session_id = "goal-session"
        profile = "default"
        workspace = "workspace"
        workspace_slug = None
        space_slug = None
        space = None
        active_stream_id = None
        model = "model"
        model_provider = "provider"

    captured = {}

    monkeypatch.setattr(routes, "require", lambda body, key: None)
    monkeypatch.setattr(routes, "get_session", lambda session_id: Session())
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: workspace)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda model, provider: (model, provider, model))
    monkeypatch.setattr(goal_api, "goal_state_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(goal_api, "restore_goal_state", lambda *args, **kwargs: None)

    def fake_goal_command_payload(session_id, goal_args, **kwargs):
        captured["kwargs"] = kwargs
        return {"ok": True, "message": "ok", "goal": {"goal": goal_args}, "kickoff_prompt": ""}

    monkeypatch.setattr(goal_api, "goal_command_payload", fake_goal_command_payload)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=None: {"payload": payload, "status": status})
    monkeypatch.setattr(routes, "bad", lambda handler, message, status=400: {"error": message, "status": status})

    result = routes._handle_goal_command(object(), {
        "session_id": "goal-session",
        "args": "Build it",
        "goal_steps": "37",
        "max_turns": "",
    })

    assert captured["kwargs"]["max_turns"] == 37
    assert captured["kwargs"]["unlimited"] is False
    assert result["status"] is None


def test_webui_goal_command_treats_unlimited_string_as_unlimited(monkeypatch):
    from web.api import goals as goal_api
    from web.api import routes

    class Session:
        session_id = "goal-session"
        profile = "default"
        workspace = "workspace"
        workspace_slug = None
        space_slug = None
        space = None
        active_stream_id = None
        model = "model"
        model_provider = "provider"

    captured = {}

    monkeypatch.setattr(routes, "require", lambda body, key: None)
    monkeypatch.setattr(routes, "get_session", lambda session_id: Session())
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: workspace)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda model, provider: (model, provider, model))
    monkeypatch.setattr(goal_api, "goal_state_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(goal_api, "restore_goal_state", lambda *args, **kwargs: None)

    def fake_goal_command_payload(session_id, goal_args, **kwargs):
        captured["kwargs"] = kwargs
        return {"ok": True, "message": "ok", "goal": {"goal": goal_args}, "kickoff_prompt": ""}

    monkeypatch.setattr(goal_api, "goal_command_payload", fake_goal_command_payload)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=None: {"payload": payload, "status": status})
    monkeypatch.setattr(routes, "bad", lambda handler, message, status=400: {"error": message, "status": status})

    result = routes._handle_goal_command(object(), {
        "session_id": "goal-session",
        "args": "Build it",
        "goal_steps": "unlimited",
    })

    assert captured["kwargs"]["max_turns"] is None
    assert captured["kwargs"]["unlimited"] is True
    assert result["status"] is None


def test_webui_goal_command_resume_reports_budget_exhausted(monkeypatch):
    from cli.goals import GoalState
    from web.api import goals as goal_api

    class FakeManager:
        def __init__(self):
            self.state = GoalState(
                goal="Ship it",
                status="paused",
                turns_used=20,
                max_turns=20,
                paused_reason="turn budget exhausted (20/20)",
                consecutive_parse_failures=0,
            )

        def resume(self):
            return self.state

    fake_mgr = FakeManager()
    monkeypatch.setattr(goal_api, "_manager", lambda *args, **kwargs: fake_mgr)

    payload = goal_api.goal_command_payload("goal-session", "resume")

    assert payload["goal"]["status"] == "paused"
    assert payload["message_key"] == "goal_paused_budget_exhausted"
    assert payload["message_args"] == [20, "20"]
    assert "turns used" in payload["message"].lower()


def test_cli_goal_command_resume_reports_budget_exhausted(monkeypatch):
    from cli.goals import GoalState
    from cli import cli as cli_module

    class FakeManager:
        def __init__(self):
            self._state = GoalState(
                goal="Ship it",
                status="paused",
                turns_used=20,
                max_turns=20,
                paused_reason="turn budget exhausted (20/20)",
                consecutive_parse_failures=0,
            )

        def resume(self):
            return self._state

        def status_line(self):
            return "  ⏸ Goal (paused, 20/20 turns used, turn budget exhausted (20/20)): Ship it"

    outputs = []
    dummy = type("DummyCLI", (), {"_get_goal_manager": lambda self: FakeManager(), "_pending_input": None})()
    monkeypatch.setattr(cli_module, "_cprint", lambda msg: outputs.append(msg))

    cli_module.SidekickCLI._handle_goal_command(dummy, "/goal resume")

    assert any("paused" in line.lower() for line in outputs)
    assert all("resumed" not in line.lower() for line in outputs)
