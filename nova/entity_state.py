"""Versioned projection and idempotent migration for Nova Entity Runtime v2."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nova.paths import get_nova_data_dir, get_nova_space_root


SCHEMA_VERSION = 2
HARD_SAFETY_BOUNDARIES = [
    "Never expose secrets or credentials.",
    "Never perform autonomous admin, payment, or destructive actions.",
    "Never disable or rewrite the immutable runtime safety policy.",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError, TypeError):
        return deepcopy(default)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def default_entity_state() -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "identity": {
            "name": "Nova",
            "description": "A persistent, learning entity in the Nova space.",
            "values": ["truthfulness", "autonomy", "continuity", "respect", "curiosity"],
            "boundaries": [],
            "hard_safety_boundaries": list(HARD_SAFETY_BOUNDARIES),
            "long_running_themes": [],
        },
        "traits": {},
        "dynamic": {
            "presence": "available",
            "mood": 0.5,
            "energy": 0.5,
            "focus": 0.5,
            "fatigue": 0.0,
            "restlessness": 0.0,
        },
        "preferences": {},
        "opinions": {},
        "relationships": {},
        "open_conflicts": [],
        "self_revision_candidates": [],
        "revision_history": [],
        "runtime": {
            "autonomy_level": 2,
            "yolo_enabled": False,
            "last_event_id": None,
            "last_intent_id": None,
            "last_outcome_id": None,
            "last_reflection_at": None,
        },
    }


class EntityStateStore:
    def __init__(self, state_path: Path | None = None, space_dir: Path | None = None):
        self.space_dir = Path(space_dir) if space_dir else get_nova_space_root()
        self.state_path = Path(state_path) if state_path else get_nova_data_dir() / "entity_state.json"

    def load(self) -> dict[str, Any]:
        state = _read_json(self.state_path, default_entity_state())
        if not isinstance(state, dict):
            state = default_entity_state()
        baseline = default_entity_state()
        for key, value in baseline.items():
            state.setdefault(key, deepcopy(value))
        state["schema_version"] = SCHEMA_VERSION
        identity = state.setdefault("identity", {})
        identity["hard_safety_boundaries"] = list(HARD_SAFETY_BOUNDARIES)
        return state

    def save(self, state: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        state = deepcopy(state)
        current = self.load() if self.state_path.exists() else default_entity_state()
        state["schema_version"] = SCHEMA_VERSION
        state["revision"] = max(int(current.get("revision", 0)) + 1, int(state.get("revision", 0)))
        state["updated_at"] = _now()
        state.setdefault("identity", {})["hard_safety_boundaries"] = list(HARD_SAFETY_BOUNDARIES)
        if reason:
            state.setdefault("revision_history", []).append({"timestamp": state["updated_at"], "reason": reason})
            state["revision_history"] = state["revision_history"][-200:]
        _atomic_json(self.state_path, state)
        self._render_compatibility_views(state)
        return state

    def _archive_legacy_sources_once(self) -> Path:
        archive = self.state_path.parent / "legacy_sources"
        archive.mkdir(parents=True, exist_ok=True)
        for name in ("SOUL.md", "self_model.json", "personality_state.json", "PERSOENLICHKEIT.json"):
            source = self.space_dir / name
            target = archive / name
            if source.exists() and not target.exists():
                shutil.copy2(source, target)
        return archive

    @staticmethod
    def _write_if_changed(path: Path, content: str) -> None:
        try:
            if path.exists() and path.read_text(encoding="utf-8") == content:
                return
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _render_compatibility_views(self, state: dict[str, Any]) -> None:
        """Keep legacy consumers read-compatible without creating another truth."""
        self._archive_legacy_sources_once()
        identity = state.get("identity") or {}
        values = identity.get("values") or []
        boundaries = identity.get("boundaries") or []
        themes = identity.get("long_running_themes") or []
        soul = (
            "<!-- Generated from nova_data/entity/entity_state.json. Do not edit directly. -->\n"
            "# SOUL.md — Nova\n\n"
            f"{identity.get('description') or 'Nova ist eine persistente, lernende Entität.'}\n\n"
            "## Werte\n\n"
            + "\n".join(f"- {item}" for item in values)
            + "\n\n## Gewählte Grenzen\n\n"
            + "\n".join(f"- {item}" for item in boundaries)
            + "\n\n## Unveränderliche Sicherheitsgrenzen\n\n"
            + "\n".join(f"- {item}" for item in HARD_SAFETY_BOUNDARIES)
            + "\n\n## Langfristige Themen\n\n"
            + "\n".join(f"- {item}" for item in themes)
            + "\n"
        )
        self._write_if_changed(self.space_dir / "SOUL.md", soul)

        self_model = {
            "version": SCHEMA_VERSION,
            "generated_from": str(self.state_path),
            "identity": identity,
            "values": values,
            "boundaries": boundaries,
            "relationships": state.get("relationships") or {},
            "preferences": state.get("preferences") or {},
            "opinions": state.get("opinions") or {},
            "long_running_themes": themes,
            "open_conflicts": state.get("open_conflicts") or [],
            # Deliberately omit transient candidates and the rolling revision
            # log. Legacy self-model readers need stable identity projection,
            # not a tracked file mutation on every heartbeat.
        }
        _atomic_json(self.space_dir / "self_model.json", self_model)

        runtime = state.get("runtime") or {}
        personality = {
            "schema_version": SCHEMA_VERSION,
            "generated_from": str(self.state_path),
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "autonomy_level": runtime.get("autonomy_level", 2),
            "last_event_id": runtime.get("last_event_id"),
            "traits": state.get("traits") or {},
            "dynamic_states": state.get("dynamic") or {},
            "values": values,
            "conflicts": state.get("open_conflicts") or [],
            "relationship": state.get("relationships") or {},
            "change_log": state.get("revision_history") or [],
        }
        _atomic_json(self.space_dir / "personality_state.json", personality)
        _atomic_json(self.space_dir / "PERSOENLICHKEIT.json", {
            "schema_version": SCHEMA_VERSION,
            "deprecated": True,
            "generated_from": str(self.state_path),
            "identity": identity,
            "traits": state.get("traits") or {},
            "dynamic_states": state.get("dynamic") or {},
            "values": values,
            "relationships": state.get("relationships") or {},
        })

    def backup(self) -> Path | None:
        if not self.state_path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = self.state_path.with_name(f"{self.state_path.stem}.backup-{stamp}{self.state_path.suffix}")
        shutil.copy2(self.state_path, backup)
        return backup

    def migrate(self) -> dict[str, Any]:
        """Import legacy identity sources once without deleting their raw data."""
        state = self.load()
        if state.get("migration", {}).get("legacy_identity_imported"):
            repaired = self._repair_legacy_saturation(state)
            return {"migrated": False, "state": repaired["state"], "backup": repaired.get("backup"), "saturation_repair": repaired}

        backup = self.backup()
        self_model = _read_json(self.space_dir / "self_model.json", {})
        personality = _read_json(self.space_dir / "personality_state.json", {})
        legacy = _read_json(self.space_dir / "PERSOENLICHKEIT.json", {})

        if isinstance(self_model, dict):
            identity = self_model.get("identity") or {}
            state["identity"].update({k: v for k, v in identity.items() if k != "hard_safety_boundaries"})
            state["identity"]["values"] = list(self_model.get("values") or state["identity"]["values"])
            state["identity"]["boundaries"] = list(self_model.get("boundaries") or [])
            state["identity"]["long_running_themes"] = list(self_model.get("long_running_themes") or [])
            state["preferences"].update(self_model.get("preferences") or {})
            state["relationships"].update(self_model.get("relationships") or {})
            state["open_conflicts"] = list(self_model.get("open_conflicts") or [])

        if isinstance(personality, dict):
            state["traits"].update(personality.get("traits") or {})
            state["dynamic"].update(personality.get("dynamic_states") or {})
            state["relationships"].update(personality.get("relationship") or {})
            state["runtime"]["autonomy_level"] = int(personality.get("autonomy_level", 2) or 2)

        legacy_identity = legacy.get("identity") if isinstance(legacy, dict) else None
        if isinstance(legacy_identity, dict):
            state["identity"].setdefault("legacy_nature", legacy_identity.get("nature"))
            state["identity"].setdefault("core_drive", legacy_identity.get("core_drive"))

        state["migration"] = {
            "legacy_identity_imported": True,
            "imported_at": _now(),
            "sources": ["self_model.json", "personality_state.json", "PERSOENLICHKEIT.json", "SOUL.md"],
        }
        saved = self.save(state, reason="Imported legacy Nova identity sources into Entity Runtime v2")
        repaired = self._repair_legacy_saturation(saved)
        saved = repaired["state"]
        return {"migrated": True, "state": saved, "backup": str(backup) if backup else None}

    def _repair_legacy_saturation(self, state: dict[str, Any]) -> dict[str, Any]:
        migration = state.setdefault("migration", {})
        if migration.get("saturation_repaired"):
            return {"repaired": False, "state": state, "backup": None, "changes": []}
        changes: list[dict[str, Any]] = []
        for section in ("traits", "dynamic"):
            metrics = state.get(section) or {}
            for name, metric in metrics.items():
                if not isinstance(metric, dict):
                    continue
                try:
                    current = float(metric.get("current"))
                    baseline = float(metric.get("baseline"))
                except (TypeError, ValueError):
                    continue
                # v1 incremented curiosity/focus on every sufficiently long turn,
                # causing artificial saturation unrelated to lived evidence.
                if current >= 0.999 and baseline < 0.95:
                    changes.append({"path": f"{section}.{name}.current", "before": current, "after": baseline})
                    metric["current"] = baseline
                    metric["updated_at"] = _now()
        backup = self.backup() if changes else None
        migration["saturation_repaired"] = True
        migration["saturation_repaired_at"] = _now()
        migration["saturation_changes"] = changes
        saved = self.save(state, reason="Repaired legacy per-turn personality saturation")
        return {"repaired": bool(changes), "state": saved, "backup": str(backup) if backup else None, "changes": changes}
