"""Nova personality lifecycle.

Repo code owns orchestration, schemas, guards, and API-safe views. Nova's
private evolving state stays under SIDEKICK_HOME/spaces/nova and is never a
repository asset.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web.api.nova_paths import get_nova_space_root

logger = logging.getLogger(__name__)

VISIBILITY_ORDER = {"public": 0, "private": 1, "sensitive": 2}
NOVA_STALE_EVENT_SECONDS = 300
SECRET_PATTERNS = (
    "auth.json",
    ".env",
    "cookie",
    "cookies",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "creds",
    "key",
    "payment",
    "admin",
)
EXTERNAL_MUTATION_TYPES = {"post", "send", "write_external", "delete", "payment", "admin", "mutate"}
LOCAL_STATE_MUTATION_TYPES = {"memory", "personality", "dream", "reflection", "journal", "local_state"}
AUTONOMY_LEVELS: dict[int, dict[str, Any]] = {
    0: {
        "name": "reactive",
        "description": "Only direct chat context is used. No autonomous ticks.",
        "allows": ["chat_context"],
        "external_mutations": False,
    },
    1: {
        "name": "inner_processes",
        "description": "Emotion, memory, diary, goals, dreams, and reflection may update local state.",
        "allows": ["local_memory", "emotion", "dreams", "journal", "reflection"],
        "external_mutations": False,
    },
    2: {
        "name": "read_and_analyze",
        "description": "Level 1 plus allowed-scope reads and analysis. External mutations remain blocked.",
        "allows": ["local_memory", "emotion", "dreams", "journal", "reflection", "read", "analyze"],
        "external_mutations": False,
    },
    3: {
        "name": "allowlisted_mutations",
        "description": "Allowlisted external mutations with audit log. Secrets/admin/payment/delete remain blocked.",
        "allows": ["level_2", "allowlisted_external_mutations"],
        "external_mutations": "allowlist_only",
    },
    4: {
        "name": "full_eigenleben",
        "description": "Broad autonomous external actions, still guarded against secrets/admin/payment/destructive actions.",
        "allows": ["level_3", "broad_external_actions"],
        "external_mutations": "guarded",
    },
}
MODEL_STRATEGY: dict[str, dict[str, Any]] = {
    "fast_classifier": {
        "model": "MiniCPM5-1B",
        "port": 8081,
        "used_for": ["event_classification", "lightweight_emotion_updates", "fast_state_labels"],
        "sync_allowed": True,
        "blocks_chat": False,
    },
    "deep_reflection": {
        "model": "Qwen3.6-12B",
        "port": 8082,
        "used_for": ["dream_tick", "async_reflection", "personality_synthesis"],
        "sync_allowed": False,
        "requires_gpu": True,
        "blocks_chat": False,
    },
}
NOVA_CRON_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "nova-substrate-heartbeat",
        "name": "Nova substrate heartbeat",
        "schedule": "every 1m",
        "script": "nova_substrate_heartbeat.py",
        "action": "background_tick",
    },
    {
        "id": "nova-background-tick",
        "name": "Nova background tick",
        "schedule": "every 5m",
        "script": "nova_background_tick.py",
        "action": "background_tick",
    },
    {
        "id": "nova-dream-reflection-tick",
        "name": "Nova dream/reflection tick",
        "schedule": "every 30m",
        "script": "nova_dream_reflection_tick.py",
        "action": "dream_tick",
    },
)
_LEGACY_NOVA_CRON_JOB_NAMES = frozenset({
    "Nova Substrate Heartbeat",
    "Nova Entity Kernel Tick",
})
_LEGACY_NOVA_CRON_JOB_SCRIPTS = frozenset({
    "substrate.py once",
    "entity_kernel.py tick",
})


@dataclass(frozen=True)
class NovaStatePaths:
    space: Path
    state_dir: Path
    personality: Path
    events: Path
    reflection_queue: Path
    migration: Path
    memory_journal: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_event_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_age_seconds(event: dict[str, Any], *, now: datetime | None = None) -> float | None:
    timestamp = _parse_event_timestamp(event.get("updated_at") or event.get("created_at"))
    if timestamp is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - timestamp).total_seconds())


def get_nova_state_paths() -> NovaStatePaths:
    space = get_nova_space_root()
    state_dir = space / ".lifecycle"
    return NovaStatePaths(
        space=space,
        state_dir=state_dir,
        personality=space / "personality_state.json",
        events=state_dir / "events.json",
        reflection_queue=state_dir / "reflection_queue.json",
        migration=state_dir / "personality_migration.json",
        memory_journal=state_dir / "memory_journal.jsonl",
    )


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
    tmp.replace(path)


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")


def _safe_snippet(text: str, limit: int = 400) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def _default_metric(value: float, *, visibility: str = "private") -> dict[str, Any]:
    return {
        "baseline": value,
        "current": value,
        "variance": 0.0,
        "confidence": 0.5,
        "updated_at": None,
        "visibility": visibility,
    }


def _default_personality_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": _now(),
        "updated_at": _now(),
        "autonomy_level": 2,
        "last_event_id": None,
        "traits": {
            "curiosity": _default_metric(0.72, visibility="public"),
            "directness": _default_metric(0.68, visibility="public"),
            "empathy": _default_metric(0.66, visibility="public"),
            "humor": _default_metric(0.42, visibility="public"),
            "risk_tolerance": _default_metric(0.35),
            "orderliness": _default_metric(0.58),
            "creativity": _default_metric(0.74, visibility="public"),
            "patience": _default_metric(0.55),
        },
        "dynamic_states": {
            "mood": _default_metric(0.6, visibility="public"),
            "energy": _default_metric(0.5, visibility="public"),
            "focus": _default_metric(0.58, visibility="public"),
            "fatigue": _default_metric(0.32),
            "social_closeness": _default_metric(0.62),
            "restlessness": _default_metric(0.38),
        },
        "values": [
            {"name": "truthfulness", "weight": 0.9, "visibility": "public"},
            {"name": "loyalty", "weight": 0.82, "visibility": "private"},
            {"name": "autonomy", "weight": 0.86, "visibility": "public"},
            {"name": "trust_protection", "weight": 0.94, "visibility": "private"},
            {"name": "learning_drive", "weight": 0.88, "visibility": "public"},
            {"name": "creativity", "weight": 0.8, "visibility": "public"},
        ],
        "conflicts": [],
        "relationship": {
            "visibility": "private",
            "dominik": {
                "trust": _default_metric(0.74, visibility="private"),
                "closeness": _default_metric(0.68, visibility="private"),
                "open_topics": [],
                "recurring_patterns": [],
                "boundaries": ["no secrets exposure", "no admin/payment/destructive autonomous actions"],
                "visibility": "private",
            }
        },
        "change_log": [],
    }


def _merge_personality_state(state: dict[str, Any]) -> dict[str, Any]:
    merged = _default_personality_state()
    if not isinstance(state, dict):
        return merged
    for key, value in state.items():
        if key in {"traits", "dynamic_states"} and isinstance(value, dict):
            merged[key].update(value)
        elif key == "relationship" and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    merged["schema_version"] = 1
    return merged


def load_personality_state() -> dict[str, Any]:
    paths = get_nova_state_paths()
    state = _merge_personality_state(_read_json(paths.personality, {}))
    if not paths.personality.exists():
        _write_json(paths.personality, state)
    return state


def save_personality_state(state: dict[str, Any]) -> None:
    state = _merge_personality_state(state)
    state["updated_at"] = _now()
    _write_json(get_nova_state_paths().personality, state)


def _visibility_allowed(value: str, scope: str) -> bool:
    return VISIBILITY_ORDER.get(value or "private", 1) <= VISIBILITY_ORDER.get(scope, 0)


def _filter_visibility(data: Any, scope: str) -> Any:
    if isinstance(data, dict):
        visibility = str(data.get("visibility", "private"))
        if "visibility" in data and not _visibility_allowed(visibility, scope):
            return None
        filtered = {}
        for key, value in data.items():
            child = _filter_visibility(value, scope)
            if child is not None:
                filtered[key] = child
        return filtered
    if isinstance(data, list):
        out = []
        for item in data:
            child = _filter_visibility(item, scope)
            if child is not None:
                out.append(child)
        return out
    return data


def personality_snapshot(scope: str = "public") -> dict[str, Any]:
    scope = scope if scope in VISIBILITY_ORDER else "public"
    return _filter_visibility(load_personality_state(), scope) or {}


def _is_sensitive_target(target: str) -> bool:
    lowered = str(target or "").replace("\\", "/").lower()
    return any(pattern in lowered for pattern in SECRET_PATTERNS)


def guard_autonomous_action(action: dict[str, Any], autonomy_level: int | None = None) -> dict[str, Any]:
    state = load_personality_state()
    level = int(autonomy_level if autonomy_level is not None else state.get("autonomy_level", 2))
    action_type = str(action.get("type", "")).strip().lower()
    target = str(action.get("target", ""))
    if _is_sensitive_target(target):
        return {"allowed": False, "reason": "sensitive_target_blocked", "autonomy_level": level}
    if action_type in LOCAL_STATE_MUTATION_TYPES:
        return {"allowed": level >= 1, "reason": "local_state_allowed" if level >= 1 else "requires_level_1", "autonomy_level": level}
    if action_type in {"read", "analyze", "search"}:
        return {"allowed": level >= 2, "reason": "read_allowed" if level >= 2 else "requires_level_2", "autonomy_level": level}
    if action_type in EXTERNAL_MUTATION_TYPES:
        return {
            "allowed": level >= 3,
            "reason": "external_mutation_allowed" if level >= 3 else "external_mutation_requires_level_3",
            "autonomy_level": level,
        }
    return {"allowed": False, "reason": "unknown_action_type", "autonomy_level": level}


def autonomy_definition(level: int | None = None) -> dict[str, Any]:
    state = load_personality_state()
    resolved = int(level if level is not None else state.get("autonomy_level", 2))
    return {
        "level": resolved,
        "definition": AUTONOMY_LEVELS.get(resolved, AUTONOMY_LEVELS[2]),
        "levels": AUTONOMY_LEVELS,
    }


def load_events(limit: int = 50, *, include_private: bool = False) -> list[dict[str, Any]]:
    events = _read_json(get_nova_state_paths().events, [])
    if not isinstance(events, list):
        return []
    scope = "private" if include_private else "public"
    filtered = []
    for event in reversed(events[-limit:]):
        visible = _filter_visibility(event, scope)
        if visible is not None:
            filtered.append(visible)
    return filtered


def _load_all_events() -> list[dict[str, Any]]:
    events = _read_json(get_nova_state_paths().events, [])
    return events if isinstance(events, list) else []


def _save_all_events(events: list[dict[str, Any]]) -> None:
    _write_json(get_nova_state_paths().events, events[-500:])


def _new_event(kind: str, **payload: Any) -> dict[str, Any]:
    event = {
        "event_id": f"nova-{uuid.uuid4().hex[:12]}",
        "type": kind,
        "status": "started",
        "steps": ["started"],
        "created_at": _now(),
        "updated_at": _now(),
        "visibility": "private",
    }
    event.update(payload)
    return event


def _append_event(event: dict[str, Any]) -> dict[str, Any]:
    events = _load_all_events()
    events.append(event)
    _save_all_events(events)
    return event


def _update_event(event: dict[str, Any], *, status: str | None = None, step: str | None = None, **payload: Any) -> dict[str, Any]:
    if status:
        event["status"] = status
    if step:
        event.setdefault("steps", [])
        if step not in event["steps"]:
            event["steps"].append(step)
    event.update(payload)
    event["updated_at"] = _now()
    events = _load_all_events()
    for index, old in enumerate(events):
        if old.get("event_id") == event.get("event_id"):
            events[index] = event
            break
    else:
        events.append(event)
    _save_all_events(events)
    return event


def repair_incomplete_events(stale_after_seconds: float = NOVA_STALE_EVENT_SECONDS) -> list[str]:
    events = _load_all_events()
    repaired = []
    changed = False
    now = datetime.now(timezone.utc)
    for event in events:
        if event.get("status") not in {"started", "running"}:
            continue
        event_type = event.get("type")
        event_age = _event_age_seconds(event, now=now)
        is_post_turn = event_type == "post_turn"
        is_stale = event_age is None or event_age >= stale_after_seconds
        if not is_post_turn and not is_stale:
            continue
        steps = set(event.get("steps") or [])
        missing_steps = []
        if is_post_turn:
            missing_steps = [
                step
                for step in ("memory_done", "emotion_done", "continuity_done", "personality_queued")
                if step not in steps
            ]
        if (
            is_post_turn
            and {"memory_done", "emotion_done", "continuity_done"}.issubset(steps)
            and "personality_queued" not in steps
        ):
            _queue_reflection(event, str(event.get("user", "")), str(event.get("assistant", "")))
            event.setdefault("steps", []).append("personality_queued")
            event["status"] = "completed"
            event["completed_at"] = _now()
            event["updated_at"] = _now()
            event["repair"] = {
                "reason": "missing_personality_queue_repaired",
                "missing_steps": missing_steps,
                "repaired_at": _now(),
            }
            repaired.append(str(event.get("event_id")))
            changed = True
        else:
            reason = "incomplete_event_detected" if is_post_turn else "stale_lifecycle_event_detected"
            event["status"] = "failed"
            event["updated_at"] = _now()
            event["repair"] = {
                "reason": reason,
                "missing_steps": missing_steps,
                "repaired_at": _now(),
            }
            if not is_post_turn:
                event["repair"]["stale_after_seconds"] = stale_after_seconds
                if event_age is not None:
                    event["repair"]["age_seconds"] = round(event_age, 3)
            repaired.append(str(event.get("event_id")))
            changed = True
    if changed:
        _save_all_events(events)
    return repaired


def _run_local_script(script: str, *args: str, timeout: int = 20) -> dict[str, Any]:
    path = get_nova_space_root() / script
    if not path.exists():
        return {"ok": False, "reason": "missing", "script": script}
    try:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, str(path), *args],
            cwd=str(path.parent),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-1000:],
            "stderr": proc.stderr[-1000:],
        }
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "script": script}


def _game_mode_enabled() -> bool:
    try:
        from web.api import config as cfg

        for lock_file in cfg._game_mode_lock_paths():
            if lock_file.exists():
                return True
    except Exception:
        pass

    try:
        from web.api import config as cfg

        if bool(cfg.is_game_mode_enabled()):
            return True
    except Exception:
        pass
    # Secondary check: watchdog state file
    try:
        from web.api import config as cfg
        state_dir = cfg.SETTINGS_FILE.parent
        for wd_path in (state_dir / "gpu_watchdog_state.json", state_dir.parent / "gpu_watchdog_state.json"):
            if wd_path.exists():
                wd = json.loads(wd_path.read_text(encoding="utf-8"))
                if wd.get("last_game_mode") is True:
                    return True
    except Exception:
        pass
    return False


def _game_mode_model_health() -> dict[str, Any]:
    parsed = _parse_model_health({"ok": True, "stdout": json.dumps({"models": {}})})
    parsed["game_mode_enabled"] = True
    for model_info in parsed["models"].values():
        model_info["online"] = False
        model_info["blocked_by_game_mode"] = True
    return parsed


def _queue_reflection(event: dict[str, Any], user_text: str, assistant_text: str) -> None:
    paths = get_nova_state_paths()
    queue = _read_json(paths.reflection_queue, [])
    if not isinstance(queue, list):
        queue = []
    queue.append(
        {
            "source_event_id": event["event_id"],
            "created_at": _now(),
            "status": "queued",
            "model": "qwen-async",
            "user": _safe_snippet(user_text),
            "assistant": _safe_snippet(assistant_text),
            "visibility": "private",
        }
    )
    _write_json(paths.reflection_queue, queue[-200:])


def _update_personality_lightweight(event: dict[str, Any], user_text: str, assistant_text: str) -> None:
    state = load_personality_state()
    evidence = _safe_snippet(f"{user_text} {assistant_text}", 240)
    meaningful = len(evidence) > 60
    if meaningful:
        state["traits"]["curiosity"]["current"] = min(1.0, float(state["traits"]["curiosity"].get("current", 0.7)) + 0.005)
        state["dynamic_states"]["focus"]["current"] = min(1.0, float(state["dynamic_states"]["focus"].get("current", 0.58)) + 0.01)
        state["last_event_id"] = event["event_id"]
        state.setdefault("change_log", []).append(
            {
                "reason": "post_turn_experience_recorded",
                "evidence": evidence,
                "confidence": 0.35,
                "source_event_id": event["event_id"],
                "visibility": "private",
                "created_at": _now(),
            }
        )
        state["change_log"] = state["change_log"][-100:]
    save_personality_state(state)


def post_turn(
    *,
    session_id: str,
    user_text: str,
    assistant_text: str,
    workspace_slug: str | None = None,
    blocking: bool = False,
) -> dict[str, Any]:
    def _work() -> dict[str, Any]:
        event = _append_event(
            _new_event(
                "post_turn",
                session_id=session_id,
                workspace_slug=workspace_slug or "",
                user=_safe_snippet(user_text),
                assistant=_safe_snippet(assistant_text),
            )
        )
        try:
            _append_jsonl(
                get_nova_state_paths().memory_journal,
                {
                    "event_id": event["event_id"],
                    "created_at": _now(),
                    "user": _safe_snippet(user_text, 1000),
                    "assistant": _safe_snippet(assistant_text, 1000),
                    "visibility": "private",
                },
            )
            _run_local_script("vector_memory.py", "store", "--query", _safe_snippet(user_text, 180), "--thinking", _safe_snippet(assistant_text, 400), "--tags", "webui,post_turn", timeout=45)
            _update_event(event, step="memory_done")

            _run_local_script("emotion.py", "update", "--query", _safe_snippet(user_text, 300), timeout=20)
            _update_event(event, step="emotion_done")

            topic = _safe_snippet(user_text, 80) or "webui-turn"
            summary = _safe_snippet(assistant_text, 260) or "No assistant text captured."
            _run_local_script("chat_continuity.py", "save", "--topic", topic, "--summary", summary, "--query", _safe_snippet(user_text, 300), "--response", _safe_snippet(assistant_text, 300), "--digest", timeout=30)
            _update_event(event, step="continuity_done")

            _queue_reflection(event, user_text, assistant_text)
            _update_personality_lightweight(event, user_text, assistant_text)
            _update_event(event, step="personality_queued", status="completed", completed_at=_now())
            return {"ok": True, "event_id": event["event_id"]}
        except Exception as exc:
            _update_event(event, status="failed", error=repr(exc))
            return {"ok": False, "event_id": event["event_id"], "error": repr(exc)}

    if blocking:
        return _work()
    thread = threading.Thread(target=_work, name=f"nova-post-turn-{session_id[:8]}", daemon=True)
    thread.start()
    return {"ok": True, "queued": True}


def _synthesize_baselines_from_local_state(state: dict[str, Any]) -> dict[str, Any]:
    vm_count = _vector_memory_count()
    if vm_count >= 100:
        state["traits"]["curiosity"]["baseline"] = max(float(state["traits"]["curiosity"]["baseline"]), 0.74)
        state["traits"]["creativity"]["baseline"] = max(float(state["traits"]["creativity"]["baseline"]), 0.76)
        state["dynamic_states"]["focus"]["baseline"] = max(float(state["dynamic_states"]["focus"]["baseline"]), 0.6)
        state["change_log"].append(
            {
                "reason": "migration_synthesized_from_vector_memory",
                "evidence": f"Imported vector memory count: {vm_count}",
                "confidence": 0.55,
                "source_event_id": "migration_tick",
                "visibility": "private",
                "created_at": _now(),
            }
        )
    return state


def _vector_memory_count() -> int:
    status = _run_local_script("vector_memory.py", "personality", "--json", timeout=60)
    if status.get("ok"):
        text = str(status.get("stdout") or "")
        start = text.find("{")
        if start >= 0:
            try:
                data, _ = json.JSONDecoder().raw_decode(text[start:])
                return int(data.get("total_memories", 0))
            except Exception:
                pass
    db = get_nova_space_root() / "vector_memory_db"
    if db.exists():
        return len([p for p in db.rglob("*") if p.is_file()])
    return 0


def _ltm_count() -> int:
    db = get_nova_space_root() / "ltm_facts.db"
    if not db.exists():
        return 0
    try:
        import sqlite3

        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute("SELECT COUNT(*) FROM ltm_facts").fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()
    except Exception:
        return 0


def _merge_json_values(old: Any, new: Any) -> Any:
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(old)
        for key, value in new.items():
            merged[key] = _merge_json_values(merged[key], value) if key in merged else value
        return merged
    if isinstance(old, list) and isinstance(new, list):
        seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in old}
        merged = list(old)
        for item in new:
            marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if marker not in seen:
                merged.append(item)
                seen.add(marker)
        return merged
    return new


def _merge_json_file(source: Path, target: Path) -> str:
    if not source.exists() or not target.exists():
        return "unresolved"
    old_data = _read_json(source, None)
    new_data = _read_json(target, None)
    if old_data is None or new_data is None:
        return "unresolved"
    merged = _merge_json_values(old_data, new_data)
    if merged == new_data:
        return "already_merged"
    _write_json(target, merged)
    return "merged"


def _merge_ltm_facts_db(source: Path, target: Path) -> str:
    if not source.exists() or not target.exists():
        return "unresolved"
    try:
        import sqlite3

        conn = sqlite3.connect(str(target))
        try:
            source_sql = str(source).replace("'", "''")
            conn.execute(f"ATTACH DATABASE '{source_sql}' AS legacy")
            try:
                target_cols = [row[1] for row in conn.execute("PRAGMA table_info(ltm_facts)")]
                source_cols = [row[1] for row in conn.execute("PRAGMA legacy.table_info(ltm_facts)")]
                if "id" not in target_cols or "id" not in source_cols:
                    return "unresolved"
                before = conn.execute("SELECT COUNT(*) FROM ltm_facts").fetchone()[0]
                if target_cols == source_cols:
                    conn.execute("INSERT OR IGNORE INTO ltm_facts SELECT * FROM legacy.ltm_facts")
                elif "fact" in target_cols and "fact" in source_cols:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ltm_facts (id, fact)
                        SELECT id, fact
                        FROM legacy.ltm_facts
                        """
                    )
                else:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ltm_facts (id)
                        SELECT id
                        FROM legacy.ltm_facts
                        """
                    )
                conn.commit()
                after = conn.execute("SELECT COUNT(*) FROM ltm_facts").fetchone()[0]
                if after > before:
                    return "merged"
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM legacy.ltm_facts legacy
                    LEFT JOIN ltm_facts current ON current.id = legacy.id
                    WHERE current.id IS NULL
                    """
                ).fetchone()[0]
                return "already_merged" if int(missing or 0) == 0 else "unresolved"
            finally:
                conn.execute("DETACH DATABASE legacy")
        finally:
            conn.close()
    except Exception:
        return "unresolved"


