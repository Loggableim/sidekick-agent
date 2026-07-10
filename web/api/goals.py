"""WebUI bridge for Sidekick persistent session goals."""

from __future__ import annotations

import copy
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:  # Exposed as a module attribute so tests can monkeypatch it directly.
    from cli.goals import (  # type: ignore
        CONTINUATION_PROMPT_TEMPLATE,
        DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES,
        DEFAULT_MAX_TURNS,
        GoalManager as _NativeGoalManager,
        GoalState,
        format_goal_turn_budget,
        judge_goal,
        normalize_goal_turn_budget,
    )
except Exception:  # pragma: no cover - depends on installed sidekick-agent
    CONTINUATION_PROMPT_TEMPLATE = ""  # type: ignore
    DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3  # type: ignore
    DEFAULT_MAX_TURNS = 20  # type: ignore
    _NativeGoalManager = None  # type: ignore
    GoalState = None  # type: ignore
    judge_goal = None  # type: ignore
    def format_goal_turn_budget(max_turns):  # type: ignore
        try:
            value = int(max_turns) if max_turns is not None else None
        except (TypeError, ValueError):
            value = None
        return "∞" if value is None or value <= 0 else str(value)

    def normalize_goal_turn_budget(max_turns, *, default=20, unlimited=False):  # type: ignore
        if unlimited:
            return None
        if max_turns is None:
            return int(default or DEFAULT_MAX_TURNS or 20)
        try:
            value = int(max_turns)
        except (TypeError, ValueError):
            return int(default or DEFAULT_MAX_TURNS or 20)
        return None if value <= 0 else value

GoalManager = _NativeGoalManager  # type: ignore

_DB_CACHE: dict[str, Any] = {}


def _default_max_turns() -> int:
    """Return the configured /goal turn budget, defaulting to Sidekick' 20 turns."""
    try:
        from web.api import config as _config

        cfg = getattr(_config, "cfg", {}) or {}
        goals_cfg = cfg.get("goals", {}) if isinstance(cfg, dict) else {}
        if not isinstance(goals_cfg, dict):
            return int(DEFAULT_MAX_TURNS or 20)
        return max(1, int(goals_cfg.get("max_turns", DEFAULT_MAX_TURNS or 20) or 20))
    except Exception:
        return int(DEFAULT_MAX_TURNS or 20)


def _meta_key(session_id: str) -> str:
    return f"goal:{session_id}"


def _profile_db(profile_home: str | Path, *, space_slug: str | None = None):
    """Return a SessionDB pinned to *profile_home*, without reading SIDEKICK_HOME.

    The upstream Sidekick GoalManager persists through sidekick_cli.goals.load_goal(),
    which resolves SessionDB from process-global SIDEKICK_HOME. WebUI sessions are
    profile-scoped and can run concurrently, so the WebUI bridge uses an explicit
    state.db path whenever the caller provides the session's profile home.
    When in a space context, returns a space-scoped goals.db instead.
    """
    # 1. space-scoped
    sp = _space_goals_path(space_slug)
    if sp:
        key = str(sp)
        cached = _DB_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            from runtime._compat.shim_state import SessionDB  # type: ignore

            db = SessionDB(db_path=sp)
        except Exception as exc:
            logger.debug("GoalManager space DB unavailable at %s: %s", sp, exc)
            return None
        _DB_CACHE[key] = db
        return db

    # 2. fallback: profile-scoped
    home = Path(profile_home).expanduser().resolve()
    key = str(home)
    cached = _DB_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from runtime._compat.shim_state import SessionDB  # type: ignore

        db = SessionDB(db_path=home / "state.db")
    except Exception as exc:  # pragma: no cover - import/env dependent
        logger.debug("GoalManager profile DB unavailable for %s: %s", home, exc)
        return None
    _DB_CACHE[key] = db
    return db


def _space_goals_path(space_slug: str | None = None) -> Path | None:
    """Return path to space-scoped goals.db, or None if not in a space context."""
    try:
        from web.api.space_engine import DEFAULT_SPACE_SLUG, get_workspace, resolve_active_space

        if space_slug:
            space = get_workspace(space_slug)
            if space is None and space_slug == "default":
                space = get_workspace(DEFAULT_SPACE_SLUG)
            if space is None:
                return None
        else:
            space = resolve_active_space()
        p = space.root / "goals.db"
        return p
    except Exception:
        return None


