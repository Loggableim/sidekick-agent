from pathlib import Path


def test_browser_permission_required_payload_reports_exact_steps():
    from web.api import browser_runtime

    sid = "permission-contract-session"
    browser_runtime.browser_permission_revoke(sid)

    read_payload = browser_runtime._permission_required_payload(sid, "read")
    assert read_payload["ok"] is False
    assert read_payload["code"] == "browser_permission_required"
    assert read_payload["required_mode"] == "read"
    assert read_payload["suggested_action"] == "request_read_permission"
    assert read_payload["permission_steps"] == ["enable_browser_watch"]
    assert read_payload["permission_step_labels"] == ["Enable browser watch"]
    assert read_payload["permission"]["needs_user_approval"] is True
    assert read_payload["approval_mode"] in {"manual", "smart", "off"}
    assert read_payload["approval_modes"] == ["manual", "smart", "off"]
    assert read_payload["active_goal"]["available"] is True
    assert read_payload["active_goal"]["session_id"] == sid

    control_payload = browser_runtime._permission_required_payload(sid, "control")
    assert control_payload["required_mode"] == "control"
    assert control_payload["suggested_action"] == "request_control_permission"
    assert control_payload["permission_steps"] == ["enable_browser_watch", "enable_browser_control"]
    assert control_payload["permission_step_labels"] == ["Enable browser watch", "Enable browser control"]
    assert control_payload["active_goal"]["session_id"] == sid


def test_browser_permission_required_control_from_read_only_reports_control_step():
    from web.api import browser_runtime

    sid = "permission-contract-read-session"
    browser_runtime.browser_permission_grant(sid, "read")

    control_payload = browser_runtime._permission_required_payload(sid, "control")
    assert control_payload["required_mode"] == "control"
    assert control_payload["permission"]["can_watch"] is True
    assert control_payload["permission"]["can_control"] is False
    assert control_payload["permission_steps"] == ["enable_browser_control"]
    assert control_payload["permission_step_labels"] == ["Enable browser control"]

    browser_runtime.browser_permission_revoke(sid)


def test_browser_permission_grant_persists_across_sessions_until_explicit_revoke():
    from web.api import browser_runtime

    sid_a = "permission-contract-global-session-a"
    sid_b = "permission-contract-global-session-b"
    browser_runtime.browser_permission_revoke(sid_a)
    browser_runtime.browser_permission_revoke(sid_b)

    try:
        granted = browser_runtime.browser_permission_grant(sid_a, "read")
        token = browser_runtime.browser_permission_token(sid_a)

        assert granted["mode"] == "read"
        assert granted["source_session_id"] == sid_a
        assert token

        mirrored = browser_runtime.browser_permission_status(sid_b)
        assert mirrored["mode"] == "read"
        assert mirrored["granted"] is True
        assert mirrored["source_session_id"] == sid_a
        assert browser_runtime.browser_permission_token(sid_b) == token
        assert browser_runtime.browser_permission_token_valid(sid_b, token, "read") is True

        updated = browser_runtime.browser_permission_grant(sid_b, "control")
        assert updated["mode"] == "control"
        assert updated["source_session_id"] == sid_b

        after_update_a = browser_runtime.browser_permission_status(sid_a)
        after_update_b = browser_runtime.browser_permission_status(sid_b)
        assert after_update_a["mode"] == "control"
        assert after_update_a["source_session_id"] == sid_b
        assert after_update_b["mode"] == "control"
        assert after_update_b["source_session_id"] == sid_b

        browser_runtime.browser_permission_revoke(sid_b)
        assert browser_runtime.browser_permission_status(sid_a)["mode"] == "none"
        assert browser_runtime.browser_permission_status(sid_b)["mode"] == "none"
    finally:
        browser_runtime.browser_permission_revoke(sid_a)
        browser_runtime.browser_permission_revoke(sid_b)


