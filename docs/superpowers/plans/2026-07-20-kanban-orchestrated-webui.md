# WebUI Kanban Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make „kanban orchestriert“ activate the active WebUI Kanban board for the current chat session.

**Architecture:** Detect the imperative phrase with a pure helper, merge `kanban` into the per-session WebUI toolset, propagate the session mode into streaming, add explicit WebUI board-routing guidance, and reuse the thread-local Workspace/Board context for Kanban tool connections. The existing gateway dispatcher remains the executor.

**Tech Stack:** Python 3, threaded WebUI routes/SSE streaming, SQLite-backed Kanban API, pytest.

## Global Constraints

- Preserve unrelated dirty changes in the working tree.
- Do not change the existing gateway dispatcher or `max_spawn` behavior.
- Do not permanently enable Kanban for ordinary WebUI sessions without the trigger or explicit profile opt-in.
- Keep dispatcher worker lifecycle guidance and ownership guards unchanged.
- Write production code only after a focused regression test has failed for the expected reason.

---

### Task 1: Deterministic trigger and session merge

**Files:**
- Create: `web/api/kanban_orchestration.py`
- Test: `tests/test_kanban_orchestration.py`

**Interfaces:**
- `is_kanban_orchestration_request(message: str | None) -> bool`
- `activate_kanban_orchestration(session, message: str | None, default_toolsets: list[str] | None = None) -> bool`
- `session_has_kanban_orchestration(session) -> bool`

- [ ] **Step 1: Write failing tests**

```python
from types import SimpleNamespace


def test_trigger_accepts_direct_and_reverse_imperatives():
    from web.api.kanban_orchestration import is_kanban_orchestration_request
    assert is_kanban_orchestration_request("kanban orchestriert") is True
    assert is_kanban_orchestration_request("Kanban-Board orchestrieren") is True
    assert is_kanban_orchestration_request("orchestriere das über das Kanban-Board") is True


def test_trigger_rejects_noun_or_failure_report():
    from web.api.kanban_orchestration import is_kanban_orchestration_request
    assert is_kanban_orchestration_request("Die Kanban-Orchestrierung funktioniert nicht") is False
    assert is_kanban_orchestration_request("Was ist ein Kanban-Board?") is False


def test_activate_preserves_existing_tools_and_adds_kanban_once():
    from web.api.kanban_orchestration import activate_kanban_orchestration
    session = SimpleNamespace(enabled_toolsets=["terminal", "file"])
    assert activate_kanban_orchestration(session, "kanban orchestriert") is True
    assert session.enabled_toolsets == ["terminal", "file", "kanban"]


def test_activate_uses_defaults_when_session_has_no_override():
    from web.api.kanban_orchestration import activate_kanban_orchestration
    session = SimpleNamespace(enabled_toolsets=None)
    assert activate_kanban_orchestration(session, "kanban orchestriert", ["terminal"]) is True
    assert session.enabled_toolsets == ["terminal", "kanban"]
```

- [ ] **Step 2: Run RED**

Run `python -m pytest -q tests/test_kanban_orchestration.py` with `PYTHONPATH=C:\sidekick\sidekick`. Expected: import failure because the new module does not exist.

- [ ] **Step 3: Implement minimal helper**

Use normalized text, explicit German/English verb forms, a bounded reverse-order match, and no match for the noun `Orchestrierung`:

```python
_ORCHESTRATION_VERB = r"(?:orchestriert|orchestriere|orchestrieren|orchestrier|orchestrate|orchestrated|orchestrating)"


def is_kanban_orchestration_request(message):
    text = " ".join(str(message or "").strip().lower().split())
    if not text or "kanban" not in text:
        return False
    kanban = r"\bkanban(?:[-\s]+board)?\b"
    verb = rf"\b{_ORCHESTRATION_VERB}\b"
    return bool(re.search(rf"{kanban}\W+{verb}", text) or re.search(rf"{verb}(?:\W+\w+){{0,8}}\W+{kanban}", text))
```

Append exactly one lowercase `kanban` entry and preserve every other toolset. `session_has_kanban_orchestration` returns whether the session list contains `kanban`.

- [ ] **Step 4: Run GREEN**

Run the same focused pytest command. Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

Run `git add -- web/api/kanban_orchestration.py tests/test_kanban_orchestration.py` and `git commit -m "feat: detect WebUI Kanban orchestration intent"`.

### Task 2: Activate per-session WebUI mode

**Files:**
- Modify: `web/api/routes.py` in `_start_chat_stream_for_session`
- Modify: `web/api/streaming.py` in `_run_agent_streaming`
- Test: `tests/test_kanban_orchestration.py`

**Interfaces:** The route calls `activate_kanban_orchestration` before the existing pending-state save, using `_resolve_cli_toolsets()` only as the fallback. Streaming removes inferred `kanban` from ordinary WebUI sessions, retains explicit profile/session opt-ins, and sets/restores `SIDEKICK_KANBAN_ORCHESTRATED=1` for opted-in runs.

- [ ] **Step 1: Write failing session-mode tests**

