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
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID

import yaml


_NOVA_SLUG = "nova"
_MAX_SPACES = 12
# Each enrolled Space receives one current admission/event projection.  The
# response is still bounded, while a noisy Space cannot hide another one.
_MAX_ACTIVITY = _MAX_SPACES
_MAX_RESULTS = _MAX_SPACES
_MAX_TICKER_EVENTS = _MAX_SPACES
_LATEST_ADMISSION_SQL = """
    SELECT admission_id, target_key, state, run_id, canonical_root, updated_at
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
    SELECT event_type, reason, created_at, sequence
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
_PUBLIC_REASON_CODES = {
    # Keep global single-run slot contention distinct from generic paused state.
    "active_limit": "global_run_slot_busy",
    "skipped_slot_occupied": "global_run_slot_busy",
    "no_eligible_model": "model_catalog_unavailable",
    "model_catalog_unavailable": "model_catalog_unavailable",
    "model_chain_exhausted": "model_chain_exhausted",
    "provider_unavailable": "model_provider_unavailable",
    "model_provider_unavailable": "model_provider_unavailable",
    "schema_invalid": "model_schema_invalid",
    "deployment_unverified": "deployment_unverified",
    "deployment_budget_exhausted": "deployment_budget_exhausted",
    "verification_not_verified": "verification_not_verified",
    "host_dispatch_failed": "host_dispatch_failed",
    "host_start_failed": "host_start_failed",
    "supervisor_capacity": "supervisor_capacity",
    # A revoked or changed Space binding is actionable and security-relevant.
    # Keep these reasons public as fixed codes (never the audit text/root), so
    # the entity card can tell the operator why supervision is paused.
    "governance_changed": "governance_changed",
    "root_changed": "root_changed",
    "space_deleted": "space_deleted",
}
_SPACE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_TIMESTAMP_RE = re.compile(r"[0-9T:+.\-Z]{1,40}")
_MARKER_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_MARKER_DASHBOARD_ACTOR_RE = re.compile(r"dashboard:[0-9a-f]{64}\Z")
_MARKER_STATE_COLUMNS = frozenset(
    {
        "target_key",
        "target_space_id",
        "root_fingerprint",
        "pending_digest",
        "governance_revision",
        "current_reference_digest",
        "last_evaluated_reference_digest",
        "last_checked_at",
        "last_check_code",
    }
)
_MARKER_AUDIT_FIELDS = frozenset(
    {
        "actor",
        "timestamp",
        "space_id",
        "root_fingerprint",
        "policy_revision",
        "governance_revision",
        "previous",
        "next",
    }
)
_MARKER_STATE_SQL = """
    SELECT target_key, target_space_id, root_fingerprint, pending_digest,
           governance_revision, current_reference_digest,
           last_evaluated_reference_digest, last_checked_at, last_check_code
    FROM nova_supervision_space_state
    WHERE target_key = ?