def test_browser_idle_cleanup_keeps_global_permission_available_for_other_sessions():
    from web.api import browser_runtime

    sid_a = "permission-contract-idle-session-a"
    sid_b = "permission-contract-idle-session-b"
    browser_runtime.browser_permission_revoke(sid_a)
    browser_runtime.browser_permission_revoke(sid_b)

    try:
        granted = browser_runtime.browser_permission_grant(sid_a, "control")
        token = browser_runtime.browser_permission_token(sid_a)

        assert granted["mode"] == "control"
        assert browser_runtime.browser_permission_status(sid_b)["mode"] == "control"

        browser_runtime._forget_session_permission(sid_a)

        assert sid_a not in browser_runtime._PERMISSIONS
        assert browser_runtime._GLOBAL_PERMISSION["mode"] == "control"
        assert browser_runtime._GLOBAL_PERMISSION["token"] == token
        assert browser_runtime.browser_permission_status(sid_b)["mode"] == "control"
        assert browser_runtime.browser_permission_token(sid_b) == token
        assert browser_runtime.browser_permission_token_valid(sid_b, token, "control") is True
    finally:
        browser_runtime.browser_permission_revoke(sid_a)
        browser_runtime.browser_permission_revoke(sid_b)


def test_browser_ui_surfaces_permission_steps_on_blocked_actions():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    apply_start = browser_js.index("function _browserApplyAgentActionButton")
    apply_end = browser_js.index("function _browserPermissionStepLabel", apply_start)
    apply_body = browser_js[apply_start:apply_end]
    warn_start = browser_js.index("function _browserWarnAgentActionBlocked")
    warn_end = browser_js.index("function _browserRefreshHeaderMenu", warn_start)
    warn_body = browser_js[warn_start:warn_end]

    assert "permission_step_labels" in apply_body
    assert "permission_steps" in apply_body
    assert "const approvalMode = String((action && action.approval_mode)" in apply_body
    assert "titleParts.push('approval: ' + approvalLabel);" in apply_body
    assert "const activeGoal = _browserActiveGoalForCurrentSession();" in apply_body
    assert "titleParts.push('goal: ' + activeGoalText);" in apply_body
    assert "steps: " in apply_body
    assert "button.setAttribute('aria-label', button.title)" in apply_body
    assert "permission_step_labels" in warn_body
    assert "permission_steps" in warn_body
    assert "const approvalMode = String(action.approval_mode" in warn_body
    assert "approvalLabel ? ' \u00b7 approval: ' + approvalLabel : ''" in warn_body
    assert "const activeGoal = _browserActiveGoalForCurrentSession();" in warn_body
    assert "activeGoalText ? ' \u00b7 goal: ' + activeGoalText : ''" in warn_body
    assert "steps: " in warn_body
    assert "showToast(message, 3200, 'warning')" in warn_body


def test_browser_session_switch_keeps_visible_permission_state():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert "const _BROWSER_PERMISSION_STORAGE_KEY = 'sidekick-browser-permission-mode';" in browser_js
    assert "function _browserRememberedPermissionMode()" in browser_js
    assert "function _browserPersistPermissionMode(mode)" in browser_js

    start = browser_js.index("function browserPrepareSessionSwitch()")
    end = browser_js.index("function browserSetDrawerOpen", start)
    body = browser_js[start:end]
    fetch_start = browser_js.index("async function _browserFetchState(sessionId)")
    fetch_end = browser_js.index("function _browserHandleStreamPayload", fetch_start)
    fetch_body = browser_js[fetch_start:fetch_end]
    init_start = browser_js.index("function _browserInitializeRuntime()")
    init_end = browser_js.index("if (document.readyState === 'complete' || document.readyState === 'interactive')", init_start)
    init_body = browser_js[init_start:init_end]

    assert "browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false})" in body
    assert "browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false})" in init_body
    assert "void browserRefreshPermission();" in fetch_body
    assert "if (!permission || permission.persist !== false)" in browser_js
    assert "browserRenderPermission({mode: previousMode, persist: false})" in browser_js


def test_browser_goal_sync_uses_route_session_fallback_when_session_object_is_unavailable():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")

    assert "if(typeof _sessionIdFromLocation==='function')" in messages_js
    assert "const currentSid = locationSessionId || sessionId || '';" in messages_js
    assert "workspace: (session && session.workspace) || ''" in messages_js
    assert "profile: (session && session.profile) || _goalActiveProfile()" in messages_js


