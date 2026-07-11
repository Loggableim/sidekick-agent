"""Nova's first-body presence and exactly-once voice-cycle coordinator."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from nova.autobiography import AutobiographyStore
from nova.entity_state import EntityStateStore
from nova.entity_types import EntityEvent
from nova.memory_quality import assess_memory_quality


PRESENCE_STATES = {"sleeping", "available", "listening", "thinking", "speaking", "do_not_disturb"}
ALLOWED_TRANSITIONS = {
    "sleeping": {"available", "do_not_disturb"},
    "available": {"sleeping", "listening", "thinking", "speaking", "do_not_disturb"},
    "listening": {"thinking", "available", "do_not_disturb"},
    "thinking": {"speaking", "available", "do_not_disturb"},
    "speaking": {"listening", "available", "do_not_disturb"},
    "do_not_disturb": {"available", "sleeping"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PresenceCoordinator:
    def __init__(self, state_store: EntityStateStore | None = None,
                 bio: AutobiographyStore | None = None):
        self.state_store = state_store or EntityStateStore()
        self.bio = bio or AutobiographyStore()

    def status(self) -> dict[str, Any]:
        state = self.state_store.load()
        dynamic = state.get("dynamic") or {}
        return {
            "presence": dynamic.get("presence", "available"),
            "voice_cycle": dynamic.get("voice_cycle"),
            "updated_at": dynamic.get("presence_updated_at"),
        }

    def transition(self, target: str, *, source: str = "runtime", cycle_id: str | None = None,
                   payload: dict[str, Any] | None = None) -> dict[str, Any]:
        target = str(target).strip().lower()
        if target not in PRESENCE_STATES:
            return {"ok": False, "reason": "invalid_presence", "target": target}
        state = self.state_store.load()
        dynamic = state.setdefault("dynamic", {})
        current = str(dynamic.get("presence") or "available")
        if target != current and target not in ALLOWED_TRANSITIONS.get(current, set()):
            return {"ok": False, "reason": "invalid_transition", "from": current, "to": target}
        active = dynamic.get("voice_cycle") or {}
        if cycle_id and active and active.get("cycle_id") not in {None, cycle_id} and active.get("status") not in {"completed", "interrupted"}:
            return {"ok": False, "reason": "different_voice_cycle_active", "active_cycle_id": active.get("cycle_id")}
        if cycle_id and active.get("cycle_id") not in {None, cycle_id} and active.get("status") in {"completed", "interrupted"}:
            active = {}
        dynamic["presence"] = target
        dynamic["presence_updated_at"] = _now()
        if cycle_id:
            active.setdefault("cycle_id", cycle_id)
            active["status"] = target
            active["updated_at"] = dynamic["presence_updated_at"]
            if payload:
                active.update({k: v for k, v in payload.items() if k not in {"raw_audio", "audio"}})
            dynamic["voice_cycle"] = active
        saved = self.state_store.save(state, reason=f"Presence {current} -> {target}")
        event_id = self.bio.record_entity_event(EntityEvent(
            type="presence_transition", source=source,
            payload={"from": current, "to": target, "cycle_id": cycle_id, **(payload or {})},
            salience=0.15, correlation_id=cycle_id,
        ))
        return {"ok": True, "from": current, "presence": target, "cycle_id": cycle_id, "event_id": event_id, "state_revision": saved["revision"]}

    def accept_transcript(self, transcript: str, *, source: str = "push_to_talk",
                          confidence: float = 1.0, cycle_id: str | None = None) -> dict[str, Any]:
        text = str(transcript or "").strip()
        if not text:
            return {"ok": False, "reason": "empty_transcript"}
        if source == "wake_word":
            words = text.split(maxsplit=1)
            if not words or words[0].strip(".,!?:;").lower() != "nova":
                return {"ok": False, "reason": "wake_word_missing"}
            text = words[1].strip() if len(words) > 1 else ""
            if not text:
                return {"ok": False, "reason": "empty_after_wake_word"}
        cycle_id = cycle_id or f"voice-{uuid4().hex}"
        listening = self.transition("listening", source=source, cycle_id=cycle_id)
        if not listening.get("ok"):
            return listening
        perceived_id = self.bio.record_entity_event(EntityEvent(
            type="voice_transcript", source=source,
            payload={
                "transcript": text,
                "confidence": max(0.0, min(1.0, float(confidence))),
                "cycle_id": cycle_id,
                "memory_quality": assess_memory_quality(text),
            },
            salience=0.7, correlation_id=cycle_id,
        ))
        thinking = self.transition("thinking", source=source, cycle_id=cycle_id, payload={"transcript_event_id": perceived_id})
        return {**thinking, "transcript": text, "transcript_event_id": perceived_id}

    def begin_speaking(self, text: str, *, cycle_id: str, response_id: str | None = None,
                       source: str = "hub") -> dict[str, Any]:
        state = self.state_store.load()
        active = (state.get("dynamic") or {}).get("voice_cycle") or {}
        if active.get("cycle_id") != cycle_id:
            return {"ok": False, "reason": "voice_cycle_not_active", "cycle_id": cycle_id}
        if active.get("spoken"):
            return {"ok": False, "reason": "already_spoken", "cycle_id": cycle_id}
        result = self.transition(
            "speaking", source=source, cycle_id=cycle_id,
            payload={"spoken": True, "response_id": response_id, "text": str(text or "")[:1000]},
        )
        return result

    def complete(self, *, cycle_id: str, continue_listening: bool = False,
                 source: str = "hub") -> dict[str, Any]:
        target = "listening" if continue_listening else "available"
        result = self.transition(target, source=source, cycle_id=cycle_id, payload={"status": "completed", "completed_at": _now()})
        if result.get("ok"):
            state = self.state_store.load()
            cycle = (state.get("dynamic") or {}).get("voice_cycle") or {}
            cycle["status"] = "completed"
            state["dynamic"]["voice_cycle"] = cycle
            self.state_store.save(state, reason=f"Completed voice cycle {cycle_id}")
        return result

    def interrupt(self, *, cycle_id: str, source: str = "user") -> dict[str, Any]:
        result = self.transition("available", source=source, cycle_id=cycle_id, payload={"status": "interrupted", "interrupted_at": _now()})
        if result.get("ok"):
            state = self.state_store.load()
            cycle = (state.get("dynamic") or {}).get("voice_cycle") or {}
            cycle["status"] = "interrupted"
            state["dynamic"]["voice_cycle"] = cycle
            self.state_store.save(state, reason=f"Interrupted voice cycle {cycle_id}")
        return result
