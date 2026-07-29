"""Strictly read-only public projection for the canonical Nova Space.

This module intentionally does not use ``EntityStateStore``, the Nova
lifecycle/status helpers, or the managed-Space governance resolver.  Those
components may migrate, repair, initialise a store, or otherwise write while
answering a read.  Presence cards must remain safe to fetch on every page
load, so this reader only opens already-existing files and SQLite databases
in read-only mode and returns a deliberately small public allowlist.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


_NOVA_SLUG = "nova"
_MAX_SPACES = 12
# Each enrolled Space receives one current admission/event projection.  The
# response is still bounded, while a noisy Space cannot hide another one.
_MAX_ACTIVITY = _MAX_SPACES
_MAX_RESULTS = _MAX_SPACES
_LATEST_ADMISSION_SQL = """
    SELECT admission_id, target_key, state, updated_at
    FROM supervisor_admissions
    WHERE target_key = ?
    ORDER BY updated_at DESC
    LIMIT 1
"""
_TERMINAL_ADMISSION_STATES = ("completed", "cancelled", "abandoned")
_LATEST_TERMINAL_ADMISSION_SQL = """
    SELECT admission_id, target_key
    FROM supervisor_admissions
    WHERE target_key = ?
      AND state IN (?, ?, ?)
    ORDER BY updated_at DESC
    LIMIT 1
"""
_LATEST_AUDIT_SQL = """
    SELECT event_type, created_at, sequence
    FROM supervisor_audit
    WHERE admission_id = ?
    ORDER BY sequence DESC
    LIMIT 1
"""
_REQUIRED_LEDGER_INDEX_SQL = {
    "idx_supervisor_admissions_target_updated": (
        "createindexidx_supervisor_admissions_target_updated"
        "onsupervisor_admissions(target_key,updated_atdesc)"
    ),
    "idx_supervisor_audit_admission_sequence": (
        "createindexidx_supervisor_audit_admission_sequence"
        "onsupervisor_audit(admission_id,sequencedesc)"
    ),
}
_PUBLIC_PRESENCE_STATES = frozenset(
    {"sleeping", "available", "listening", "thinking", "speaking", "do_not_disturb"}
)
_PUBLIC_RUN_STATES = frozenset(
    {
        "provisioning",
        "active",
        "paused",
        "cancelling",
        "cancelled",
        "abandoning",
        "abandoned",
        "completed",
    }
)
_PUBLIC_ACTIVITY_KINDS = {
    "provisioning": "provisioning",
    "admitted": "admitted",
    "paused": "paused",
    "completed": "completed",
    "reconciled_completed": "reconciled_completed",
    "reconciled_completed_during_action_gate": "completed",
    "reconciled_completed_after_human_terminal": "completed",
    "reconciled_completed_during_pause": "completed",
    "cancelled": "cancelled",
    "abandoned": "abandoned",
    "cancelling": "cancelling",
    "abandoning": "abandoning",
}
_RESULT_BY_EVENT = {
    "completed": "completed",
    "reconciled_completed": "completed",
    "reconciled_completed_during_action_gate": "completed",
    "reconciled_completed_after_human_terminal": "completed",
    "reconciled_completed_during_pause": "completed",
    "cancelled": "cancelled",
    "abandoned": "abandoned",
}
_BLOCKER_CODES = {
    "paused": "supervisor_paused",
    "abandoned": "supervisor_abandoned",
    "abandoning": "supervisor_abandoning",
}
_SPACE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_TIMESTAMP_RE = re.compile(r"[0-9T:+.\-Z]{1,40}")

# The status route deliberately keeps this metadata local to the read-only
# projection.  Importing the lifecycle module would make the GET route depend
# on helpers that may migrate state, repair events, create cron jobs, or probe
# a model.  Update this snapshot alongside the lifecycle contract when Nova's
# public status vocabulary changes.
_AUTONOMY_LEVELS: dict[int, dict[str, Any]] = {
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
_MODEL_STRATEGY: dict[str, dict[str, Any]] = {
    "fast_classifier": {
        "model": "MiniCPM5-1B",
        "port": 8081,
        "used_for": ["event_classification", "lightweight_emotion_updates", "fast_state_labels"],
        "sync_allowed": True,
        "blocks_chat": False,
    },
    "deep_reflection": {
        "model": "Gemma 4 26B-A4B",
        "port": 8082,
        "used_for": ["dream_tick", "async_reflection", "personality_synthesis"],
        "sync_allowed": False,
        "requires_gpu": True,
        "blocks_chat": False,
    },
    "fallback_mind": {
        "model": "Qwen3.6-12B",
        "port": 8083,
        "used_for": ["fallback_thought", "fallback_dream"],
        "sync_allowed": False,
        "requires_gpu": True,
        "blocks_chat": False,
    },
}


def build_presence_status(*, home: Path | None = None) -> dict[str, Any]:
    """Read Nova's voice-presence projection without opening a state store."""
    resolved_home = _read_only_home(home)
    spaces_root = _read_only_spaces_root(resolved_home, explicit_home=home is not None)
    nova_root = _read_only_nova_root(resolved_home, spaces_root, explicit_home=home is not None)
    state = _read_json(nova_root / "nova_data" / "entity" / "entity_state.json")
    dynamic = _mapping(state.get("dynamic"))
    return {
        "presence": _public_presence_state(state),
        "voice_cycle": copy.deepcopy(dynamic.get("voice_cycle")),
        "updated_at": dynamic.get("presence_updated_at"),
    }