class _ProfileGoalManager:
    """Small WebUI-local GoalManager adapter with explicit profile persistence."""

    def __init__(self, session_id: str, *, profile_home: str | Path, default_max_turns: int = 20, space_slug: str | None = None):
        if GoalState is None:
            raise RuntimeError("Sidekick goal state unavailable")
        self.session_id = session_id
        self.profile_home = Path(profile_home).expanduser().resolve()
        self.space_slug = str(space_slug or "").strip().lower() or None
        self.default_max_turns = int(default_max_turns or DEFAULT_MAX_TURNS or 20)
        self._state = self._load()

    @property
    def state(self):
        return self._state

    def _load(self):
        db = _profile_db(self.profile_home, space_slug=self.space_slug)
        if db is None or not self.session_id:
            return None
        try:
            raw = db.get_meta(_meta_key(self.session_id))
        except Exception as exc:
            logger.debug("GoalManager profile get_meta failed: %s", exc)
            return None
        if not raw:
            return None
        try:
            return GoalState.from_json(raw)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("GoalManager profile state parse failed for %s: %s", self.session_id, exc)
            return None

    def _save(self, state) -> None:
        db = _profile_db(self.profile_home, space_slug=self.space_slug)
        if db is None or not self.session_id or state is None:
            return
        try:
            db.set_meta(_meta_key(self.session_id), state.to_json())
        except Exception as exc:
            logger.debug("GoalManager profile set_meta failed: %s", exc)

    def is_active(self) -> bool:
        return self._state is not None and self._state.status == "active"

    def has_goal(self) -> bool:
        return self._state is not None and self._state.status in ("active", "paused")

    def status_line(self) -> str:
        s = self._state
        if s is None or s.status in ("cleared",):
            return "No active goal. Set one with /goal <text>."
        turns = f"{s.turns_used}/{format_goal_turn_budget(s.max_turns)} turns"
        if s.status == "active":
            return f"⊙ Goal (active, {turns}): {s.goal}"
        if s.status == "paused":
            extra = f" — {s.paused_reason}" if s.paused_reason else ""
            return f"⏸ Goal (paused, {turns}{extra}): {s.goal}"
        if s.status == "done":
            return f"✓ Goal done ({turns}): {s.goal}"
        return f"Goal ({s.status}, {turns}): {s.goal}"

    def set(
        self,
        goal: str,
        *,
        max_turns: Optional[int] = None,
        unlimited: bool = False,
    ):
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal text is empty")
        state = GoalState(  # type: ignore[operator]
            goal=goal,
            status="active",
            turns_used=0,
            max_turns=normalize_goal_turn_budget(
                max_turns,
                default=self.default_max_turns,
                unlimited=unlimited,
            ),
            created_at=time.time(),
            last_turn_at=0.0,
        )
        self._state = state
        self._save(state)
        return state

    def pause(self, reason: str = "user-paused"):
        if not self._state:
            return None
        self._state.status = "paused"
        self._state.paused_reason = reason
        self._save(self._state)
        return self._state

    def resume(self, *, reset_budget: bool = False):
        if not self._state:
            return None
        exhausted = (
            self._state.max_turns is not None
            and int(self._state.turns_used or 0) >= int(self._state.max_turns or 0)
        )
        if exhausted and not reset_budget:
            return self._state
        self._state.status = "active"
        self._state.paused_reason = None
        if reset_budget:
            self._state.turns_used = 0
        self._state.consecutive_parse_failures = 0
        self._save(self._state)
        return self._state

    def clear(self) -> None:
        if self._state is None:
            return
        self._state.status = "cleared"
        self._save(self._state)
        self._state = None

    def evaluate_after_turn(self, last_response: str, *, user_initiated: bool = True) -> Dict[str, Any]:
        state = self._state
        if state is None or state.status != "active":
            return {
                "status": state.status if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        state.turns_used += 1
        state.last_turn_at = time.time()

        if judge_goal is None:
            verdict, reason, parse_failed = "continue", "goal judge unavailable", False
        else:
            verdict, reason, parse_failed = judge_goal(state.goal, str(last_response or ""))
        state.last_verdict = verdict
        state.last_reason = reason
        if parse_failed:
            state.consecutive_parse_failures = int(getattr(state, "consecutive_parse_failures", 0) or 0) + 1
        else:
            state.consecutive_parse_failures = 0

        if verdict == "done":
            state.status = "done"
            self._save(state)
            return {
                "status": "done",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": f"✓ Goal achieved: {reason}",
            }

        if state.consecutive_parse_failures >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"judge model returned unparseable output {state.consecutive_parse_failures} turns in a row"
            )
            self._save(state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — the judge model ({state.consecutive_parse_failures} turns) "
                    "isn't returning the required JSON verdict. Route the judge to a stricter "
                    "model in ~/.sidekick/config.yaml:\n"
                    "  auxiliary:\n"
                    "    goal_judge:\n"
                    "      provider: openrouter\n"
                    "      model: google/gemini-3-flash-preview\n"
                    "Then /goal resume to continue."
                ),
            }

        if state.max_turns is not None and state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = (
                f"turn budget exhausted ({state.turns_used}/{format_goal_turn_budget(state.max_turns)})"
            )
            self._save(state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — {state.turns_used}/{format_goal_turn_budget(state.max_turns)} turns used. "
                    "Use /goal resume to keep going, or /goal clear to stop."
                ),
            }

        self._save(state)
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": f"↻ Continuing toward goal ({state.turns_used}/{format_goal_turn_budget(state.max_turns)}): {reason}",
        }

    def next_continuation_prompt(self) -> Optional[str]:
        if not self._state or self._state.status != "active":
            return None
        return CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)