def test_browser_goal_sync_uses_route_workspace_fallback_before_session_hydration():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")

    assert "new URLSearchParams(window.location&&window.location.search||'')" in messages_js
    assert "const workspace=String((session&&session.workspace)||_goalActiveSpace()||'').trim();" in messages_js
    assert "workspace," in messages_js


def test_browser_ui_surfaces_stale_frame_errors_as_retryable_state():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert "function _browserIsStaleFrameError(err)" in browser_js
    assert "function _browserErrorData(err)" in browser_js
    assert "err.data && typeof err.data === 'object'" in browser_js
    assert "data.error && typeof data.error === 'object'" in browser_js
    assert "code === 'browser_frame_stale'" in browser_js
    assert "browser frame changed since inspection" in browser_js
    assert "function _browserStaleFrameMessage(err)" in browser_js
    assert "expected_frame_rev" in browser_js
    assert "current_frame_rev" in browser_js
    assert "Refresh snapshot and retry." in browser_js
    assert "const staleFrame = _browserIsStaleFrameError(err);" in browser_js
    assert "const text = staleFrame" in browser_js
    assert "? _browserStaleFrameMessage(err)" in browser_js
    assert "if (staleFrame) _browserSetActionSummary('Blocked: ' + text);" in browser_js
    assert "void browserSyncToCurrentSession({force: true, allowPending: true});" in browser_js
    assert "function _browserFrameBoundControlPayload(action, payload)" in browser_js
    assert "act === 'click' || act === 'scroll' || act === 'move'" in browser_js
    assert "next.expected_frame_rev = state.frame_rev;" in browser_js
    assert "const controlPayload = _browserFrameBoundControlPayload(action, payload);" in browser_js
    assert "expected_frame_rev: state.frame_rev != null ? state.frame_rev : undefined" in browser_js


def test_streaming_copilot_protocol_for_blocked_browser_actions_is_explicit():
    streaming_py = Path("web/api/streaming.py").read_text(encoding="utf-8")

    assert "inspect SIDEKICK_WEBUI_BROWSER_AGENT_CONTEXT_URL when available" in streaming_py
    assert "follow its recommended_action/available_actions instead of guessing permissions or endpoints" in streaming_py
    assert "When browser agent-context exposes active_goal" in streaming_py
    assert "do not silently switch to a narrower success condition" in streaming_py
    assert "report the required_mode and the permission_step_labels/permission_steps exactly" in streaming_py
    assert "do not retry control actions until the user has completed the required browser watch/control step" in streaming_py
    assert "If a browser control call returns HTTP 409 or code browser_frame_stale" in streaming_py
    assert "retry at most once using the new frame revision" in streaming_py
    assert "When browser agent-context exposes approval_mode" in streaming_py
    assert "current user-control mode (manual/smart/off)" in streaming_py


def test_streaming_copilot_protocol_has_deepseek_glm_precision_mode():
    streaming_py = Path("web/api/streaming.py").read_text(encoding="utf-8")

    protocol_start = streaming_py.index("_model_family_hint =")
    protocol_end = streaming_py.index("_copilot_protocol = ", protocol_start)
    protocol_body = streaming_py[protocol_start:protocol_end]

    assert '"deepseek", "glm", "zai", "z.ai", "z-ai"' in protocol_body
    assert "DeepSeek/GLM mode: use short explicit checklists" in protocol_body
    assert "avoid vague success claims" in protocol_body
    assert "restate the final evidence succinctly" in protocol_body