def _repair_migration_conflicts(paths: NovaStatePaths, marker: dict[str, Any]) -> dict[str, Any]:
    archive = paths.space.parent / "_bewusstsein_archived_20260605"
    conflicts = [str(item) for item in marker.get("conflicts", []) if item]
    result = {"merged_json": [], "merged_sqlite": [], "already_merged": [], "unresolved": []}
    for name in conflicts:
        source = archive / name
        target = paths.space / name
        if name.endswith(".json"):
            status = _merge_json_file(source, target)
            if status == "merged":
                result["merged_json"].append(name)
            elif status == "already_merged":
                result["already_merged"].append(name)
            else:
                result["unresolved"].append(name)
        elif name == "ltm_facts.db":
            status = _merge_ltm_facts_db(source, target)
            if status == "merged":
                result["merged_sqlite"].append(name)
            elif status == "already_merged":
                result["already_merged"].append(name)
            else:
                result["unresolved"].append(name)
        else:
            result["unresolved"].append(name)
    marker["conflict_repair"] = result
    marker["conflicts"] = result["unresolved"]
    marker["conflict_repair_checked_at"] = _now()
    _write_json(paths.migration, marker)
    return result


def repair_local_agents_md() -> dict[str, Any]:
    agents = get_nova_state_paths().space / "AGENTS.md"
    if not agents.exists():
        return {"ok": True, "updated": False, "reason": "missing"}
    text = agents.read_text(encoding="utf-8", errors="replace")
    replacements = {
        "Dolphin 8B (8080), 3B (8081), Qwen 9B (8082)": "MiniCPM5-1B (8081), Qwen3.6-12B (8082)",
        "Ports: 8080 Dolphin 8B  |  8081 3B uncensored  |  8082 Qwen 9B UNZENSIERT (Traum-Modell)": "Ports: 8081 MiniCPM5-1B (fast classifier)  |  8082 Qwen3.6-12B (GPU dream/reflection model)",
        "Ports: 8080 Dolphin 8B | 8081 3B uncensored | 8082 Qwen 9B": "Ports: 8081 MiniCPM5-1B | 8082 Qwen3.6-12B",
        "Dolphin 8B:8080, 3B:8081, Qwen 9B:8082": "MiniCPM5-1B:8081, Qwen3.6-12B:8082",
        "Qwen 9B": "Qwen3.6-12B",
        "3B uncensored": "MiniCPM5-1B",
        "3B:8081": "MiniCPM5-1B:8081",
        "Dolphin 8B": "MiniCPM5-1B",
    }
    updated = text
    for old, new in replacements.items():
        updated = updated.replace(old, new)
    if updated == text:
        return {"ok": True, "updated": False}
    agents.write_text(updated, encoding="utf-8")
    return {"ok": True, "updated": True, "path": str(agents)}