def _manager(session_id: str, *, profile_home: str | Path | None = None, space_slug: str | None = None):
    if GoalManager is None:
        return None
    if (profile_home or space_slug) and GoalManager is _NativeGoalManager and GoalState is not None:
        try:
            effective_profile_home = profile_home
            if effective_profile_home is None:
                try:
                    from runtime._compat.shim_constants import get_sidekick_home as _get_sidekick_home

                    effective_profile_home = _get_sidekick_home()
                except Exception:
                    effective_profile_home = Path(".")
            return _ProfileGoalManager(
                session_id=session_id,
                profile_home=effective_profile_home,
                default_max_turns=_default_max_turns(),
                space_slug=space_slug,
            )
        except Exception as exc:
            logger.debug("Profile-scoped GoalManager unavailable: %s", exc)
            return None
    return GoalManager(session_id=session_id, default_max_turns=_default_max_turns())


def _state_payload(
    state: Any,
    session_id: str = "",
    *,
    space_slug: str | None = None,
) -> Optional[Dict[str, Any]]:
    if state is None:
        return None
    space = str(space_slug or getattr(state, "space", "") or getattr(state, "space_slug", "") or "").strip().lower()
    return {
        "goal": getattr(state, "goal", "") or "",
        "status": getattr(state, "status", "") or "",
        "turns_used": int(getattr(state, "turns_used", 0) or 0),
        "max_turns": getattr(state, "max_turns", None),
        "last_verdict": getattr(state, "last_verdict", None),
        "last_reason": getattr(state, "last_reason", None),
        "paused_reason": getattr(state, "paused_reason", None),
        "session_id": str(session_id).strip() if session_id else "",
        **({"space": space} if space else {}),
    }


def _payload(
    *,
    ok: bool = True,
    action: str,
    message: str,
    state: Any = None,
    space_slug: str | None = None,
    error: str | None = None,
    kickoff_prompt: str | None = None,
    decision: Dict[str, Any] | None = None,
    message_key: str | None = None,
    message_args: list[Any] | None = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "ok": bool(ok),
        "action": action,
        "message": message,
        "goal": _state_payload(state, space_slug=space_slug),
    }
    if error:
        body["error"] = error
    if kickoff_prompt:
        body["kickoff_prompt"] = kickoff_prompt
    if decision is not None:
        body["decision"] = decision
    if message_key:
        body["message_key"] = message_key
    if message_args is not None:
        body["message_args"] = [a for a in message_args if a is not None]
    return body