def test_browser_agent_context_url_is_exposed_to_agent_and_ui():
    routes_py = Path("web/api/routes.py").read_text(encoding="utf-8")
    streaming_py = Path("web/api/streaming.py").read_text(encoding="utf-8")
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert 'if parsed.path == "/api/browser/agent-context":' in routes_py
    assert "return _handle_browser_agent_context(handler, parsed)" in routes_py
    assert "def _handle_browser_agent_context(handler, parsed):" in routes_py
    assert "context = browser_agent_context(sid)" in routes_py
    assert 'return j(handler, {"context": context})' in routes_py

    assert "'SIDEKICK_WEBUI_BROWSER_AGENT_CONTEXT_URL':" in streaming_py
    assert 'f"{_browser_base_url}/api/browser/agent-context?session_id={session_id}"' in streaming_py
    assert "inspect SIDEKICK_WEBUI_BROWSER_AGENT_CONTEXT_URL when available" in streaming_py

    assert "async function browserRefreshAgentContext(sessionId)" in browser_js
    assert "api('/api/browser/agent-context?session_id=' + encodeURIComponent(sid))" in browser_js
    assert "_browserAgentContext = context;" in browser_js
    assert "_browserUpdateHeaderBadge();" in browser_js
    assert "approval_mode: approvalMode" in browser_js
    assert "approval_mode: backendContext.approval_mode || localContext.approval_mode" in browser_js
    assert "const goalState = (typeof window !== 'undefined' && window._goalState" in browser_js
    assert "active_goal: activeGoal" in browser_js
    assert "active_goal: backendContext.active_goal || localContext.active_goal" in browser_js
    assert "expected_frame_rev: state.frame_rev == null ? null : state.frame_rev" in browser_js
    assert "expected_frame_rev: backendContext.expected_frame_rev != null ? backendContext.expected_frame_rev : localContext.expected_frame_rev" in browser_js
    assert "const approvalMode = String((typeof window !== 'undefined' && window._approvalMode) || (agentContext && agentContext.approval_mode)" in browser_js
    assert "const approvalLabel = ['manual', 'smart', 'off'].includes(approvalMode) ? approvalMode : '';" in browser_js
    assert "function _browserActiveGoalForCurrentSession(sessionId)" in browser_js
    assert "const activeGoal = _browserActiveGoalForCurrentSession();" in browser_js
    assert "const activeGoal = _browserActiveGoalForCurrentSession(sid);" in browser_js
    assert "const fallbackGoal = _browserActiveGoalForCurrentSession(result && result.session_id);" in browser_js
    assert "const activeGoalLabel = activeGoalText ? 'goal active' : '';" in browser_js
    assert "approvalLabel ? ' \u00b7 approval ' + approvalLabel : ''" in browser_js
    assert "activeGoalLabel ?" in browser_js
    assert "goal active" in browser_js
    assert "parts.push('approval mode ' + approvalLabel);" in browser_js
    assert "parts.push('active goal: ' + activeGoalText);" in browser_js


def test_browser_agent_context_guides_permission_escalation():
    from web.api import browser_runtime

    sid = "agent-context-contract-session"
    browser_runtime.browser_permission_revoke(sid)

    locked = browser_runtime.browser_agent_context(sid)
    locked_actions = locked["available_actions"]
    assert locked["recommended_action"] == "request_read_permission"
    assert locked_actions["snapshot"]["available"] is False
    assert locked_actions["snapshot"]["approval_mode"] in {"manual", "smart", "off"}
    assert locked_actions["snapshot"]["active_goal"]["session_id"] == sid
    assert locked_actions["snapshot"]["required_permission"] == "read"
    assert locked_actions["snapshot"]["permission_steps"] == ["enable_browser_watch"]
    assert locked_actions["snapshot"]["permission_step_labels"] == ["Enable browser watch"]
    assert locked_actions["navigate"]["available"] is False
    assert locked_actions["navigate"]["required_permission"] == "control"
    assert locked_actions["navigate"]["permission_steps"] == ["enable_browser_watch", "enable_browser_control"]
    assert locked_actions["navigate"]["permission_step_labels"] == ["Enable browser watch", "Enable browser control"]

    browser_runtime.browser_permission_grant(sid, "read")
    read_only = browser_runtime.browser_agent_context(sid)
    read_actions = read_only["available_actions"]
    assert read_only["recommended_action"] == "request_control_permission"
    assert read_actions["navigate"]["available"] is False
    assert read_actions["navigate"]["permission_steps"] == ["enable_browser_control"]
    assert read_actions["navigate"]["permission_step_labels"] == ["Enable browser control"]
    assert read_actions["snapshot"]["required_permission"] == "read"
    assert read_actions["snapshot"]["permission_steps"] == []

    browser_runtime.browser_permission_grant(sid, "control")
    control = browser_runtime.browser_agent_context(sid)
    control_actions = control["available_actions"]
    assert control["recommended_action"] == "navigate"
    assert control["approval_mode"] in {"manual", "smart", "off"}
    assert control["approval_modes"] == ["manual", "smart", "off"]
    assert control["active_goal"]["available"] is True
    assert control["active_goal"]["session_id"] == sid
    assert control["active_goal"]["present"] in {True, False}
    assert "expected_frame_rev" in control
    assert control_actions["navigate"]["available"] is True
    assert control_actions["navigate"]["approval_mode"] == control["approval_mode"]
    assert control_actions["navigate"]["active_goal"]["session_id"] == sid
    assert control_actions["navigate"]["permission_steps"] == []

    browser_runtime.browser_permission_revoke(sid)


