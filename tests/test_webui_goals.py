from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace


def _close_db_cache(cache):
    for db in list(cache.values()):
        close = getattr(db, "close", None)
        if callable(close):
            close()
    cache.clear()


class _FakeGoalManager:
    _states = {}

    def __init__(self, session_id: str, default_max_turns: int = 20):
        self.session_id = session_id
        self.default_max_turns = default_max_turns
        self._state = self._states.get(session_id)

    @property
    def state(self):
        return self._state

    def has_goal(self):
        return self._state is not None and self._state.status in {"active", "paused"}

    def set(self, goal: str):
        state = SimpleNamespace(
            goal=goal,
            status="active",
            turns_used=0,
            max_turns=self.default_max_turns,
            last_verdict=None,
            last_reason=None,
            paused_reason=None,
        )
        self._states[self.session_id] = state
        self._state = state
        return state

    def pause(self, reason: str = "user-paused"):
        if self._state is None:
            return None
        self._state.status = "paused"
        self._state.paused_reason = reason
        return self._state

    def resume(self, reset_budget: bool = True):
        if self._state is None:
            return None
        self._state.status = "active"
        self._state.paused_reason = None
        if reset_budget:
            self._state.turns_used = 0
        return self._state

    def clear(self):
        self._states.pop(self.session_id, None)
        self._state = None

    def is_active(self):
        return self._state is not None and self._state.status == "active"

    def evaluate_after_turn(self, last_response: str, *, user_initiated: bool = True):
        from web.api import goals as _goals

        state = self._state
        if state is None or state.status != "active":
            return {
                "status": getattr(state, "status", None) if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        state.turns_used += 1
        state.last_turn_at = 0.0

        if _goals.judge_goal is None:
            verdict, reason = "continue", "goal judge unavailable"
        else:
            verdict, reason, _parse_failed = _goals.judge_goal(state.goal, str(last_response or ""))
        state.last_verdict = verdict
        state.last_reason = reason

        if verdict == "done":
            state.status = "done"
            return {
                "status": "done",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": f"✓ Goal achieved: {reason}",
            }

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — {state.turns_used}/{state.max_turns} turns used. "
                    "Use /goal resume to keep going, or /goal clear to stop."
                ),
            }

        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": f"↻ Continuing toward goal ({state.turns_used}/{state.max_turns}): {reason}",
        }

    def next_continuation_prompt(self):
        from web.api import goals as _goals

        if not self._state or self._state.status != "active":
            return None
        template = getattr(_goals, "CONTINUATION_PROMPT_TEMPLATE", "")
        if not template:
            template = "Continue working toward: {goal}"
        return template.format(goal=self._state.goal)


def test_goal_command_payload_includes_session_id(monkeypatch):
    from web.api import goals

    _FakeGoalManager._states.clear()
    monkeypatch.setattr(goals, "GoalManager", _FakeGoalManager)

    set_payload = goals.goal_command_payload("goal-session-1", "ship the browser polish")
    assert set_payload["ok"] is True
    assert set_payload["action"] == "set"
    assert set_payload["goal"]["goal"] == "ship the browser polish"
    assert set_payload["goal"]["session_id"] == "goal-session-1"

    status_payload = goals.goal_command_payload("goal-session-1", "status")
    assert status_payload["ok"] is True
    assert status_payload["action"] == "status"
    assert status_payload["goal"]["session_id"] == "goal-session-1"


def test_goal_clear_returns_null_goal(monkeypatch):
    from web.api import goals

    _FakeGoalManager._states.clear()
    monkeypatch.setattr(goals, "GoalManager", _FakeGoalManager)

    goals.goal_command_payload("goal-session-2", "stale banner must disappear")
    clear_payload = goals.goal_command_payload("goal-session-2", "clear")
    assert clear_payload["ok"] is True
    assert clear_payload["action"] == "clear"
    assert clear_payload["goal"] is None


def test_goal_resume_returns_kickoff_prompt(monkeypatch):
    from web.api import goals

    _FakeGoalManager._states.clear()
    monkeypatch.setattr(goals, "GoalManager", _FakeGoalManager)
    monkeypatch.setattr(goals, "CONTINUATION_PROMPT_TEMPLATE", "Continue: {goal}")

    goals.goal_command_payload("goal-session-resume", "ship the browser polish")
    resume_payload = goals.goal_command_payload("goal-session-resume", "resume")

    assert resume_payload["ok"] is True
    assert resume_payload["action"] == "resume"
    assert resume_payload["goal"]["goal"] == "ship the browser polish"
    assert resume_payload["goal"]["status"] == "active"
    assert resume_payload["kickoff_prompt"] == "Continue: ship the browser polish"
    assert "Continuing now." in resume_payload["message"]