def build_status_projection(*, home: Path | None = None) -> dict[str, Any]:
    """Return Nova's legacy-compatible status shape without runtime work.

    This projection never imports the lifecycle, model, cron, or entity-store
    layers.  It intentionally reports model/process health as unchecked rather
    than turning a dashboard poll into a mutation, subprocess, or model call.
    """
    resolved_home = _read_only_home(home)
    spaces_root = _read_only_spaces_root(resolved_home, explicit_home=home is not None)
    nova_root = _read_only_nova_root(resolved_home, spaces_root, explicit_home=home is not None)
    entity_state = _read_json(nova_root / "nova_data" / "entity" / "entity_state.json")
    legacy_state = _read_json(nova_root / "personality_state.json")
    runtime = _mapping(entity_state.get("runtime"))
    autonomy_level = _autonomy_level(runtime.get("autonomy_level", legacy_state.get("autonomy_level")))
    game_mode_enabled = _read_game_mode_enabled(resolved_home)
    reflection_queue = _read_json_value(nova_root / ".lifecycle" / "reflection_queue.json", [])
    reflection_items = reflection_queue if isinstance(reflection_queue, list) else []
    reflection_summary = {
        "queued": sum(
            1
            for item in reflection_items
            if isinstance(item, Mapping) and item.get("status", "queued") == "queued"
        ),
        "total": len(reflection_items),
        # A read-only status must not infer live availability from stale files.
        "blocked_by_qwen_offline": None,
    }
    models = _unchecked_model_status(game_mode_enabled)
    yolo_state = _read_json(nova_root / ".lifecycle" / "yolo.json")
    yolo_enabled = yolo_state.get("enabled") is True
    presence = _public_presence_state(entity_state)
    recent_events = _read_recent_events(nova_root / ".lifecycle" / "events.json")
    entity_runtime = _entity_runtime_projection(
        nova_root,
        entity_state,
        runtime,
        presence=presence,
        autonomy_level=autonomy_level,
        yolo_enabled=yolo_enabled,
        game_mode_enabled=game_mode_enabled,
        reflection_summary=reflection_summary,
    )
    return {
        "ok": True,
        "game_mode_enabled": game_mode_enabled,
        "autonomy_level": autonomy_level,
        "autonomy": {
            "level": autonomy_level,
            "definition": copy.deepcopy(_AUTONOMY_LEVELS[autonomy_level]),
            "levels": copy.deepcopy(_AUTONOMY_LEVELS),
        },
        "autonomy_levels": copy.deepcopy(_AUTONOMY_LEVELS),
        "model_strategy": copy.deepcopy(_MODEL_STRATEGY),
        "model_registry": _read_model_registry(nova_root),
        "models": models,
        "repaired_events": [],
        "qwen": "blocked_by_game_mode" if game_mode_enabled else "not_checked",
        "minicpm": "blocked_by_game_mode" if game_mode_enabled else "not_checked",
        "cron": {"ok": True, "mode": "read_only", "checked": False},
        "reflection_queue": reflection_summary,
        "memory": {
            "vector": _read_vector_memory_count(nova_root),
            "ltm": _read_ltm_count(nova_root / "ltm_facts.db"),
        },
        "last_events": recent_events,
        "entity_runtime": entity_runtime,
        "paths": {
            "space": str(nova_root),
            "personality": str(nova_root / "personality_state.json"),
        },
    }