def _goal_status_payload(state: Any, *, default_message: str | None = None) -> Dict[str, Any]:
    """Build localized-status style payload fields from a goal state."""
    if default_message is None:
        default_message = "No active goal. Set one with /goal <text>."
    if state is None:
        return {"message": default_message, "message_key": "goal_status_none"}
    status = str(getattr(state, "status", "") or "").strip()
    if status in ("cleared",):
        return {"message": default_message, "message_key": "goal_status_none"}
    turns_used = int(getattr(state, "turns_used", 0) or 0)
    max_turns = getattr(state, "max_turns", None)
    budget_label = format_goal_turn_budget(max_turns)
    goal = str(getattr(state, "goal", "") or "")
    if status == "active":
        return {
            "message": f"⊙ Goal (active, {turns_used}/{budget_label} turns): {goal}",
            "message_key": "goal_status_active",
            "message_args": [turns_used, budget_label, goal],
        }
    if status == "paused":
        reason = str(getattr(state, "paused_reason", "") or "")
        exhausted = (
            max_turns is not None
            and turns_used >= int(max_turns)
        ) or "budget exhausted" in reason.lower() or "turn budget exhausted" in reason.lower()
        if exhausted:
            return {
                "message": f"⏸ Goal paused — {turns_used}/{budget_label} turns used. Use /goal resume to keep going, or /goal clear to stop.",
                "message_key": "goal_paused_budget_exhausted",
                "message_args": [turns_used, budget_label],
            }
        return {
            "message": f"⏸ Goal (paused, {turns_used}/{budget_label}{' — ' + reason if reason else ''}): {goal}",
            "message_key": "goal_status_paused",
            "message_args": [turns_used, budget_label, reason, goal],
        }
    if status == "done":
        return {
            "message": f"✓ Goal done ({turns_used}/{budget_label}): {goal}",
            "message_key": "goal_status_done",
            "message_args": [turns_used, budget_label, goal],
        }
    return {
        "message": f"Goal ({status}, {turns_used}/{budget_label}): {goal}",
        "message_args": [status, turns_used, budget_label, goal],
    }


def _extract_goal_turns_from_message(message: str) -> tuple[int, int]:
    """Best-effort extraction for continuation messages like '(1/20)'."""
    if not message:
        return 0, 0
    match = re.search(r"\((\d+)\s*/\s*(\d+)\)", message)
    if not match:
        return 0, 0
    try:
        return int(match.group(1)), int(match.group(2))
    except Exception:
        return 0, 0


def _goal_decision_payload(
    decision: Dict[str, Any],
    state: Any,
) -> Dict[str, Any]:
    """Attach goal message i18n key/args to an evaluation decision."""
    if not isinstance(decision, dict):
        return decision
    status = str(decision.get("status") or "").strip()
    reason = str(decision.get("reason") or "").strip()
    turns_used = int(getattr(state, "turns_used", 0) or 0)
    max_turns = getattr(state, "max_turns", None)
    budget_label = format_goal_turn_budget(max_turns)
    if (turns_used, max_turns) in ((0, 0), (0, None)):
        turns_used, parsed_max_turns = _extract_goal_turns_from_message(str(decision.get("message") or ""))
        if parsed_max_turns:
            max_turns = parsed_max_turns
            budget_label = format_goal_turn_budget(max_turns)

    if status == "done":
        return {
            **decision,
            "turns_used": turns_used,
            "max_turns": max_turns,
            "message_key": "goal_achieved",
            "message_args": [reason],
        }
    if status == "paused":
        return {
            **decision,
            "turns_used": turns_used,
            "max_turns": max_turns,
            "message_key": "goal_paused_budget_exhausted",
            "message_args": [turns_used, budget_label],
        }
    if decision.get("should_continue"):
        return {
            **decision,
            "turns_used": turns_used,
            "max_turns": max_turns,
            "message_key": "goal_continuing",
            "message_args": [turns_used, budget_label, reason],
        }
    return {
        **decision,
        "turns_used": turns_used,
        "max_turns": max_turns,
    }