def test_goal_frontend_reconciles_stale_local_state_contract():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")

    assert "async function _syncGoalStateFromServer()" in messages_js
    assert "args:'status'" in messages_js
    assert "r=await api('/api/goal',{method:'POST',body:JSON.stringify(body)})" in messages_js
    assert "_clearGoalState();" in messages_js
    assert "window._syncGoalStateFromServer=_syncGoalStateFromServer;" in messages_js
    assert "_scheduleGoalStateSync(60);" in messages_js
    assert "function _goalActiveSession()" in messages_js
    assert "typeof S!=='undefined'&&S&&S.session" in messages_js


def test_goal_frontend_ignores_stale_status_responses_after_session_switch():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")

    assert "const sid=session&&session.session_id;" in messages_js
    assert "const currentSession=_goalActiveSession();" in messages_js
    assert "const currentSid=currentSession&&currentSession.session_id;" in messages_js
    assert "if(currentSid!==sid)return;" in messages_js
    assert "if(r&&r.goal&&r.goal.session_id&&r.goal.session_id!==sid)return;" in messages_js


def test_goal_continuation_reload_persistence_contract():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "sidekick-webui-goal-continuation-" in messages_js
    assert "_storePendingGoalContinuation(" in messages_js
    assert "_restorePendingGoalContinuationForSession(" in messages_js
    assert "_clearPendingGoalContinuation(" in messages_js
    assert "_maybeStartPendingGoalContinuation()" in messages_js
    assert "function _launchGoalContinuation(" in messages_js
    assert "_launchGoalContinuation(_pendingGoalContinuation);" in messages_js
    assert "window._launchGoalContinuation=_launchGoalContinuation;" in messages_js
    assert "_clearPendingGoalContinuationLaunchState();" in messages_js
    assert "_restorePendingGoalContinuationForSession(sid)" in sessions_js
    assert "restoredGoalContinuation" in sessions_js


def test_goal_evaluation_requests_continuation(monkeypatch):
    from web.api import goals

    _FakeGoalManager._states.clear()
    monkeypatch.setattr(goals, "GoalManager", _FakeGoalManager)
    monkeypatch.setattr(goals, "judge_goal", lambda goal, response: ("continue", "needs another pass", False))
    monkeypatch.setattr(goals, "CONTINUATION_PROMPT_TEMPLATE", "Continue: {goal}")

    goals.goal_command_payload("goal-session-3", "ship the browser polish")
    decision = goals.evaluate_goal_after_turn("goal-session-3", "assistant reply")

    assert decision["should_continue"] is True
    assert decision["status"] == "active"
    assert decision["continuation_prompt"] == "Continue: ship the browser polish"
    assert decision["message_key"] == "goal_continuing"
    assert decision["turns_used"] == 1
    assert decision["max_turns"] == 20
    state = _FakeGoalManager._states["goal-session-3"]
    assert state.status == "active"
    assert state.last_verdict == "continue"
    assert state.last_reason == "needs another pass"


def test_goal_evaluation_marks_done(monkeypatch):
    from web.api import goals

    _FakeGoalManager._states.clear()
    monkeypatch.setattr(goals, "GoalManager", _FakeGoalManager)
    monkeypatch.setattr(goals, "judge_goal", lambda goal, response: ("done", "objective complete", False))

    goals.goal_command_payload("goal-session-4", "finish the task")
    decision = goals.evaluate_goal_after_turn("goal-session-4", "assistant reply")

    assert decision["should_continue"] is False
    assert decision["status"] == "done"
    assert decision["message_key"] == "goal_achieved"
    assert "objective complete" in decision["message"]
    state = _FakeGoalManager._states["goal-session-4"]
    assert state.status == "done"
    assert state.last_verdict == "done"
    assert state.last_reason == "objective complete"