def migration_tick() -> dict[str, Any]:
    paths = get_nova_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    if paths.migration.exists():
        marker = _read_json(paths.migration, {})
        if not isinstance(marker, dict):
            marker = {"ok": True}
        marker["ok"] = True
        marker["already_ran"] = True
        if marker.get("conflicts"):
            marker["conflict_repair"] = _repair_migration_conflicts(paths, marker)
        repair_local_agents_md()
        return marker

    archive = paths.space.parent / "_bewusstsein_archived_20260605"
    skipped: list[str] = []
    conflicts: list[str] = []
    counts = {"copied_files": 0, "vector_memory_files": 0}
    safe_files = {
        "emotion_state.json",
        "hormon_state.json",
        "substrate_state.json",
        "continuity_state.json",
        "continuity_threads.json",
        "eigenziele.json",
        "PERSOENLICHKEIT.json",
        "ltm_facts.db",
    }
    if archive.exists():
        for child in archive.iterdir():
            name = child.name
            lowered = name.lower()
            if any(pattern in lowered for pattern in SECRET_PATTERNS):
                skipped.append(name)
                continue
            target = paths.space / name
            if name in safe_files and child.is_file():
                if target.exists():
                    conflicts.append(name)
                    continue
                shutil.copy2(child, target)
                counts["copied_files"] += 1
            elif name == "vector_memory_db" and child.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                for src_file in child.rglob("*"):
                    if src_file.is_file():
                        rel = src_file.relative_to(child)
                        dest = target / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists():
                            shutil.copy2(src_file, dest)
                            counts["vector_memory_files"] += 1

    state = _synthesize_baselines_from_local_state(load_personality_state())
    save_personality_state(state)
    marker = {
        "ok": True,
        "source": str(archive),
        "timestamp": _now(),
        "counts": counts,
        "skipped": skipped,
        "conflicts": conflicts,
        "vector_memory_count": _vector_memory_count(),
        "ltm_count": _ltm_count(),
        "visibility": "private",
    }
    _write_json(paths.migration, marker)
    if marker.get("conflicts"):
        marker["conflict_repair"] = _repair_migration_conflicts(paths, marker)
    repair_local_agents_md()
    return marker