def test_browser_control_actions_can_be_guarded_by_observed_frame_revision():
    runtime_py = Path("web/api/browser_runtime.py").read_text(encoding="utf-8")
    streaming_py = Path("web/api/streaming.py").read_text(encoding="utf-8")
    routes_py = Path("web/api/routes.py").read_text(encoding="utf-8")

    assert "def _expected_frame_rev(self, payload: dict[str, Any] | None = None) -> int | None:" in runtime_py
    assert 'for key in ("expected_frame_rev", "observed_frame_rev"):' in runtime_py
    assert "def _frame_rev_guard(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:" in runtime_py
    assert "def _normalize_approval_mode(mode: Any = None) -> str:" in runtime_py
    assert "def _current_approval_mode() -> str:" in runtime_py
    assert "active_goal = _active_goal_context(session_id)" in runtime_py
    assert "approval_mode = _current_approval_mode()" in runtime_py
    assert '"approval_mode": approval_mode' in runtime_py
    assert '"approval_modes": ["manual", "smart", "off"]' in runtime_py
    assert '"active_goal": active_goal' in runtime_py
    assert '"approval_mode": approval_mode' in runtime_py
    assert '"code": "browser_frame_stale"' in runtime_py
    assert '"expected_frame_rev": expected' in runtime_py
    assert '"current_frame_rev": current' in runtime_py
    assert 'sid = str(getattr(self, "session_id", "") or state.get("session_id") or "")' in runtime_py
    assert "active_goal = _active_goal_context(sid)" in runtime_py
    assert '"active_goal": active_goal' in runtime_py
    assert "Refresh snapshot and retry." in runtime_py
    assert "if act == \"open\":" in runtime_py
    assert "stale_frame = self._frame_rev_guard(payload)" in runtime_py
    assert "if stale_frame:" in runtime_py
    assert "return stale_frame" in runtime_py
    assert '"expected_frame_rev": frame_rev' in runtime_py
    assert '"expected_frame_rev": frame_rev' in runtime_py
    assert "include the current expected_frame_rev from browser agent-context/action payloads" in streaming_py
    assert "control fails safely if the user-visible frame changed since inspection" in streaming_py
    assert 'if act in {"click", "scroll", "move"}:' in runtime_py
    assert "stale_frame = session._frame_rev_guard(payload)" in runtime_py
    assert 'state.get("code") == "browser_frame_stale"' in routes_py
    assert "return j(handler, state, status=409)" in routes_py
    assert '409 if result.get("code") == "browser_frame_stale" else 400' in routes_py


def test_browser_frame_rev_guard_blocks_mismatched_revision_payload():
    from web.api.browser_runtime import BrowserSession

    class _Snapshot:
        frame_rev = 7

        def to_dict(self):
            return {"frame_rev": self.frame_rev, "session_id": "frame-guard-test"}

    session = object.__new__(BrowserSession)
    session._snapshot = _Snapshot()

    assert session._frame_rev_guard({}) is None
    assert session._frame_rev_guard({"expected_frame_rev": 7}) is None
    assert session._frame_rev_guard({"observed_frame_rev": "7"}) is None

    stale = session._frame_rev_guard({"expected_frame_rev": 6})
    assert stale["ok"] is False
    assert stale["code"] == "browser_frame_stale"
    assert stale["expected_frame_rev"] == 6
    assert stale["current_frame_rev"] == 7
    assert stale["approval_mode"] in {"manual", "smart", "off"}
    assert stale["active_goal"]["session_id"] == "frame-guard-test"
    assert stale["state"]["frame_rev"] == 7