def test_goal_command_payload_preserves_active_goal_when_agent_running(monkeypatch):
    from web.api import goals

    _FakeGoalManager._states.clear()
    monkeypatch.setattr(goals, "GoalManager", _FakeGoalManager)

    set_payload = goals.goal_command_payload("goal-session-5", "keep the goal visible")
    assert set_payload["ok"] is True
    assert set_payload["goal"]["goal"] == "keep the goal visible"
    assert set_payload["goal"]["status"] == "active"

    error_payload = goals.goal_command_payload(
        "goal-session-5",
        "should not replace the active goal while busy",
        stream_running=True,
    )

    assert error_payload["ok"] is False
    assert error_payload["error"] == "agent_running"
    assert error_payload["goal"]["goal"] == "keep the goal visible"
    assert error_payload["goal"]["status"] == "active"
    assert error_payload["goal"]["session_id"] == "goal-session-5"


def test_cli_goal_state_persists_across_manager_reopen(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from cli import goals

    _close_db_cache(goals._DB_CACHE)

    sid = "goal-persist-cli"
    state = goals.GoalState(
        goal="persist the goal",
        status="active",
        turns_used=2,
        max_turns=7,
        created_at=1.0,
        last_turn_at=2.0,
        last_verdict="continue",
        last_reason="keep going",
        paused_reason=None,
        consecutive_parse_failures=0,
    )
    goals.save_goal(sid, state)

    _close_db_cache(goals._DB_CACHE)
    loaded = goals.load_goal(sid)

    assert loaded is not None
    assert loaded.goal == "persist the goal"
    assert loaded.status == "active"
    assert loaded.turns_used == 2
    assert loaded.max_turns == 7
    assert loaded.last_verdict == "continue"
    assert loaded.last_reason == "keep going"


def test_webui_goal_payload_persists_across_profile_manager_reopen(monkeypatch, tmp_path):
    from web.api import goals

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(goals, "_space_goals_path", lambda: None)
    _close_db_cache(goals._DB_CACHE)

    sid = "goal-persist-webui"
    set_payload = goals.goal_command_payload(sid, "persist the webui goal", profile_home=tmp_path)
    assert set_payload["ok"] is True
    assert set_payload["goal"]["status"] == "active"
    assert set_payload["goal"]["goal"] == "persist the webui goal"

    _close_db_cache(goals._DB_CACHE)
    snapshot = goals.goal_state_snapshot(sid, profile_home=tmp_path)
    assert snapshot is not None
    assert snapshot.goal == "persist the webui goal"
    assert snapshot.status == "active"

    status_payload = goals.goal_command_payload(sid, "status", profile_home=tmp_path)
    assert status_payload["ok"] is True
    assert status_payload["goal"]["goal"] == "persist the webui goal"
    assert status_payload["goal"]["status"] == "active"


def test_goal_command_kickoff_forwards_goal_related_stream(monkeypatch, tmp_path):
    from web.api import goals as goals_mod
    from web.api import profiles as profiles_mod
    from web.api import routes

    sid = "goal-kickoff-route"
    fake_session = SimpleNamespace(
        session_id=sid,
        profile="default",
        workspace="nova",
        model="model-x",
        model_provider="provider-y",
        pending_started_at=None,
        save=lambda: None,
    )
    captured = {}

    monkeypatch.setattr(routes, "get_session", lambda session_id: fake_session)
    monkeypatch.setattr(profiles_mod, "get_hermes_home_for_profile", lambda profile: tmp_path)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: "nova")
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("model-x", "provider-y", True),
    )
    monkeypatch.setattr(
        goals_mod,
        "goal_command_payload",
        lambda *args, **kwargs: {
            "ok": True,
            "action": "set",
            "message": "goal set",
            "goal": {"goal": "continue coding", "status": "active"},
            "kickoff_prompt": "continue coding",
        },
    )
    monkeypatch.setattr(goals_mod, "goal_state_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(goals_mod, "restore_goal_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "set_last_workspace", lambda workspace: None)
    monkeypatch.setattr(routes, "create_stream_channel", lambda: object())
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda session_id: nullcontext())
    monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", lambda s, **kwargs: setattr(s, "pending_started_at", 123.0))
    monkeypatch.setattr(
        routes,
        "j",
        lambda handler, payload, status=200: {"status": status, "payload": payload},
    )

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            captured["thread_target"] = target
            captured["thread_args"] = args
            captured["thread_kwargs"] = dict(kwargs or {})
            captured["thread_daemon"] = daemon

        def start(self):
            captured["thread_started"] = True

    monkeypatch.setattr(routes.threading, "Thread", _FakeThread)
    monkeypatch.setattr(routes, "_run_agent_streaming", lambda *args, **kwargs: captured.update({"run_kwargs": dict(kwargs)}))

    try:
        result = routes._handle_goal_command(object(), {"session_id": sid, "args": "continue coding"})

        assert result["status"] == 200
        assert result["payload"]["ok"] is True
        assert captured["thread_started"] is True
        assert captured["thread_kwargs"]["goal_related"] is True
        assert captured["thread_kwargs"]["mode"] == ""
        assert captured["thread_kwargs"]["sandbox_disabled"] is False
    finally:
        routes.STREAMS.clear()
        routes.STREAM_GOAL_RELATED.clear()