def goal_state_snapshot(
    session_id: str,
    *,
    profile_home: str | Path | None = None,
    space_slug: str | None = None,
) -> Any:
    """Return a deep copy of current goal state for rollback before kickoff."""
    mgr = _manager(str(session_id or ""), profile_home=profile_home, space_slug=space_slug)
    if mgr is None:
        return None
    return copy.deepcopy(getattr(mgr, "state", None))


def restore_goal_state(
    session_id: str,
    snapshot: Any,
    *,
    profile_home: str | Path | None = None,
    space_slug: str | None = None,
) -> None:
    """Restore a prior goal state after kickoff stream creation fails."""
    mgr = _manager(str(session_id or ""), profile_home=profile_home, space_slug=space_slug)
    if mgr is None:
        return
    if snapshot is None:
        try:
            mgr.clear()
        except Exception:
            pass
        return
    if isinstance(mgr, _ProfileGoalManager):
        mgr._state = snapshot
        mgr._save(snapshot)
        return
    try:
        from cli.goals import save_goal  # type: ignore

        save_goal(str(session_id or ""), snapshot)
    except Exception as exc:  # pragma: no cover - native fallback only
        logger.debug("Goal state restore failed for %s: %s", session_id, exc)


def goal_state_for_session(
    session_id: str,
    *,
    profile_home: str | Path | None = None,
    space_slug: str | None = None,
) -> Optional[Dict[str, Any]]:
    """Return the persisted goal payload for a session, if any."""
    mgr = _manager(str(session_id or ""), profile_home=profile_home, space_slug=space_slug)
    if mgr is None:
        return None
    state = getattr(mgr, "state", None)
    if state is None:
        return None
    if str(getattr(state, "status", "") or "").strip() == "cleared":
        return None
    return _state_payload(state, str(session_id or ""), space_slug=space_slug)


