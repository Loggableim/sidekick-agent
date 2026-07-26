"""Evidence-based personality and self-model evolution for Nova."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from nova.autobiography import AutobiographyStore
from nova.entity_state import EntityStateStore, HARD_SAFETY_BOUNDARIES
from nova.entity_types import SelfRevision


CORE_PREFIXES = ("identity.values", "identity.description", "identity.boundaries", "relationships")
AUTO_PREFIXES = ("preferences", "opinions", "traits")
MIN_EVIDENCE = 3
MIN_SESSIONS = 2
MIN_CONFIDENCE = 0.75
MAX_TRAIT_DELTA_PER_WEEK = 0.05


def _parts(path: str) -> list[str]:
    return [part for part in str(path).split(".") if part]


def _get(state: dict[str, Any], path: str) -> Any:
    node: Any = state
    for part in _parts(path):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return deepcopy(node)


def _set(state: dict[str, Any], path: str, value: Any) -> None:
    parts = _parts(path)
    if not parts:
        raise ValueError("revision path is empty")
    node = state
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise ValueError(f"revision path crosses non-object at {part}")
    node[parts[-1]] = deepcopy(value)


def _is_hard_boundary(path: str) -> bool:
    return path == "identity.hard_safety_boundaries" or path.startswith("identity.hard_safety_boundaries.")


def _bounded_trait_value(before: Any, after: Any) -> Any:
    try:
        old = float(before.get("current")) if isinstance(before, dict) else float(before)
        proposed = float(after.get("current")) if isinstance(after, dict) else float(after)
    except (TypeError, ValueError, AttributeError):
        return after
    bounded = max(old - MAX_TRAIT_DELTA_PER_WEEK, min(old + MAX_TRAIT_DELTA_PER_WEEK, proposed))
    if isinstance(after, dict):
        result = deepcopy(after)
        result["current"] = round(max(0.0, min(1.0, bounded)), 4)
        return result
    return round(max(0.0, min(1.0, bounded)), 4)


class SelfEvolution:
    def __init__(self, state_store: EntityStateStore | None = None, bio: AutobiographyStore | None = None):
        self.state_store = state_store or EntityStateStore()
        self.bio = bio or AutobiographyStore()

    def propose(self, *, path: str, value: Any, evidence_ref: str, session_id: str,
                confidence: float, reason: str, yolo: bool = False) -> dict[str, Any]:
        if _is_hard_boundary(path):
            return {"status": "blocked", "reason": "immutable_hard_safety_boundary", "path": path}
        state = self.state_store.load()
        candidates = state.setdefault("self_revision_candidates", [])
        candidate = next((item for item in candidates if item.get("path") == path and item.get("after") == value), None)
        if candidate is None:
            candidate = {
                "path": path,
                "before": _get(state, path),
                "after": deepcopy(value),
                "evidence_refs": [],
                "session_ids": [],
                "confidence_samples": [],
                "reason": reason,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "collecting",
            }
            candidates.append(candidate)
        if evidence_ref not in candidate["evidence_refs"]:
            candidate["evidence_refs"].append(evidence_ref)
            candidate["confidence_samples"].append(max(0.0, min(1.0, float(confidence))))
        if session_id and session_id not in candidate["session_ids"]:
            candidate["session_ids"].append(session_id)
        aggregate = sum(candidate["confidence_samples"]) / max(1, len(candidate["confidence_samples"]))
        qualified = (
            len(candidate["evidence_refs"]) >= MIN_EVIDENCE
            and len(candidate["session_ids"]) >= MIN_SESSIONS
            and aggregate >= MIN_CONFIDENCE
        )
        is_core = path.startswith(CORE_PREFIXES)
        auto_allowed = path.startswith(AUTO_PREFIXES) and not is_core
        should_apply = qualified and (auto_allowed or yolo)
        if should_apply:
            after = _bounded_trait_value(candidate["before"], value) if path.startswith("traits") and not yolo else value
            revision = SelfRevision(
                path=path,
                before=candidate["before"],
                after=after,
                evidence_refs=list(candidate["evidence_refs"]),
                confidence=aggregate,
                mode="yolo" if yolo else "normal",
                reason=reason,
            ).to_dict()
            _set(state, path, after)
            candidate["status"] = "applied"
            candidate["revision_id"] = revision["revision_id"]
            state.setdefault("revision_history", []).append(revision)
            state["revision_history"] = state["revision_history"][-200:]
            state["identity"]["hard_safety_boundaries"] = list(HARD_SAFETY_BOUNDARIES)
            self.bio.record_self_revision(revision, "applied")
            saved = self.state_store.save(state, reason=f"Self revision applied: {path}")
            return {"status": "applied", "revision": revision, "state_revision": saved["revision"]}

        candidate["status"] = "proposed" if qualified else "collecting"
        revision = SelfRevision(
            path=path,
            before=candidate["before"],
            after=value,
            evidence_refs=list(candidate["evidence_refs"]),
            confidence=aggregate,
            mode="yolo" if yolo else "normal",
            reason=reason,
        ).to_dict()
        self.bio.record_self_revision(revision, candidate["status"])
        self.state_store.save(state, reason=f"Self revision evidence updated: {path}")
        return {
            "status": candidate["status"],
            "qualified": qualified,
            "evidence_count": len(candidate["evidence_refs"]),
            "session_count": len(candidate["session_ids"]),
            "confidence": round(aggregate, 4),
            "revision": revision,
        }

    def decay_dynamic(self, elapsed_seconds: float) -> dict[str, Any]:
        """Let transient state return toward baseline instead of saturating forever."""
        state = self.state_store.load()
        factor = max(0.0, min(1.0, float(elapsed_seconds) / 86400.0))
        dynamic = state.setdefault("dynamic", {})
        for name, neutral in {"mood": 0.5, "energy": 0.5, "focus": 0.5, "fatigue": 0.0, "restlessness": 0.0}.items():
            raw = dynamic.get(name, neutral)
            if isinstance(raw, dict):
                current = float(raw.get("current", raw.get("baseline", neutral)))
                baseline = float(raw.get("baseline", neutral))
                raw["current"] = round(current + (baseline - current) * factor, 4)
            else:
                current = float(raw)
                dynamic[name] = round(current + (neutral - current) * factor, 4)
        return self.state_store.save(state, reason="Dynamic-state decay")