def test_goal_command_resume_forwards_goal_related_stream(monkeypatch, tmp_path):
    from web.api import goals as goals_mod
    from web.api import profiles as profiles_mod
    from web.api import routes

    sid = "goal-resume-route"
    fake_session = SimpleNamespace(
        session_id=sid,
        profile="default",
        workspace="nova",
        model="model-x",
        model_provider="provider-y",
        pending_started_at=None,
        save=lambda: None,
    )
    captured = {}

    monkeypatch.setattr(routes, "get_session", lambda session_id: fake_session)
    monkeypatch.setattr(profiles_mod, "get_hermes_home_for_profile", lambda profile: tmp_path)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: "nova")
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("model-x", "provider-y", True),
    )
    monkeypatch.setattr(
        goals_mod,
        "goal_command_payload",
        lambda *args, **kwargs: {
            "ok": True,
            "action": "resume",
            "message": "goal resumed",
            "goal": {"goal": "continue coding", "status": "active"},
            "kickoff_prompt": "Continue: continue coding",
        },
    )
    monkeypatch.setattr(routes, "set_last_workspace", lambda workspace: None)
    monkeypatch.setattr(routes, "create_stream_channel", lambda: object())
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda session_id: nullcontext())
    monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", lambda s, **kwargs: setattr(s, "pending_started_at", 123.0))
    monkeypatch.setattr(
        routes,
        "j",
        lambda handler, payload, status=200: {"status": status, "payload": payload},
    )

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            captured["thread_target"] = target
            captured["thread_args"] = args
            captured["thread_kwargs"] = dict(kwargs or {})
            captured["thread_daemon"] = daemon

        def start(self):
            captured["thread_started"] = True

    monkeypatch.setattr(routes.threading, "Thread", _FakeThread)
    monkeypatch.setattr(routes, "_run_agent_streaming", lambda *args, **kwargs: captured.update({"run_kwargs": dict(kwargs)}))

    try:
        result = routes._handle_goal_command(object(), {"session_id": sid, "args": "resume"})

        assert result["status"] == 200
        assert result["payload"]["ok"] is True
        assert captured["thread_started"] is True
        assert captured["thread_kwargs"]["goal_related"] is True
        assert captured["thread_kwargs"]["mode"] == ""
        assert captured["thread_kwargs"]["sandbox_disabled"] is False
    finally:
        routes.STREAMS.clear()
        routes.STREAM_GOAL_RELATED.clear()


def test_pending_goal_continuation_promotes_next_stream(monkeypatch, tmp_path):
    from web.api import routes
    from web.api import turn_journal as turn_journal_mod

    sid = "goal-continuation-route"
    fake_session = SimpleNamespace(
        session_id=sid,
        profile="default",
        workspace="nova",
        model="model-x",
        model_provider="provider-y",
        pending_started_at=123.0,
        save=lambda: None,
        compact=lambda: {"session_id": sid},
    )
    captured = {}
    routes.PENDING_GOAL_CONTINUATION.add(sid)

    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda session_id: nullcontext())
    monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", lambda s, **kwargs: setattr(s, "pending_started_at", 123.0))
    monkeypatch.setattr(routes, "set_last_workspace", lambda workspace: None)
    monkeypatch.setattr(routes, "create_stream_channel", lambda: object())
    monkeypatch.setattr(turn_journal_mod, "append_turn_journal_event", lambda *args, **kwargs: {"turn_id": "turn-1"})
    monkeypatch.setattr(
        routes,
        "_run_agent_streaming",
        lambda *args, **kwargs: captured.update({"run_kwargs": dict(kwargs)}),
    )

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            captured["thread_kwargs"] = dict(kwargs or {})
            captured["thread_started"] = False

        def start(self):
            captured["thread_started"] = True

    monkeypatch.setattr(routes.threading, "Thread", _FakeThread)

    try:
        response = routes._start_chat_stream_for_session(
            fake_session,
            msg="continue the goal",
            attachments=[],
            workspace="nova",
            model="model-x",
            model_provider="provider-y",
        )

        assert response["stream_id"]
        assert sid not in routes.PENDING_GOAL_CONTINUATION
        assert routes.STREAM_GOAL_RELATED[response["stream_id"]] is True
        assert captured["thread_started"] is True
        assert captured["thread_kwargs"]["goal_related"] is True
    finally:
        for key in list(routes.STREAMS.keys()):
            routes.STREAMS.pop(key, None)
        routes.STREAM_GOAL_RELATED.clear()


