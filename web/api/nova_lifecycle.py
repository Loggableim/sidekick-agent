"""Nova personality lifecycle.

Repo code owns orchestration, schemas, guards, and API-safe views. Nova's
private evolving state stays under SIDEKICK_HOME/spaces/nova and is never a
repository asset.
"""

from __future__ import annotations

import json
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

VISIBILITY_ORDER = {"public": 0, "private": 1, "sensitive": 2}
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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


def repair_incomplete_events() -> list[str]:
    events = _load_all_events()
    repaired = []
    changed = False
    for event in events:
        if event.get("status") == "started":
            event["status"] = "failed"
            event["updated_at"] = _now()
            event["repair"] = {
                "reason": "incomplete_event_detected",
                "repaired_at": _now(),
            }
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


def migration_tick() -> dict[str, Any]:
    paths = get_nova_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    if paths.migration.exists():
        return {"ok": True, "already_ran": True}

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
    status = _run_local_script("dream_narrator.py", "--mode", "simple", "--type", "rem", "--port", "8082", "--scenes", "1", timeout=180)
    if status.get("ok"):
        _update_event(event, step="dream_done", status="completed", completed_at=_now())
        return {"ok": True, "event_id": event["event_id"]}
    _update_event(event, status="failed", error=status)
    return {"ok": False, "event_id": event["event_id"], "error": status}


def get_nova_status() -> dict[str, Any]:
    migration_tick()
    state = load_personality_state()
    model_status = _run_local_script("local_llm_bridge.py", "--health", timeout=15)
    return {
        "ok": True,
        "autonomy_level": state.get("autonomy_level", 2),
        "qwen": "online" if "8082" in str(model_status.get("stdout", "")) and "online" in str(model_status.get("stdout", "")) else "unknown",
        "memory": {"vector": _vector_memory_count(), "ltm": _ltm_count()},
        "last_events": load_events(limit=10, include_private=True),
        "paths": {
            "space": str(get_nova_space_root()),
            "personality": str(get_nova_state_paths().personality),
        },
    }