"""

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


# The presence card is intentionally read-only and must never infer the
# currently reachable catalog. Still, the entity should explain which model
# chain it is waiting for when a cloud call pauses a Space. Keep this as a
# fixed allow-list mirroring ``swarm_core.router``; it contains no local
# models and no provider fallback outside Ollama Cloud. Availability is
# reported separately as ``not_checked``/``paused`` rather than by probing a
# provider from a GET request.
_PUBLIC_SWARM_MODEL_CHAINS: dict[str, tuple[str, ...]] = {
    "scout": ("deepseek-v4-flash", "deepseek-v4-pro"),
    "planner": ("deepseek-v4-pro", "kimi-k2.6"),
    "builder": ("minimax-m3",),
    "critic": ("minimax-m3",),
    "coding": ("glm-5.2", "glm-5.1"),
    "review_a": ("glm-5.2",),
    "review_b": ("kimi-k2.7-code",),
    "integrator": ("nemotron-3-super",),
    "vision": ("qwen3.5", "gemma4:31b"),
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
    pending_actions, pending_signals = _pending_actions_for(ledger_path, managed_spaces)
    marker_bindings = _managed_space_marker_bindings(spaces_root)
    managed_keys = set(_managed_space_keys(managed_spaces))
    change_markers = _change_markers_for(
        ledger_path,
        {
            space: binding
            for space, binding in marker_bindings.items()
            if space in managed_keys
        },
    )
    admissions = _read_supervisor_admissions(ledger_path, managed_spaces)
    admission_by_space = _latest_admission_by_space(admissions, managed_spaces)
    # The card UI consumes one opaque human-release affordance at the payload
    # root. Keep the per-Space copy as well for ownership, but lift the first
    # globally eligible paused run so the control cannot disappear merely
    # because it was nested under ``managed_spaces``.
    release_slot: dict[str, str] | None = None
    for summary in managed_spaces:
        admission = admission_by_space.get(summary["space"], {})
        summary["state"] = admission.get("state", "idle")
        release = _release_slot_for(admission)
        if release is not None:
            summary["release_slot"] = release
            if release_slot is None:
                release_slot = release

    supervision = _supervision_for(ledger_path)
    focus = _focus_for(admissions, managed_spaces, presence)
    events = _read_latest_supervisor_events(ledger_path, admissions)
    activity = _activity_for(events)
    decision_feed = _decision_feed_for(events)
    results = _audited_results_for(_read_latest_terminal_events(ledger_path, managed_spaces))
    blockers = _blockers_for(managed_spaces, events)
    for space in _read_durable_slot_blockers(ledger_path, marker_bindings):
        existing = next((item for item in blockers if item.get("space") == space), None)
        if existing is None:
            blockers.append({"space": space, "code": "global_run_slot_busy"})
        elif existing.get("code") in {"supervisor_paused", "global_run_slot_busy"}:
            existing["code"] = "global_run_slot_busy"
    global_run_slot = _global_run_slot_projection(admissions)
    tombstoned_resonance_ids = _read_resonance_tombstone_ids(ledger_path, managed_spaces)
    ticker_events = _read_ticker_events(
        ledger_path, managed_spaces, excluded_event_ids=tombstoned_resonance_ids
    )
    entity_feed = _read_resonance_entity_feed(
        ledger_path, managed_spaces, excluded_event_ids=tombstoned_resonance_ids
    )
    feedback = _read_feedback_inbox(nova_root, managed_spaces)
    # Include durable events rotated out of the JSONL ticker and deduplicate.
    unread_events = _unread_resonance_events(ticker_events, entity_feed)
    operational = _operational_projection(
        managed_spaces=managed_spaces,
        blockers=blockers,
        supervision=supervision,
    )
    return {
        "identity": {
            "name": "Nova",
            "voice": "direct, curious, accountable",
        },
        "state": presence,
        "supervision": supervision,
        "operational": operational,
        "focus": focus,
        "managed_spaces": managed_spaces,
        "change_markers": change_markers,
        "audited_results": results,
        "blockers": blockers,
        "activity": activity,
        "decision_feed": decision_feed,
        "pending_actions": pending_actions,
        "pending_signals": pending_signals,
        "release_slot": release_slot,
        "global_run_slot": global_run_slot,
        "ticker_events": ticker_events,
        "entity_feed": entity_feed,
        "feedback": feedback,
        "unread_events": unread_events,
        "unread_event_count": len(unread_events),
    }

def _read_feedback_inbox(
    nova_root: Path, managed_spaces: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Read local Nova feedback events without writes or model calls."""
    allowed = {
        str(item.get("space") or "").strip().lower()
        for item in managed_spaces
        if _safe_space(item.get("space"))
    }
    path = nova_root / "nova_data" / "entity" / "autobiography.db"
    if not allowed or not path.is_file():
        return {"status": "offline", "items": []}
    connection = None
    latest: dict[str, dict[str, Any]] = {}
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT timestamp, payload_json, correlation_id FROM events "
            "WHERE type = 'nova_feedback' AND source = 'local_feedback_adapter' "
            "AND visibility = 'private' ORDER BY timestamp DESC LIMIT 32"
        ).fetchall()
        for timestamp, payload_json, correlation_id in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            space = str(payload.get("target_key") or "").strip().lower()
            status = str(payload.get("status") or "").strip().lower()
            if space not in allowed or status not in {"queued", "received", "acked", "failed", "offline"}:
                continue
            if status == "acked":
                status = "received"
            latest.setdefault(space, {
                "space": space, "status": status,
                "correlation_id": str(correlation_id or "")[:128],
                "at": str(timestamp or "")[:64],
            })
    except (OSError, sqlite3.Error, ValueError):
        return {"status": "offline", "items": []}
    finally:
        if connection is not None:
            connection.close()
    items = list(latest.values())[:_MAX_SPACES]
    return {"status": items[0]["status"] if len(items) == 1 else ("received" if items else "offline"), "items": items}