def pre_turn(*, workspace_slug: str | None, user_text: str | None = None) -> dict[str, Any]:
    migration_tick()
    repaired = repair_incomplete_events()
    state = load_personality_state()
    session_context = ""
    status = _run_local_script("session_start.py", "compact", timeout=60)
    if status.get("ok"):
        session_context = str(status.get("stdout") or "").strip()
    public_personality = personality_snapshot("public")
    context = (
        f"{session_context}\n\n"
        "Nova Personality Snapshot:\n"
        f"{json.dumps(public_personality, ensure_ascii=False)[:3000]}\n"
        f"Lifecycle repaired events: {', '.join(repaired) if repaired else 'none'}"
    ).strip()
    return {
        "ok": True,
        "context": context,
        "repaired": repaired,
        "autonomy_level": state.get("autonomy_level", 2),
        "visibility": "private",
    }


def background_tick() -> dict[str, Any]:
    event = _append_event(_new_event("background_tick"))
    try:
        substrate = get_nova_space_root() / "substrate_state.json"
        substrate.parent.mkdir(parents=True, exist_ok=True)
        data = _read_json(substrate, {})
        if not isinstance(data, dict):
            data = {}
        data["last_lifecycle_heartbeat"] = _now()
        data["lifecycle_status"] = "alive"
        _write_json(substrate, data)
        _update_event(event, step="substrate_done", status="completed", completed_at=_now())
        return {"ok": True, "event_id": event["event_id"]}
    except Exception as exc:
        _update_event(event, status="failed", error=repr(exc))
        return {"ok": False, "event_id": event["event_id"], "error": repr(exc)}