```python
def test_non_triggered_session_does_not_gain_kanban():
    from web.api.kanban_orchestration import activate_kanban_orchestration
    from types import SimpleNamespace
    session = SimpleNamespace(enabled_toolsets=["terminal"])
    assert activate_kanban_orchestration(session, "create a normal file") is False
    assert session.enabled_toolsets == ["terminal"]


def test_session_mode_is_derived_from_persisted_toolset():
    from web.api.kanban_orchestration import session_has_kanban_orchestration
    from types import SimpleNamespace
    assert session_has_kanban_orchestration(SimpleNamespace(enabled_toolsets=["kanban"])) is True
    assert session_has_kanban_orchestration(SimpleNamespace(enabled_toolsets=["terminal"])) is False
```

- [ ] **Step 2: Run RED**

Run `python -m pytest -q tests/test_kanban_orchestration.py`. Expected: failure until the session-mode helper/integration exists.

- [ ] **Step 3: Implement route and streaming integration**

Inside the existing session lock, activate the mode before `_prepare_chat_start_session_for_stream`; do not add a second save. In streaming, inspect the loaded session before constructing `AIAgent`, filter `kanban` from ordinary inferred WebUI toolsets, keep it for an explicit opt-in, set the marker under the existing environment lock, and restore the prior marker in the existing cleanup path. Do not mutate global config.

- [ ] **Step 4: Run GREEN**

Run the focused pytest command. Expected: all Task 1 and Task 2 tests pass.

- [ ] **Step 5: Commit**

Run `git add -- web/api/routes.py web/api/streaming.py tests/test_kanban_orchestration.py` and `git commit -m "feat: activate Kanban orchestration per WebUI session"`.

### Task 3: Active-board connection and explicit prompt guidance

**Files:**
- Modify: `tools/kanban_tools.py`
- Modify: `runtime/prompt_builder.py`
- Modify: `run_agent.py`
- Test: `tests/test_kanban_orchestration.py`

**Interfaces:** `_connect()` keeps returning `(kb, conn)` and uses `web.api.kanban_bridge._conn()` only when the current thread has a WebUI workspace override. `KANBAN_WEBUI_ORCHESTRATOR_GUIDANCE` is separate from worker-only `KANBAN_GUIDANCE`.

- [ ] **Step 1: Write failing board/prompt tests**

```python
def test_kanban_tool_connection_reuses_webui_workspace(monkeypatch):
    from web.api import kanban_bridge
    from tools import kanban_tools
    sentinel = object()
    monkeypatch.setattr(kanban_bridge, "_conn", lambda: ("kb", sentinel))
    monkeypatch.setattr(kanban_bridge, "_get_ws_kanban_home", lambda: "workspace-root")
    assert kanban_tools._connect() == ("kb", sentinel)


def test_webui_prompt_names_active_board_semantics():
    from runtime.prompt_builder import KANBAN_WEBUI_ORCHESTRATOR_GUIDANCE
    text = KANBAN_WEBUI_ORCHESTRATOR_GUIDANCE.lower()
    assert "kanban orchestriert" in text
    assert "webui" in text
    assert "kanban_list" in text
    assert "dispatcher" in text
    assert "delegate_task" in text
```

- [ ] **Step 2: Run RED**

Run `python -m pytest -q tests/test_kanban_orchestration.py`. Expected: missing guidance/connection behavior.

- [ ] **Step 3: Implement minimal board/prompt changes**

In `_connect`, lazily import `web.api.kanban_bridge`; use its `_conn()` when `_get_ws_kanban_home()` is non-empty and preserve the current `sidekick_cli.kanban_db.connect()` path otherwise. Add a prompt block mapping the trigger to the active WebUI board, requiring `kanban_list`, concrete assignees, dependency links, dispatcher execution, and no `delegate_task` board substitution. In `AIAgent._build_system_prompt_parts`, use worker guidance only when `SIDEKICK_KANBAN_TASK` is set; otherwise use the WebUI block when Kanban tools are present.

- [ ] **Step 4: Run GREEN**

Run `python -m pytest -q tests/test_kanban_orchestration.py`. Expected: all new tests pass.

- [ ] **Step 5: Commit**

Run `git add -- tools/kanban_tools.py runtime/prompt_builder.py run_agent.py tests/test_kanban_orchestration.py` and `git commit -m "feat: route WebUI Kanban tools to active board"`.

### Task 4: Regression verification and scope inspection

**Files:** `tests/test_kanban_orchestration.py`, `tests/test_kanban_worker_capacity.py`, `tests/test_dashboard_health.py`.

- [ ] **Step 1:** Run `python -m pytest -q tests/test_kanban_orchestration.py` and require all new tests to pass.
- [ ] **Step 2:** Run `python -m pytest -q tests/test_kanban_worker_capacity.py` and require the existing capacity tests to pass.
- [ ] **Step 3:** Run `python -m pytest -q tests/test_dashboard_health.py -k kanban` and report unrelated failures separately.
- [ ] **Step 4:** Run `python -m compileall -q web/api/kanban_orchestration.py web/api/routes.py web/api/streaming.py tools/kanban_tools.py runtime/prompt_builder.py run_agent.py` and `git diff --check`.
- [ ] **Step 5:** Inspect `git status --short --branch`, `git diff --stat`, and `git log -5 --oneline`; confirm no existing dirty Kanban-cap files were swept into the feature commits and report whether a live WebUI listener was available.