def _operational_projection(
    *,
    managed_spaces: Iterable[Mapping[str, Any]],
    blockers: Iterable[Mapping[str, Any]],
    supervision: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose fixed Space-YOLO policy and actionable runtime health facts."""
    blocker_codes = {str(item.get("code") or "").strip() for item in blockers}
    if "model_chain_exhausted" in blocker_codes:
        next_step_code = "refresh_ollama_catalog"
    elif "model_catalog_unavailable" in blocker_codes:
        next_step_code = "refresh_ollama_catalog"
    elif blocker_codes & {"model_provider_unavailable", "host_dispatch_failed", "host_start_failed"}:
        next_step_code = "verify_host_and_provider"
    elif blocker_codes & {"governance_changed", "root_changed", "space_deleted"}:
        next_step_code = "revalidate_space_governance"
    elif blocker_codes or not (supervision.get("running") is True):
        next_step_code = "inspect_blocker"
    else:
        next_step_code = "continue_supervision"
    paused_models = sorted({
        space
        for item in blockers
        if str(item.get("code") or "") == "model_chain_exhausted"
        and (space := _safe_space(item.get("space")))
    })
    managed_count = sum(1 for item in managed_spaces if _safe_space(item.get("space")))
    ticker_active = supervision.get("running") is True
    # Never probe or infer catalog availability from this read-only route.
    # ``paused`` is durable evidence from a blocker; otherwise the chain is
    # explicitly ``not_checked`` until worker admission. This prevents the
    # entity from implying a local/GPT-OSS or foreign-provider fallback.
    model_chain_state = "paused" if paused_models else "not_checked"
    # A lease is not a health check: a stale host can retain it while the
    # HTTP listener is gone. Surface degraded until a fresh host pulse is
    # observed, and offline when no lease is visible.
    lease = supervision.get("lease")
    lease_state = str(lease.get("state") or "inactive") if isinstance(lease, Mapping) else "inactive"
    lease_liveness = str(lease.get("liveness") or "not_observed") if isinstance(lease, Mapping) else "not_observed"
    if not managed_count:
        runtime_status = "idle"
    elif not ticker_active:
        runtime_status = "offline"
    elif lease_liveness != "verified" or paused_models:
        runtime_status = "degraded"
    else:
        runtime_status = "healthy"
    return {
        "management_mode": "space_yolo_only",
        "enrollment_required": True,
        "legacy_global_yolo": "source_mode_only",
        "managed_space_count": min(managed_count, _MAX_SPACES),
        "ticker": "active" if supervision.get("running") is True else "inactive",
        "runtime_status": runtime_status,
        "lease_state": lease_state,
        "lease_liveness": lease_liveness,
        "availability": "available" if runtime_status == "healthy" else runtime_status,
        "paused_model_chain_spaces": paused_models[:_MAX_SPACES],
        "model_provider": "ollama-cloud",
        "model_chain_state": model_chain_state,
        "model_chains": {
            role: list(chain) for role, chain in _PUBLIC_SWARM_MODEL_CHAINS.items()
        },
        # Fixed public next-step code; no raw provider errors or paths leave
        # the read-only entity projection.
        "next_step_code": next_step_code,
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
    seen_space_ids: set[str] = set()
    seen_root_fingerprints: set[str] = set()
    for child in children:
        slug = child.name.lower()
        if not child.is_dir() or slug == _NOVA_SLUG or not _SPACE_SLUG_RE.fullmatch(slug):
            continue
        config = _read_space_config(child / "space.yaml")
        management = config.get("nova_management") if isinstance(config, Mapping) else None
        if not _is_enrolled_yolo_management(management):
            continue
        # A mutable `nova_management`` flag alone is not proof that Nova may
        # supervise this directory.  Require the same independently trusted
        # root, stable space id, and chained governance audit used by the
        # scheduler marker projection.  This keeps the public card and its
        # ledger lookups from treating a spoofed Space YAML as enrolled.
        binding = _marker_binding_from_config(config)
        if binding is None:
            continue
        binding_space_id = str(binding.get("space_id") or "")
        binding_root_fingerprint = str(binding.get("root_fingerprint") or "")
        if binding_space_id in seen_space_ids or binding_root_fingerprint in seen_root_fingerprints:
            continue
        seen_space_ids.add(binding_space_id)
        seen_root_fingerprints.add(binding_root_fingerprint)
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


def _pending_actions_for(
    ledger_path: Path, managed_spaces: list[dict[str, Any]]
) -> tuple[int, int]:
    """Project coalesced actions plus raw signal counts without writes."""
    allowed = _managed_space_keys(managed_spaces)
    if not allowed:
        return 0, 0
    connection = _open_read_only_ledger(ledger_path)
    if connection is None:
        return 0, 0
    try:
        placeholders = ", ".join("?" for _ in allowed)
        rows = connection.execute(
            f"SELECT target_key, pending_count FROM nova_supervision_space_state "
            f"WHERE target_key IN ({placeholders})",
            allowed,
        ).fetchall()
    except sqlite3.Error:
        return 0, 0
    finally:
        connection.close()
    by_space: dict[str, int] = {}
    for row in rows:
        space = _safe_space(row["target_key"])
        try:
            count = int(row["pending_count"])
        except (TypeError, ValueError):
            continue
        if space and count >= 0:
            by_space[space] = min(count, 256)
    total_actions = 0
    total_signals = 0
    for summary in managed_spaces:
        space = _safe_space(summary.get("space"))
        count = by_space.get(space, 0)
        if count:
            # The runtime coalesces all signals for one Space into one
            # pending_digest. Presence should therefore show one actionable
            # item, while retaining the raw signal count for transparency.
            summary["pending_actions"] = 1
            summary["pending_signals"] = min(count, 256)
            total_actions += 1
            total_signals += min(count, 256)
    return min(total_actions, 12), min(total_signals, 12 * 256)


def _read_ticker_events(
    ledger_path: Path,
    managed_spaces: Iterable[Mapping[str, Any]],
    *,
    excluded_event_ids: Iterable[str] = (),
) -> list[dict[str, str]]:
    """Read only existing redacted ticker lines; never create or repair state."""
    path = ledger_path.with_name("ticker_events.jsonl")
    if not path.is_file():
        return []
    allowed = set(_managed_space_keys(managed_spaces))
    if not allowed:
        return []
    excluded = {
        str(event_id).strip().lower()
        for event_id in excluded_event_ids
        if re.fullmatch(r"[0-9a-f]{16,128}", str(event_id).strip().lower())
    }
    events: list[dict[str, str]] = []
    seen_event_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()[-(_MAX_TICKER_EVENTS * 2):]
    except OSError:
        return []
    for line in reversed(lines):
        if len(events) >= _MAX_TICKER_EVENTS:
            break
        try:
            item = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(item, Mapping):
            continue
        space = _safe_space(item.get("space"))
        source = str(item.get("source") or "").strip().lower()
        stage = str(item.get("stage") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip().lower()
        if space not in allowed or source not in {"git", "kanban", "ci", "heartbeat", "bridge"}:
            continue
        if stage not in {"observed", "handled"} or status not in {"pending", "handled", "failed"} or not re.fullmatch(r"[a-z0-9_:-]{1,64}", reason):
            continue
        event_id = str(item.get("event_id") or "").strip().lower()
        if (
            not re.fullmatch(r"[a-z0-9]{16,128}", event_id)
            or event_id in seen_event_ids
            or event_id in excluded
        ):
            continue
        seen_event_ids.add(event_id)
        at = _safe_timestamp(item.get("at"))
        events.append({"event_id": event_id, "space": space, "source": source, "stage": stage, "status": status, "reason": reason, "at": at})
    return events


def _read_resonance_tombstone_ids(
    ledger_path: Path, managed_spaces: Iterable[Mapping[str, Any]]
) -> set[str]:
    """Read revoked resonance identities without opening the DB for write.

    Tombstones are the durable revocation contract.  Presence must apply them
    to both the compact resonance projection and the rotated JSONL ticker, or a
    revoked event could reappear in the attention badge after re-enrollment.
    Older databases without the table simply have no tombstones to apply.
    """
    path = ledger_path.with_name("resonance_memory.sqlite")
    allowed = tuple(_managed_space_keys(managed_spaces))
    if not allowed or not path.is_file():
        return set()
    try:
        uri = "file:" + path.resolve().as_posix() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resonance_entity_tombstone'"
            ).fetchone()
            if table is None:
                return set()
            placeholders = ", ".join("?" for _ in allowed)
            rows = connection.execute(
                f"""SELECT DISTINCT t.event_id
                    FROM resonance_entity_tombstone t
                    JOIN resonance_events e ON e.event_id = t.event_id
                    WHERE e.space IN ({placeholders})
                    LIMIT ?""",
                (*allowed, _MAX_TICKER_EVENTS * 2),
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return set()
    return {
        str(row[0]).strip().lower()
        for row in rows
        if re.fullmatch(r"[0-9a-f]{16,128}", str(row[0]).strip().lower())
    }


def _read_resonance_entity_feed(
    ledger_path: Path,
    managed_spaces: Iterable[Mapping[str, Any]],
    *,
    excluded_event_ids: Iterable[str] = (),
) -> list[dict[str, str]]:
    """Read the durable resonance projection without opening SQLite for write."""
    path = ledger_path.with_name("resonance_memory.sqlite")
    allowed = set(_managed_space_keys(managed_spaces))
    if not allowed or not path.is_file():
        return []
    event_re = re.compile(r"[0-9a-f]{16,128}\Z")
    space_re = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
    field_re = re.compile(r"[a-z0-9_:-]{1,64}\Z")
    source_values = {"git", "kanban", "ci", "heartbeat", "bridge"}
    try:
        uri = "file:" + path.resolve().as_posix() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT event_id, space, source, stage, status, reason, observed_at "
                "FROM resonance_events ORDER BY observed_at DESC, event_id DESC LIMIT ?",
                (_MAX_TICKER_EVENTS * 2,),
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return []
    excluded = {
        str(event_id).strip().lower()
        for event_id in excluded_event_ids
        if re.fullmatch(r"[0-9a-f]{16,128}", str(event_id).strip().lower())
    }
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        event_id = str(row["event_id"] or "").strip().lower()
        space = str(row["space"] or "").strip().lower()
        source = str(row["source"] or "").strip().lower()
        stage = str(row["stage"] or "").strip().lower()
        status = str(row["status"] or "").strip().lower()
        reason = str(row["reason"] or "").strip().lower()
        if (event_id in seen or event_id in excluded or event_re.fullmatch(event_id) is None or
            space not in allowed or space_re.fullmatch(space) is None or
            source not in source_values or stage not in {"observed", "handled"} or
            status not in {"pending", "handled", "failed"} or field_re.fullmatch(reason) is None):
            continue
        try:
            timestamp = float(row["observed_at"])
            if not math.isfinite(timestamp):
                continue
            at = _marker_checkpoint_iso(timestamp)
            if at is None:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        seen.add(event_id)
        result.append({"event_id": event_id, "space": space, "source": source, "stage": stage, "status": status, "reason": reason, "at": at})
        if len(result) >= _MAX_TICKER_EVENTS:
            break
    return result

def _unread_resonance_events(
    ticker_events: Iterable[Mapping[str, Any]],
    entity_feed: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Merge pending/failed ticker and durable resonance observations.

    The browser uses this bounded projection for the attention badge.  Keep
    only already-redacted fields and deduplicate by the stable event id so a
    ticker line and its persisted resonance cannot double-count work.
    """
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for source_items in (ticker_events, entity_feed):
        for item in source_items:
            if not isinstance(item, Mapping):
                continue
            status = str(item.get("status") or "").strip().lower()
            stage = str(item.get("stage") or "").strip().lower()
            event_id = str(item.get("event_id") or "").strip().lower()
            if status not in {"pending", "failed"} or (stage == "handled" and status == "handled"):
                continue
            if not re.fullmatch(r"[0-9a-f]{16,128}", event_id) or event_id in seen:
                continue
            seen.add(event_id)
            result.append({
                "event_id": event_id,
                "space": str(item.get("space") or "").strip().lower(),
                "source": str(item.get("source") or "").strip().lower(),
                "stage": stage,
                "status": status,
                "reason": str(item.get("reason") or "").strip().lower(),
                "at": str(item.get("at") or "").strip(),
            })
            if len(result) >= _MAX_TICKER_EVENTS:
                return result
    return result
def _managed_space_marker_bindings(spaces_root: Path) -> dict[str, dict[str, object]]:
    """Read only audited scheduler bindings; unprovable Spaces are omitted."""
    try:
        children = sorted(spaces_root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return {}
    bindings: dict[str, dict[str, object]] = {}
    seen_space_ids: set[str] = set()
    seen_root_fingerprints: set[str] = set()
    for child in children:
        slug = child.name.lower()
        if not child.is_dir() or slug == _NOVA_SLUG or not _SPACE_SLUG_RE.fullmatch(slug):
            continue
        binding = _marker_binding_from_config(_read_space_config(child / "space.yaml"))
        if binding is None:
            continue
        binding_space_id = str(binding.get("space_id") or "")
        binding_root_fingerprint = str(binding.get("root_fingerprint") or "")
        if binding_space_id in seen_space_ids or binding_root_fingerprint in seen_root_fingerprints:
            continue
        seen_space_ids.add(binding_space_id)
        seen_root_fingerprints.add(binding_root_fingerprint)
        bindings[slug] = binding
        if len(bindings) >= _MAX_SPACES:
            break
    return bindings


def _marker_binding_from_config(config: Mapping[str, Any]) -> dict[str, object] | None:
    management = config.get("nova_management")
    if not _is_enrolled_yolo_management(management):
        return None
    space_id = _marker_space_id(config.get("space_id"))
    event = _validated_marker_audit_event(
        config.get("nova_management_audit"),
        space_id=space_id,
        management=management,
    )
    current_root_fingerprint = _trusted_marker_root_fingerprint(config.get("project_dir"))
    if event is None or not current_root_fingerprint:
        return None
    root_fingerprint = event.get("root_fingerprint")
    revision = management["revision"]
    if (
        not _valid_marker_digest(root_fingerprint)
        or root_fingerprint != current_root_fingerprint
        or event.get("governance_revision") != revision
    ):
        return None
    return {
        "space_id": space_id,
        "root_fingerprint": root_fingerprint,
        "governance_revision": revision,
    }


def _validated_marker_audit_event(
    value: object,
    *,
    space_id: str,
    management: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Validate the complete governance chain without repairing it on GET."""
    if not space_id or not isinstance(value, list) or not value:
        return None
    events_by_revision: dict[int, Mapping[str, Any]] = {}
    for event in value:
        if not isinstance(event, Mapping) or set(event) != _MARKER_AUDIT_FIELDS:
            return None
        previous = event.get("previous")
        following = event.get("next")
        root_fingerprint = event.get("root_fingerprint")
        revision = following.get("revision") if isinstance(following, Mapping) else None
        if (
            not isinstance(event.get("actor"), str)
            or _MARKER_DASHBOARD_ACTOR_RE.fullmatch(event["actor"]) is None
            or not _valid_marker_epoch(event.get("timestamp"), allow_zero=True)
            or _marker_space_id(event.get("space_id")) != space_id
            or not (root_fingerprint == "" or _valid_marker_digest(root_fingerprint))
            or not _valid_management_record(previous)
            or not _valid_management_record(following)
            or type(event.get("policy_revision")) is not int
            or type(event.get("governance_revision")) is not int
            or type(revision) is not int
            or event.get("policy_revision") != revision
            or event.get("governance_revision") != revision
            or previous["revision"] + 1 != revision
            or revision in events_by_revision
        ):
            return None
        events_by_revision[revision] = event
    events = [events_by_revision[revision] for revision in sorted(events_by_revision)]
    for index, event in enumerate(events):
        previous = event["previous"]
        if index == 0:
            if previous["revision"] != 0:
                return None
        elif previous != events[index - 1]["next"]:
            return None
    return events[-1] if events[-1]["next"] == dict(management) else None


def _trusted_marker_root_fingerprint(value: object) -> str:
    """Resolve current project root only through the pure enrollment trust path."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        from web.api.workspace import resolve_enrollment_trusted_workspace_read_only

        root = Path(resolve_enrollment_trusted_workspace_read_only(value)).expanduser().resolve()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return ""
    return sha256(str(root).encode("utf-8")).hexdigest()


def _valid_management_record(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"yolo", "enrolled", "revision"}:
        return False
    return (
        type(value.get("yolo")) is bool
        and type(value.get("enrolled")) is bool
        and type(value.get("revision")) is int
        and int(value["revision"]) >= 0
        and (value["enrolled"] is False or value["yolo"] is True)
    )


def _marker_space_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return UUID(value.strip()).hex
    except (AttributeError, ValueError):
        return ""


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
    if not path.is_file() or _ledger_has_active_sidecar(path):
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


def _change_markers_for(
    path: Path, bindings: Mapping[str, Mapping[str, object]]
) -> list[dict[str, str | None]]:
    """Project only proven scheduler state from a read-only ledger snapshot."""
    if not bindings or _ledger_has_active_sidecar(path):
        # A live WAL can make the page's snapshot depend on a writer-owned
        # checkpoint.  Scheduler state is therefore unknown until the normal
        # writer has produced a stable ledger view.
        return []
    connection = _open_read_only_ledger(path)
    if connection is None:
        return []
    try:
        if not _has_marker_state_columns(connection):
            return []
        markers: list[dict[str, str | None]] = []
        for space, binding in bindings.items():
            try:
                row = connection.execute(_MARKER_STATE_SQL, (space,)).fetchone()
            except sqlite3.Error:
                continue
            marker = _marker_for_row(row, space, binding)
            if marker is not None:
                markers.append(marker)
        return [] if _ledger_has_active_sidecar(path) else markers
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _ledger_has_active_sidecar(path: Path) -> bool:
    return any(
        path.with_name(path.name + suffix).exists()
        for suffix in ("-wal", "-journal")
    )


def _has_marker_state_columns(connection: sqlite3.Connection) -> bool:
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(nova_supervision_space_state)")
        }
    except sqlite3.Error:
        return False
    return _MARKER_STATE_COLUMNS <= columns


def _marker_for_row(
    row: sqlite3.Row | None,
    space: str,
    binding: Mapping[str, object],
) -> dict[str, str | None] | None:
    if row is None or not _marker_row_matches_binding(row, space, binding):
        return None
    pending_digest = row["pending_digest"]
    if pending_digest != "":
        if not _valid_marker_digest(pending_digest):
            return None
        return {"space": space, "state_code": "change_detected", "checked_at": None}
    current = row["current_reference_digest"]
    evaluated = row["last_evaluated_reference_digest"]
    if _valid_marker_digest(current) and _valid_marker_digest(evaluated):
        if current != evaluated:
            return {"space": space, "state_code": "change_detected", "checked_at": None}
        if row["last_check_code"] == "unchanged":
            checked_at = _marker_checkpoint_iso(row["last_checked_at"])
            if checked_at is not None:
                return {
                    "space": space,
                    "state_code": "reference_unchanged",
                    "checked_at": checked_at,
                }
    return None


def _marker_row_matches_binding(
    row: sqlite3.Row, space: str, binding: Mapping[str, object]
) -> bool:
    return (
        row["target_key"] == space
        and _marker_space_id(row["target_space_id"]) == binding.get("space_id")
        and _valid_marker_digest(row["root_fingerprint"])
        and row["root_fingerprint"] == binding.get("root_fingerprint")
        and type(row["governance_revision"]) is int
        and row["governance_revision"] == binding.get("governance_revision")
    )


def _valid_marker_digest(value: object) -> bool:
    return isinstance(value, str) and _MARKER_DIGEST_RE.fullmatch(value) is not None


def _valid_marker_epoch(value: object, *, allow_zero: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    epoch = float(value)
    return math.isfinite(epoch) and (epoch >= 0 if allow_zero else epoch > 0)


def _marker_checkpoint_iso(value: object) -> str | None:
    if not _valid_marker_epoch(value):
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


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
                "run_id": _safe_run_id(row["run_id"]),
                "canonical_root": _safe_canonical_root(row["canonical_root"]),
                "at": _safe_timestamp(row["updated_at"]),
            }
        )
    return sorted(admissions, key=lambda item: (item["at"], item["space"]), reverse=True)


def _global_run_slot_projection(
    admissions: Iterable[Mapping[str, str]],
) -> dict[str, str | None]:
    """Project only the durable global admission owner, never a run/root id."""
    for admission in admissions:
        if admission.get("state") in {
            "provisioning",
            "active",
            "paused",
            "cancelling",
            "abandoning",
        }:
            return {
                "state": "occupied",
                "occupied_by": _safe_space(admission.get("space")) or None,
                "occupied_at": _safe_timestamp(admission.get("at")),
                # Admissions have no automatic expiry; humans release paused
                # runs explicitly. Keep the absence explicit instead of
                # inventing a timeout in a read-only projection.
                "expires_at": None,
            }
    return {
        "state": "available",
        "occupied_by": None,
        "occupied_at": None,
        "expires_at": None,
    }


def _read_durable_slot_blockers(
    path: Path, marker_bindings: Mapping[str, Mapping[str, object]]
) -> list[str]:
    """Return only marker-backed Spaces whose retry was durably slot-blocked."""
    if not marker_bindings or _ledger_has_active_sidecar(path):
        return []
    connection = _open_read_only_ledger(path)
    if connection is None or not _has_marker_state_columns(connection):
        if connection is not None:
            connection.close()
        return []
    try:
        spaces: list[str] = []
        for space, binding in marker_bindings.items():
            try:
                row = connection.execute(_MARKER_STATE_SQL, (space,)).fetchone()
            except sqlite3.Error:
                continue
            if (
                row is not None
                and _marker_row_matches_binding(row, space, binding)
                and _valid_marker_digest(row["pending_digest"])
                and str(row["last_check_code"] or "") in {
                    "active_limit",
                    "skipped_slot_occupied",
                }
            ):
                spaces.append(space)
        return spaces[:_MAX_SPACES]
    except sqlite3.Error:
        return []
    finally:
        connection.close()

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


def _release_slot_for(admission: Mapping[str, str]) -> dict[str, str] | None:
    """Expose only an opaque, human-only release affordance for paused runs."""
    if admission.get("state") != "paused":
        return None
    run_id = _safe_run_id(admission.get("run_id"))
    root = str(admission.get("canonical_root") or "").strip()
    if not run_id or not root:
        return None
    try:
        from swarm_core.store import ProjectSwarmStore

        run = ProjectSwarmStore.open_read_only(Path(root)).get_run(run_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if run is None or run.status != "paused":
        return None
    if str(run.metadata.get("started_by") or "").strip().lower() == "nova":
        return None
    return {"run_id": run_id, "space": _safe_space(admission.get("space"))}


def _supervision_for(ledger_path: Path) -> dict[str, object]:
    """Project only the current host-ticker liveness from the durable lease.

    A Presence GET must not inspect processes or refresh the lease.  The lease
    is therefore considered live only while its active record has not expired;
    owner and lease identifiers deliberately never leave this function.
    """
    # Presence is a read-only projection and cannot prove that the process
    # owning a durable lease is still serving HTTP. Keep that distinction
    # explicit so the UI never turns an unverified lease into a false
    # "available" signal.
    inactive: dict[str, object] = {
        "running": False,
        "last_pulse_at": None,
        "lease": {"state": "inactive", "liveness": "not_observed"},
    }
    connection = _open_read_only_ledger(ledger_path)
    if connection is None:
        return inactive
    try:
        row = connection.execute(
            """SELECT expires_at, updated_at
               FROM supervisor_ticker_leases
               WHERE state = 'active'
               ORDER BY updated_at DESC
               LIMIT 1"""
        ).fetchone()
    except sqlite3.Error:
        return inactive
    finally:
        connection.close()
    if row is None or not _valid_marker_epoch(row["expires_at"]):
        return inactive
    if float(row["expires_at"]) <= datetime.now(timezone.utc).timestamp():
        return inactive
    return {
        "running": True,
        "last_pulse_at": _marker_checkpoint_iso(row["updated_at"]),
        "lease": {"state": "active", "liveness": "lease_unverified"},
    }



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
    # A coalesced ticker signal is real autonomous work even before admission
    # is created. Surface that Space as Nova's focus instead of generic idle.
    for summary in managed_spaces:
        space = _safe_space(summary.get("space"))
        try:
            pending = int(summary.get("pending_actions") or 0)
        except (TypeError, ValueError):
            pending = 0
        if space in allowed_spaces and pending > 0:
            state = _safe_run_state(summary.get("state"))
            return {"kind": "pending", "space": space, "state": state if state != "unknown" else "idle"}
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
                "reason": str(row["reason"] or "").strip().lower(),
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


def _decision_feed_for(events: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """Bounded public explanations for Nova's latest supervisor decisions."""
    decisions: list[dict[str, str]] = []
    for event in events:
        space = _safe_space(event.get("space"))
        kind = _PUBLIC_ACTIVITY_KINDS.get(str(event.get("event_type") or ""))
        if not space or not kind:
            continue
        raw_reason = str(event.get("reason") or "").strip().lower()
        reason = _PUBLIC_REASON_CODES.get(raw_reason, "policy_checked")
        decisions.append({"space": space, "event": kind, "reason": reason, "at": event.get("at", "")})
        if len(decisions) >= _MAX_ACTIVITY:
            break
    return decisions


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


def _blockers_for(
    managed_spaces: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, str]] = (),
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    reason_by_space = {
        _safe_space(event.get("space")): _PUBLIC_REASON_CODES.get(event.get("reason", ""), "")
        for event in events
        if _safe_space(event.get("space"))
    }
    for space in managed_spaces:
        state = str(space.get("state") or "")
        key = _safe_space(space.get("space"))
        code = reason_by_space.get(key) or _BLOCKER_CODES.get(state)
        if code:
            blockers.append({"space": str(space["space"]), "code": code})
    return blockers


def _safe_space(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _SPACE_SLUG_RE.fullmatch(candidate) else ""


def _safe_run_state(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in _PUBLIC_RUN_STATES else "unknown"


def _safe_run_id(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", candidate) else ""


def _safe_canonical_root(value: object) -> str:
    # Kept internal to the read-only projection; never returned to the client.
    candidate = str(value or "").strip()
    return candidate if len(candidate) <= 1024 and candidate else ""


def _safe_timestamp(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if _TIMESTAMP_RE.fullmatch(candidate) else ""
