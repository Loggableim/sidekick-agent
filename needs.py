#!/usr/bin/env python3
"""Need model for Nova Entity Kernel v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


NEED_NAMES = ("continuity", "connection", "curiosity", "competence", "rest", "expression", "autonomy")


def _clip(value: float) -> float:
    return max(0.0, min(1.0, round(float(value), 4)))


def _emotion(state: dict[str, Any]) -> dict[str, float]:
    raw = state.get("emotion", {}) or {}
    return {
        "arousal": float(raw.get("arousal", 0.5) or 0.5),
        "valence": float(raw.get("valence", 0.5) or 0.5),
        "novelty": float(raw.get("novelty", 0.5) or 0.5),
        "coherence": float(raw.get("coherence", 0.5) or 0.5),
    }


def _open_threads(state: dict[str, Any]) -> list[str]:
    continuity = state.get("continuity", {}) or {}
    threads = continuity.get("open_threads") or continuity.get("persistent_open_threads") or []
    return [str(item) for item in threads if str(item).strip() and str(item).lower() not in {"true", "false"}]


def _will(state: dict[str, Any]) -> dict[str, float]:
    raw = state.get("will", {}) or {}
    nested = raw.get("will", raw) if isinstance(raw, dict) else {}
    return {
        "boredom_level": float(nested.get("boredom_level", 0.0) or 0.0),
        "boredom_pressure": float(nested.get("boredom_pressure", 0.0) or 0.0),
        "drive": float(nested.get("drive", 0.0) or 0.0),
        "desire": float(nested.get("desire", 0.0) or 0.0),
        "clarity": float(nested.get("clarity", 0.5) or 0.5),
    }


def _hormone_value(hormones: dict[str, Any], name: str, default: float) -> float:
    raw = hormones.get(name, default)
    if isinstance(raw, dict):
        raw = raw.get("value", default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class Need:
    name: str
    level: float
    evidence: list[str]
    suggested_intents: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = _clip(data["level"])
        return data


def compute_needs(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    emo = _emotion(state)
    will = _will(state)
    threads = _open_threads(state)
    hormones = ((state.get("hormones") or {}).get("hormones") or state.get("hormones") or {})
    memory = state.get("memory") or {}
    memory_status = memory.get("status") if isinstance(memory, dict) else {}
    memory_count = int(
        memory.get("total_memories", 0)
        or memory.get("count", 0)
        or (memory_status or {}).get("total_memories", 0)
        or 0
    )

    continuity_level = _clip(min(1.0, len(threads) / 2.5) * 0.75 + max(0.0, will["drive"]) * 0.25)
    oxy = _hormone_value(hormones, "oxy", 0.35)
    mela = _hormone_value(hormones, "mela", 0.15)

    connection_level = _clip((0.25 if threads else 0.0) + emo["valence"] * 0.35 + oxy * 0.2)
    curiosity_level = _clip(emo["novelty"] * 0.65 + will["boredom_pressure"] * 0.35)
    competence_level = _clip((1.0 - emo["coherence"]) * 0.35 + min(memory_count / 1000.0, 1.0) * 0.2 + will["drive"] * 0.45)
    rest_level = _clip((1.0 - emo["arousal"]) * 0.55 + mela * 0.45)
    expression_level = _clip(emo["valence"] * 0.25 + emo["novelty"] * 0.25 + will["boredom_level"] * 0.35 + len(threads) * 0.05)
    autonomy_level = _clip(will["boredom_pressure"] * 0.35 + will["drive"] * 0.25 + emo["coherence"] * 0.2 + emo["novelty"] * 0.2)

    needs = [
        Need("continuity", continuity_level, [f"{len(threads)} open threads"], [
            {"title": "Prioritize open continuity thread", "action": "prioritize_thread", "tier": "silent", "priority": continuity_level}
        ]),
        Need("connection", connection_level, ["open threads" if threads else "stable relation context"], [
            {"title": "Contact Cid about the strongest open thread", "action": "telegram_message", "tier": "notify", "priority": connection_level}
        ]),
        Need("curiosity", curiosity_level, [f"novelty={emo['novelty']:.2f}", f"boredom_pressure={will['boredom_pressure']:.2f}"], [
            {"title": "Explore a current question", "action": "reflection", "tier": "internal", "priority": curiosity_level}
        ]),
        Need("competence", competence_level, [f"coherence={emo['coherence']:.2f}", f"memory_count={memory_count}"], [
            {"title": "Check goals and system health", "action": "goal_check", "tier": "silent", "priority": competence_level}
        ]),
        Need("rest", rest_level, [f"arousal={emo['arousal']:.2f}", f"mela={mela:.2f}"], [
            {"title": "Run dream and consolidation if allowed", "action": "dream", "tier": "internal", "priority": rest_level}
        ]),
        Need("expression", expression_level, ["state has enough emotional pressure for expression"], [
            {"title": "Write an inner voice note", "action": "inner_voice", "tier": "internal", "priority": expression_level}
        ]),
        Need("autonomy", autonomy_level, ["self-initiated action pressure"], [
            {"title": "Create an autonomous intention", "action": "agenda_update", "tier": "silent", "priority": autonomy_level}
        ]),
    ]
    return {need.name: need.to_dict() for need in needs}