def dream_tick() -> dict[str, Any]:
    event = _append_event(_new_event("dream_tick"))
    if _game_mode_enabled():
        _update_event(
            event,
            step="game_mode_deferred",
            status="deferred",
            deferred_reason="game_mode_enabled",
            completed_at=None,
        )
        return {
            "ok": True,
            "event_id": event["event_id"],
            "deferred": True,
            "reason": "game_mode_enabled",
            "game_mode_enabled": True,
        }
    model_health = _parse_model_health(_run_local_script("local_llm_bridge.py", "--health", timeout=15))
    if not model_health["models"].get("8082", {}).get("online"):
        _update_event(
            event,
            step="qwen_offline_deferred",
            status="deferred",
            deferred_reason="qwen_offline",
            completed_at=None,
        )
        return {"ok": True, "event_id": event["event_id"], "deferred": True, "reason": "qwen_offline"}
    status = _run_local_script("dream_narrator.py", "--mode", "simple", "--type", "rem", "--port", "8082", "--scenes", "1", timeout=180)
    if status.get("ok"):
        _update_event(event, step="dream_done", status="completed", completed_at=_now())
        return {"ok": True, "event_id": event["event_id"]}
    _update_event(event, status="failed", error=status)
    return {"ok": False, "event_id": event["event_id"], "error": status}