def build_presence_card(*, home: Path | None = None) -> dict[str, Any]:
    """Return only bounded public Nova-presence data without modifying disk.

    ``home`` is injectable for tests.  Runtime calls resolve it directly from
    ``SIDEKICK_HOME`` rather than through a lifecycle/bootstrap helper.
    """
    resolved_home = _read_only_home(home)
    spaces_root = _read_only_spaces_root(resolved_home, explicit_home=home is not None)
    nova_root = _read_only_nova_root(resolved_home, spaces_root, explicit_home=home is not None)
    entity_state = _read_json(nova_root / "nova_data" / "entity" / "entity_state.json")
    presence = _public_presence_state(entity_state)
    managed_spaces = _managed_space_summaries(spaces_root)
    ledger_path = resolved_home / "state" / "nova-space-supervisor.sqlite"
    admissions = _read_supervisor_admissions(ledger_path, managed_spaces)
    admission_by_space = _latest_admission_by_space(admissions, managed_spaces)
    for summary in managed_spaces:
        summary["state"] = admission_by_space.get(summary["space"], {}).get("state", "idle")

    focus = _focus_for(admissions, managed_spaces, presence)
    events = _read_latest_supervisor_events(ledger_path, admissions)
    activity = _activity_for(events)
    results = _audited_results_for(_read_latest_terminal_events(ledger_path, managed_spaces))
    blockers = _blockers_for(managed_spaces)
    return {
        "identity": {
            "name": "Nova",
            "voice": "direct, curious, accountable",
        },
        "state": presence,
        "focus": focus,
        "managed_spaces": managed_spaces,
        "audited_results": results,
        "blockers": blockers,
        "activity": activity,
    }