def test_cli_goal_resume_queues_continuation_prompt(monkeypatch):
    from queue import Queue

    from cli import cli as cli_mod

    class _ResumeGoalManager:
        def __init__(self):
            self.session_id = "goal-session-cli-resume"
            self._state = SimpleNamespace(goal="resume the goal", status="paused", turns_used=2, max_turns=5)

        def resume(self, reset_budget: bool = True):
            self._state.status = "active"
            if reset_budget:
                self._state.turns_used = 0
            return self._state

        def next_continuation_prompt(self):
            return "Continue working toward: resume the goal"

    fake_cli = object.__new__(cli_mod.SidekickCLI)
    fake_cli._pending_input = Queue()
    fake_cli._goal_manager = _ResumeGoalManager()

    printed = []
    monkeypatch.setattr(cli_mod, "_cprint", lambda *args, **kwargs: printed.append(" ".join(str(arg) for arg in args)))
    monkeypatch.setattr(cli_mod.SidekickCLI, "_get_goal_manager", lambda self: self._goal_manager)

    cli_mod.SidekickCLI._handle_goal_command(fake_cli, "/goal resume")

    assert fake_cli._pending_input.get_nowait() == "Continue working toward: resume the goal"
    assert any("Continuing now." in line for line in printed)


def test_gateway_goal_resume_enqueues_continuation_prompt(monkeypatch):
    import asyncio

    from runtime.gateway import run as gateway_mod

    class _ResumeGoalManager:
        def __init__(self):
            self.session_id = "goal-session-gateway-resume"
            self._state = SimpleNamespace(goal="resume the goal", status="paused", turns_used=2, max_turns=5)

        def resume(self, reset_budget: bool = True):
            self._state.status = "active"
            if reset_budget:
                self._state.turns_used = 0
            return self._state

        def next_continuation_prompt(self):
            return "Continue working toward: resume the goal"

    runner = object.__new__(gateway_mod.GatewayRunner)
    runner.adapters = {"discord": SimpleNamespace()}
    captured = {}

    monkeypatch.setattr(gateway_mod.GatewayRunner, "_get_goal_manager_for_event", lambda self, event: (_ResumeGoalManager(), SimpleNamespace(session_id="goal-session-gateway-resume")))
    monkeypatch.setattr(gateway_mod.GatewayRunner, "_session_key_for_source", lambda self, source: "goal-session-gateway-resume")
    monkeypatch.setattr(gateway_mod.GatewayRunner, "_clear_goal_pending_continuations", lambda self, session_key, adapter: 0)
    monkeypatch.setattr(
        gateway_mod.GatewayRunner,
        "_enqueue_fifo",
        lambda self, session_key, event, adapter: captured.update({
            "session_key": session_key,
            "event": event,
            "adapter": adapter,
        }),
    )
    monkeypatch.setattr(gateway_mod, "MessageEvent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(gateway_mod, "t", lambda key, **kwargs: f"resumed:{kwargs.get('goal', '')}")

    event = SimpleNamespace(
        get_command_args=lambda: "resume",
        source=SimpleNamespace(platform="discord", chat_id="chat-1"),
        channel_prompt=">",
        message_id="msg-1",
    )

    result = asyncio.run(runner._handle_goal_command(event))

    assert result == "resumed:resume the goal"
    assert captured["session_key"] == "goal-session-gateway-resume"
    assert captured["event"].text == "Continue working toward: resume the goal"
    assert captured["event"].message_type == gateway_mod.MessageType.TEXT