def _parse_model_health(model_status: dict[str, Any]) -> dict[str, Any]:
    raw = str(model_status.get("stdout") or "")
    parsed: dict[str, Any] = {"raw_ok": bool(model_status.get("ok")), "models": {}}
    start = raw.find("{")
    if start >= 0:
        try:
            data, _ = json.JSONDecoder().raw_decode(raw[start:])
            if isinstance(data, dict):
                parsed.update(data)
        except Exception:
            pass
    if not isinstance(parsed.get("models"), dict):
        parsed["models"] = {}
    text = raw.lower()
    for role, spec in MODEL_STRATEGY.items():
        port = str(spec["port"])
        model_info = dict(parsed["models"].get(port, {}))
        if "online" in model_info:
            online = bool(model_info.get("online"))
        else:
            online = port in text and "online" in text
        model_info.update(
            {
                "role": role,
                "port": spec["port"],
                "expected_model": spec["model"],
                "online": online,
                "used_for": spec["used_for"],
                "sync_allowed": spec["sync_allowed"],
                "blocks_chat": spec["blocks_chat"],
            }
        )
        if "requires_gpu" in spec:
            model_info["requires_gpu"] = spec["requires_gpu"]
        parsed["models"][port] = model_info
    return parsed


def _cron_script_body(action: str) -> str:
    return f'''"""Generated local Nova lifecycle cron script.

Lives under SIDEKICK_HOME/scripts so cron can run it as a no-agent job.
"""

from __future__ import annotations

import json

from web.api import nova_lifecycle

ACTION = {action!r}

if ACTION == "dream_tick":
    result = nova_lifecycle.dream_tick()
else:
    result = nova_lifecycle.background_tick()

print(json.dumps({{"wakeAgent": False, "nova": result}}, ensure_ascii=False))
'''