def goal_command_payload(
    session_id: str,
    args: str = "",
    *,
    stream_running: bool = False,
    profile_home: str | Path | None = None,
    space_slug: str | None = None,
    max_turns: Optional[int] = None,
    unlimited: bool = False,
) -> Dict[str, Any]:
    """Return the WebUI response payload for a /goal command.

    Mirrors the gateway command semantics:
    - /goal or /goal status shows status
    - /goal pause pauses
    - /goal resume resumes without auto-starting a turn
    - /goal clear|stop|done clears
    - /goal <text> sets a new active goal and returns kickoff_prompt so the
      caller can start the first normal user-role turn immediately.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return _payload(ok=False, action="error", error="missing_session", message="session_id required", space_slug=space_slug)

    mgr = _manager(sid, profile_home=profile_home, space_slug=space_slug)
    if mgr is None:
        return _payload(ok=False, action="error", error="unavailable", message="Goals unavailable on this session.", space_slug=space_slug)

    text = str(args or "").strip()
    lower = text.lower()

    if not text or lower == "status":
        state = getattr(mgr, "state", None)
        status_payload = _goal_status_payload(state)
        state_status = str(getattr(state, "status", "") or "").strip()
        visible_state = None if state_status == "cleared" else state
        return _payload(action="status", state=visible_state, space_slug=space_slug, **status_payload)

    if lower == "pause":
        state = mgr.pause(reason="user-paused")
        if state is None:
            return _payload(
                ok=False,
                action="pause",
                error="no_goal",
                message="No goal set.",
                message_key="goal_no_goal",
                space_slug=space_slug,
            )
        return _payload(
            action="pause",
            message=f"⏸ Goal paused: {state.goal}",
            message_key="goal_paused",
            message_args=[str(state.goal)],
            state=state,
            space_slug=space_slug,
        )

    if lower == "resume":
        state = mgr.resume()
        if state is None:
            return _payload(
                ok=False,
                action="resume",
                error="no_goal",
                message="No goal to resume.",
                message_key="goal_no_goal",
                space_slug=space_slug,
            )
        if str(getattr(state, "status", "") or "").strip() != "active":
            status_payload = _goal_status_payload(state, default_message="Goal remains paused.")
            return _payload(
                action="resume",
                message=status_payload["message"],
                message_key=status_payload.get("message_key"),
                message_args=status_payload.get("message_args"),
                state=state,
                space_slug=space_slug,
            )
        return _payload(
            action="resume",
            message=(
                f"▶ Goal resumed: {state.goal}\n"
                "Send a new message, or type continue, to kick it off."
            ),
            message_key="goal_resumed",
            message_args=[str(state.goal)],
            state=state,
            space_slug=space_slug,
        )

    if lower in ("clear", "stop", "done"):
        had = bool(mgr.has_goal())
        mgr.clear()
        return _payload(
            action="clear",
            message="Goal cleared." if had else "No active goal.",
            message_key="goal_cleared" if had else "goal_no_goal",
            state=getattr(mgr, "state", None),
            space_slug=space_slug,
        )

    if stream_running:
        return _payload(
            ok=False,
            action="set",
            error="agent_running",
            message=(
                "Agent is running — use /goal status / pause / clear mid-run, "
                "or /stop before setting a new goal."
            ),
            space_slug=space_slug,
        )

    try:
        state = mgr.set(text, max_turns=max_turns, unlimited=unlimited)
    except ValueError as exc:
        return _payload(ok=False, action="set", error="invalid_goal", message=f"Invalid goal: {exc}", space_slug=space_slug)

    budget_label = "unlimited runs" if getattr(state, "max_turns", None) is None else f"{state.max_turns} runs"
    followup = (
        "I'll keep working until the goal is done, you pause/clear it, or you stop it.\n"
        if getattr(state, "max_turns", None) is None
        else "I'll keep working until the goal is done, you pause/clear it, or the budget is exhausted.\n"
    )

    return _payload(
        action="set",
        message=(
            f"⊙ Goal set ({budget_label}): {state.goal}\n"
            f"{followup}"
            "Controls: /goal status · /goal pause · /goal resume · /goal clear"
        ),
        state=state,
        kickoff_prompt=state.goal,
        space_slug=space_slug,
    )


def has_active_goal(
    session_id: str,
    *,
    profile_home: str | Path | None = None,
    space_slug: str | None = None,
) -> bool:
    """Return True when the session has an active standing goal to evaluate."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    mgr = _manager(sid, profile_home=profile_home, space_slug=space_slug)
    if mgr is None:
        return False
    try:
        return bool(mgr.is_active())
    except Exception as exc:
        logger.debug("goal active-state check failed for session=%s: %s", sid, exc)
        return False


def evaluate_goal_after_turn(
    session_id: str,
    last_response: str,
    *,
    user_initiated: bool = True,
    profile_home: str | Path | None = None,
    space_slug: str | None = None,
) -> Dict[str, Any]:
    """Evaluate a completed turn against the standing goal, if any."""
    sid = str(session_id or "").strip()
    if not sid:
        return {
            "status": None,
            "should_continue": False,
            "continuation_prompt": None,
            "verdict": "inactive",
            "reason": "missing session_id",
            "message": "",
        }
    mgr = _manager(sid, profile_home=profile_home, space_slug=space_slug)
    if mgr is None:
        return {
            "status": None,
            "should_continue": False,
            "continuation_prompt": None,
            "verdict": "inactive",
            "reason": "goals unavailable",
            "message": "",
        }
    try:
        if not mgr.is_active():
            return {
                "status": getattr(getattr(mgr, "state", None), "status", None),
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }
        decision = mgr.evaluate_after_turn(str(last_response or ""), user_initiated=user_initiated)
    except Exception as exc:
        logger.debug("goal evaluation failed for session=%s: %s", sid, exc)
        return {
            "status": None,
            "should_continue": False,
            "continuation_prompt": None,
            "verdict": "error",
            "reason": f"goal evaluation failed: {type(exc).__name__}",
            "message": "",
        }
    if not isinstance(decision, dict):
        decision = {}
    decision.setdefault("should_continue", False)
    decision.setdefault("continuation_prompt", None)
    decision.setdefault("message", "")
    decision = dict(decision)
    decision = _goal_decision_payload(decision, getattr(mgr, "state", None))
    return decision
