"""Strictly read-only public projection for the canonical Nova Space.

This module intentionally does not use ``EntityStateStore``, the Nova
lifecycle/status helpers, or the managed-Space governance resolver.  Those
components may migrate, repair, initialise a store, or otherwise write while
answering a read.  Presence cards must remain safe to fetch on every page
load, so this reader only opens already-existing files and SQLite databases
in read-only mode and returns a deliberately small public allowlist.
"""

from __future__ import annotations

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


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


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