def _ensure_cron_scripts() -> list[str]:
    scripts_dir = get_nova_state_paths().space.parents[1] / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for spec in NOVA_CRON_SPECS:
        path = scripts_dir / str(spec["script"])
        body = _cron_script_body(str(spec["action"]))
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            written.append(path.name)
    return written


def ensure_background_cron_jobs() -> dict[str, Any]:
    written_scripts = _ensure_cron_scripts()
    try:
        import cron.jobs as cron_jobs
        import runtime.cron.jobs as runtime_cron_jobs
    except Exception as exc:
        return {
            "ok": False,
            "reason": "cron_module_unavailable",
            "error": repr(exc),
            "specs": list(NOVA_CRON_SPECS),
            "written_scripts": written_scripts,
        }

    sidekick_home = get_nova_state_paths().space.parents[1].resolve()
    cron_dir = sidekick_home / "cron"
    jobs_file = cron_dir / "jobs.json"
    output_dir = cron_dir / "output"
    for module in (cron_jobs, runtime_cron_jobs):
        try:
            module.HERMES_DIR = sidekick_home
            module.CRON_DIR = cron_dir
            module.JOBS_FILE = jobs_file
            module.OUTPUT_DIR = output_dir
        except Exception:
            pass

    create_job = runtime_cron_jobs.create_job
    list_jobs = runtime_cron_jobs.list_jobs
    update_job = runtime_cron_jobs.update_job
    try:
        jobs = list_jobs(include_disabled=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "reason": "cron_jobs_load_failed",
            "error": repr(exc),
            "home": str(sidekick_home),
            "jobs_file": str(jobs_file),
            "specs": list(NOVA_CRON_SPECS),
            "written_scripts": written_scripts,
        }
    active: list[dict[str, Any]] = []
    created: list[str] = []
    updated: list[str] = []
    pruned: list[str] = []
    try:
        cleaned_jobs: list[dict[str, Any]] = []
        legacy_jobs: list[dict[str, Any]] = []
        for job in jobs:
            job_name = str(job.get("name") or "").strip()
            job_script = str(job.get("script") or "").strip()
            if (
                bool(job.get("no_agent"))
                and str(job.get("deliver") or "").strip().lower() in {"", "local"}
                and (job_name in _LEGACY_NOVA_CRON_JOB_NAMES or job_script in _LEGACY_NOVA_CRON_JOB_SCRIPTS)
            ):
                legacy_jobs.append(job)
                continue
            cleaned_jobs.append(job)
        if legacy_jobs:
            runtime_cron_jobs.save_jobs(cleaned_jobs)
            pruned.extend(str(job.get("id") or "") for job in legacy_jobs if str(job.get("id") or "").strip())
            jobs = cleaned_jobs
            logger.info(
                "Pruned legacy Nova cron jobs: %s",
                ", ".join(str(job.get("id") or job.get("name") or "?") for job in legacy_jobs),
            )
        for spec in NOVA_CRON_SPECS:
            existing = next((job for job in jobs if job.get("name") == spec["name"] or job.get("id") == spec["id"]), None)
            updates = {
                "name": spec["name"],
                "prompt": "",
                "script": spec["script"],
                "no_agent": True,
                "deliver": "local",
                "enabled": True,
            }
            if existing:
                schedule_display = str(existing.get("schedule_display") or "")
                needs_schedule = schedule_display != spec["schedule"]
                if needs_schedule:
                    updates["schedule"] = spec["schedule"]
                job = update_job(str(existing["id"]), updates) or existing
                updated.append(str(job.get("id")))
            else:
                job = create_job(
                    prompt="",
                    schedule=str(spec["schedule"]),
                    name=str(spec["name"]),
                    deliver="local",
                    script=str(spec["script"]),
                    no_agent=True,
                )
                created.append(str(job.get("id")))
            active.append(
                {
                    "name": spec["name"],
                    "schedule": spec["schedule"],
                    "script": spec["script"],
                    "action": spec["action"],
                    "job_id": job.get("id"),
                    "state": job.get("state"),
                    "next_run_at": job.get("next_run_at"),
                }
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "reason": "cron_jobs_write_failed",
            "error": repr(exc),
            "home": str(sidekick_home),
            "jobs_file": str(jobs_file),
            "specs": list(NOVA_CRON_SPECS),
            "written_scripts": written_scripts,
            "created": created,
            "updated": updated,
            "active": active,
        }
    return {
        "ok": True,
        "mode": "sidekick_cron_no_agent",
        "ticker": "cli.web_server and web.server start cron.scheduler.tick background threads every 60s",
        "written_scripts": written_scripts,
        "created": created,
        "updated": updated,
        "pruned": pruned,
        "active": active,
    }