def _read_only_home(home: Path | None) -> Path:
    if home is not None:
        return Path(home).expanduser().resolve()
    configured = os.getenv("SIDEKICK_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".sidekick").resolve()


def _read_only_spaces_root(home: Path, *, explicit_home: bool) -> Path:
    if not explicit_home:
        configured = os.getenv("SIDEKICK_WEBUI_SPACES_DIR", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
    return home / "spaces"


def _read_only_nova_root(home: Path, spaces_root: Path, *, explicit_home: bool) -> Path:
    if not explicit_home:
        configured = os.getenv("SIDEKICK_NOVA_SPACE", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
    del home
    return spaces_root / _NOVA_SLUG


def _read_json_value(path: Path, default: Any) -> Any:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default
    return loaded


def _read_json(path: Path) -> Mapping[str, Any]:
    loaded = _read_json_value(path, {})
    return loaded if isinstance(loaded, Mapping) else {}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _autonomy_level(value: object) -> int:
    if isinstance(value, bool):
        return 2
    try:
        level = int(value)
    except (TypeError, ValueError):
        return 2
    return level if level in _AUTONOMY_LEVELS else 2


def _read_game_mode_enabled(home: Path) -> bool:
    state_root = home / "state"
    webui_state = state_root / "webui"
    if (webui_state / "game_mode.lock").is_file() or (state_root / "game_mode.lock").is_file():
        return True
    settings = _read_json(webui_state / "settings.json")
    return settings.get("game_mode_enabled") is True


def _read_model_registry(nova_root: Path) -> dict[str, Any]:
    payload = _read_json(nova_root / "model_registry.json")
    if not isinstance(payload.get("models"), Mapping):
        return {"version": 0, "models": {}}
    return copy.deepcopy(dict(payload))


def _unchecked_model_status(game_mode_enabled: bool) -> dict[str, Any]:
    models: dict[str, dict[str, Any]] = {}
    for role, spec in _MODEL_STRATEGY.items():
        model = {
            "role": role,
            "port": spec["port"],
            "expected_model": spec["model"],
            "online": False,
            "used_for": list(spec["used_for"]),
            "sync_allowed": spec["sync_allowed"],
            "blocks_chat": spec["blocks_chat"],
            "checked": False,
        }
        if "requires_gpu" in spec:
            model["requires_gpu"] = spec["requires_gpu"]
        if game_mode_enabled:
            model["blocked_by_game_mode"] = True
        models[str(spec["port"])] = model
    return {"raw_ok": False, "checked": False, "models": models}


def _read_recent_events(path: Path) -> list[dict[str, Any]]:
    events = _read_json_value(path, [])
    if not isinstance(events, list):
        return []
    return [
        copy.deepcopy(dict(event))
        for event in reversed(events[-10:])
        if isinstance(event, Mapping)
    ]


def _read_vector_memory_count(nova_root: Path) -> int:
    directory = nova_root / "vector_memory_db"
    try:
        return sum(1 for path in directory.rglob("*") if path.is_file()) if directory.is_dir() else 0
    except OSError:
        return 0


def _read_ltm_count(path: Path) -> int:
    if not path.is_file():
        return 0
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        row = connection.execute("SELECT COUNT(*) FROM ltm_facts").fetchone()
        return int(row[0] if row else 0)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return 0
    finally:
        if connection is not None:
            connection.close()


def _entity_runtime_projection(
    nova_root: Path,
    entity_state: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    presence: str,
    autonomy_level: int,
    yolo_enabled: bool,
    game_mode_enabled: bool,
    reflection_summary: Mapping[str, Any],
) -> dict[str, Any]:
    pid_state = _read_json(nova_root / "nova_data" / "runtime" / "nova_mind.pid.json")
    pid = pid_state.get("pid") if isinstance(pid_state.get("pid"), int) else None
    return {
        "ok": True,
        "schema_version": entity_state.get("schema_version"),
        "state_revision": entity_state.get("revision"),
        "presence": presence,
        "autonomy": {"level": autonomy_level, "yolo_enabled": yolo_enabled},
        "mind": {
            "pid": pid,
            "running": None,
            "checked": False,
            "model_route": "ollama-cloud:deepseek-v4-flash" if game_mode_enabled else "local-registry",
            "game_mode": game_mode_enabled,
        },
        "reflection_queue": {
            "queued": int(reflection_summary.get("queued", 0) or 0),
            "total": int(reflection_summary.get("total", 0) or 0),
            "path": str(nova_root / ".lifecycle" / "reflection_queue.json"),
        },
        "soak": copy.deepcopy(_read_json(nova_root / "nova_data" / "entity" / "soak_v2.json")),
        "last_event_id": runtime.get("last_event_id"),
        "last_intent_id": runtime.get("last_intent_id"),
        "last_outcome_id": runtime.get("last_outcome_id"),
        "last_reflection_at": runtime.get("last_reflection_at"),
        "paths": {
            "state": str(nova_root / "nova_data" / "entity" / "entity_state.json"),
            "events": str(nova_root / "nova_data" / "entity" / "autobiography.db"),
        },
    }


def _public_presence_state(entity_state: Mapping[str, Any]) -> str:
    dynamic = entity_state.get("dynamic")
    value = dynamic.get("presence") if isinstance(dynamic, Mapping) else None
    state = str(value or "").strip().lower()
    return state if state in _PUBLIC_PRESENCE_STATES else "available"


def _managed_space_summaries(spaces_root: Path) -> list[dict[str, Any]]:
    try:
        children = sorted(spaces_root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []
    summaries: list[dict[str, Any]] = []
    for child in children:
        slug = child.name.lower()
        if not child.is_dir() or slug == _NOVA_SLUG or not _SPACE_SLUG_RE.fullmatch(slug):
            continue
        config = _read_space_config(child / "space.yaml")
        management = config.get("nova_management") if isinstance(config, Mapping) else None
        if not _is_enrolled_yolo_management(management):
            continue
        summaries.append(
            {
                "space": slug,
                # The public projection intentionally derives its display name
                # from the stable slug instead of returning arbitrary config text.
                "name": _display_name(slug),
                "governance_revision": int(management["revision"]),
                "state": "idle",
            }
        )
        if len(summaries) >= _MAX_SPACES:
            break
    return summaries


def _read_space_config(path: Path) -> Mapping[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def _is_enrolled_yolo_management(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"yolo", "enrolled", "revision"}:
        return False
    return (
        value.get("yolo") is True
        and value.get("enrolled") is True
        and type(value.get("revision")) is int
        and int(value["revision"]) >= 0
    )


def _display_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _managed_space_keys(managed_spaces: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                space
                for item in managed_spaces
                if (space := _safe_space(item.get("space")))
            }
        )
    )


def _open_read_only_ledger(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        # An older ledger must be migrated on the normal supervisor write path.
        # Presence deliberately returns no ledger projection rather than issuing
        # an unbounded history read or applying DDL from a page load.
        if not _has_required_ledger_indexes(connection):
            connection.close()
            return None
        return connection
    except (OSError, sqlite3.Error, ValueError):
        if connection is not None:
            connection.close()
        return None


def _has_required_ledger_indexes(connection: sqlite3.Connection) -> bool:
    placeholders = ", ".join("?" for _ in _REQUIRED_LEDGER_INDEX_SQL)
    try:
        rows = connection.execute(
            f"""
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'index' AND name IN ({placeholders})
            """,
            tuple(_REQUIRED_LEDGER_INDEX_SQL),
        ).fetchall()
    except sqlite3.Error:
        return False
    found = {
        str(row["name"]): "".join(str(row["sql"] or "").lower().split())
        for row in rows
    }
    return found == _REQUIRED_LEDGER_INDEX_SQL


def _read_supervisor_admissions(
    path: Path, managed_spaces: Iterable[Mapping[str, Any]]
) -> list[dict[str, str]]:
    allowed_spaces = _managed_space_keys(managed_spaces)
    if not allowed_spaces:
        return []
    connection = _open_read_only_ledger(path)
    if connection is None:
        return []
    try:
        rows = [
            row
            for space in allowed_spaces
            if (row := connection.execute(_LATEST_ADMISSION_SQL, (space,)).fetchone()) is not None
        ]
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    admissions = []
    for row in rows:
        space = _safe_space(row["target_key"])
        admission_id = str(row["admission_id"] or "")
        if not space or not admission_id:
            continue
        admissions.append(
            {
                "admission_id": admission_id,
                "space": space,
                "state": _safe_run_state(row["state"]),
                "at": _safe_timestamp(row["updated_at"]),
            }
        )
    return sorted(admissions, key=lambda item: (item["at"], item["space"]), reverse=True)


def _latest_admission_by_space(
    admissions: Iterable[Mapping[str, str]], managed_spaces: Iterable[Mapping[str, Any]]
) -> dict[str, Mapping[str, str]]:
    allowed_spaces = set(_managed_space_keys(managed_spaces))
    latest: dict[str, Mapping[str, str]] = {}
    for admission in admissions:
        space = admission.get("space", "")
        if space in allowed_spaces and admission.get("state") in _PUBLIC_RUN_STATES:
            latest.setdefault(space, admission)
    return latest


def _focus_for(
    admissions: Iterable[Mapping[str, str]],
    managed_spaces: Iterable[Mapping[str, Any]],
    presence: str,
) -> dict[str, str]:
    allowed_spaces = set(_managed_space_keys(managed_spaces))
    for admission in admissions:
        space = str(admission.get("space") or "")
        state = str(admission.get("state") or "")
        if space in allowed_spaces and state in _PUBLIC_RUN_STATES:
            return {"kind": "supervision", "space": space, "state": state}
    return {"kind": "presence", "state": presence}


def _read_latest_supervisor_events(
    path: Path, admissions: Iterable[Mapping[str, str]]
) -> list[dict[str, str]]:
    latest_by_space: dict[str, str] = {}
    for admission in admissions:
        space = _safe_space(admission.get("space"))
        admission_id = str(admission.get("admission_id") or "")
        if space and admission_id:
            latest_by_space.setdefault(space, admission_id)
    if not latest_by_space:
        return []
    connection = _open_read_only_ledger(path)
    if connection is None:
        return []
    try:
        return _read_latest_audit_events(connection, latest_by_space)
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _read_latest_terminal_events(
    path: Path, managed_spaces: Iterable[Mapping[str, Any]]
) -> list[dict[str, str]]:
    allowed_spaces = _managed_space_keys(managed_spaces)
    if not allowed_spaces:
        return []
    connection = _open_read_only_ledger(path)
    if connection is None:
        return []
    try:
        # The supervisor permits only one non-terminal admission globally, so
        # this descending index seek reaches a Space's latest terminal record
        # without reading an unbounded run history.
        terminal_admissions = {
            space: str(row["admission_id"] or "")
            for space in allowed_spaces
            if (
                row := connection.execute(
                    _LATEST_TERMINAL_ADMISSION_SQL,
                    (space, *_TERMINAL_ADMISSION_STATES),
                ).fetchone()
            )
            is not None
            and str(row["admission_id"] or "")
        }
        return _read_latest_audit_events(connection, terminal_admissions)
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _read_latest_audit_events(
    connection: sqlite3.Connection, admission_by_space: Mapping[str, str]
) -> list[dict[str, str]]:
    rows = [
        (space, row)
        for space, admission_id in admission_by_space.items()
        if (row := connection.execute(_LATEST_AUDIT_SQL, (admission_id,)).fetchone()) is not None
    ]
    events = []
    for space, row in rows:
        events.append(
            {
                "space": space,
                "event_type": str(row["event_type"] or "").strip().lower(),
                "at": _safe_timestamp(row["created_at"]),
                "sequence": str(row["sequence"] or ""),
            }
        )
    return sorted(events, key=lambda item: int(item["sequence"] or 0), reverse=True)


def _activity_for(events: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    activity: list[dict[str, str]] = []
    for event in events:
        kind = _PUBLIC_ACTIVITY_KINDS.get(event.get("event_type", ""))
        space = _safe_space(event.get("space"))
        if kind is not None and space:
            activity.append({"kind": kind, "space": space, "at": event.get("at", "")})
        if len(activity) >= _MAX_ACTIVITY:
            break
    return activity


def _audited_results_for(events: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for event in events:
        space = _safe_space(event.get("space"))
        result = _RESULT_BY_EVENT.get(event.get("event_type", ""))
        if not space or result is None:
            continue
        results.append({"space": space, "result": result, "at": event.get("at", "")})
        if len(results) >= _MAX_RESULTS:
            break
    return results


def _blockers_for(managed_spaces: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    for space in managed_spaces:
        state = str(space.get("state") or "")
        code = _BLOCKER_CODES.get(state)
        if code:
            blockers.append({"space": str(space["space"]), "code": code})
    return blockers


def _safe_space(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _SPACE_SLUG_RE.fullmatch(candidate) else ""


def _safe_run_state(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in _PUBLIC_RUN_STATES else "unknown"


def _safe_timestamp(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if _TIMESTAMP_RE.fullmatch(candidate) else ""