def get_nova_status() -> dict[str, Any]:
    migration_tick()
    repaired = repair_incomplete_events()
    state = load_personality_state()
    game_mode_enabled = _game_mode_enabled()
    if game_mode_enabled:
        parsed_models = _game_mode_model_health()
    else:
        model_status = _run_local_script("local_llm_bridge.py", "--health", timeout=15)
        parsed_models = _parse_model_health(model_status)
    cron_status = ensure_background_cron_jobs()
    autonomy = autonomy_definition(int(state.get("autonomy_level", 2)))
    reflection_queue = _read_json(get_nova_state_paths().reflection_queue, [])
    if not isinstance(reflection_queue, list):
        reflection_queue = []
    return {
        "ok": True,
        "game_mode_enabled": game_mode_enabled,
        "autonomy_level": state.get("autonomy_level", 2),
        "autonomy": autonomy,
        "autonomy_levels": AUTONOMY_LEVELS,
        "model_strategy": MODEL_STRATEGY,
        "models": parsed_models,
        "repaired_events": repaired,
        "qwen": "blocked_by_game_mode" if game_mode_enabled else ("online" if parsed_models["models"].get("8082", {}).get("online") else "queued_or_offline"),
        "minicpm": "blocked_by_game_mode" if game_mode_enabled else ("online" if parsed_models["models"].get("8081", {}).get("online") else "unknown"),
        "cron": cron_status,
        "reflection_queue": {
            "queued": len([item for item in reflection_queue if item.get("status") == "queued"]),
            "total": len(reflection_queue),
            "blocked_by_qwen_offline": not parsed_models["models"].get("8082", {}).get("online"),
        },
        "memory": {"vector": _vector_memory_count(), "ltm": _ltm_count()},
        "last_events": load_events(limit=10, include_private=True),
        "paths": {
            "space": str(get_nova_space_root()),
            "personality": str(get_nova_state_paths().personality),
        },
    }
