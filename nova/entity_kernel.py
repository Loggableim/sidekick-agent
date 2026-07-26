#!/usr/bin/env python3
"""Canonical Nova Entity Runtime v2.

One auditable loop owns perception, state, needs, intents, governance, actions,
outcomes, reflection, and the fast status projection.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nova.actions import ActionRegistry
from nova.agenda import AgendaStore
from nova.autobiography import AutobiographyStore
from nova.autonomy_policy import AutonomyPolicy
from nova.entity_state import EntityStateStore
from nova.entity_types import EntityEvent, Intent, Outcome
from nova.experience_learning import extract_self_evidence
from nova.memory_quality import assess_memory_quality
from nova.needs import compute_needs
from nova.paths import get_nova_data_dir, get_nova_space_root
from nova.reflection_worker import ReflectionWorker
from nova.self_evolution import SelfEvolution
from nova.soak_monitor import SoakMonitor
from nova.runtime_utf8 import configure_utf8_stdio

try:
    from nova.state_snapshot import collect_snapshot
except Exception:  # pragma: no cover - degraded runtime fallback
    collect_snapshot = None


PYTHON = sys.executable


class EntityKernel:
    def __init__(self, space_dir: Path | None = None,
                 state_provider: Callable[[], dict[str, Any]] | None = None,
                 action_registry: ActionRegistry | None = None):
        self.space_dir = Path(space_dir).resolve() if space_dir is not None else get_nova_space_root()
        self.state_provider = state_provider
        data_dir = (self.space_dir / "nova_data" / "entity") if space_dir is not None else get_nova_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.agenda = AgendaStore(data_dir / "agenda.json")
        self.bio = AutobiographyStore(data_dir / "autobiography.db")
        self.entity_state = EntityStateStore(data_dir / "entity_state.json", self.space_dir)
        policy_path = self.space_dir / "autonomy_policy.json"
        bundled_policy = Path(__file__).parent / "autonomy_policy.json"
        self.policy = AutonomyPolicy(policy_path if policy_path.exists() else bundled_policy)
        self.actions = action_registry or ActionRegistry(self.space_dir)
        self.reflections = ReflectionWorker(self.space_dir, self.bio)
        self.self_evolution = SelfEvolution(self.entity_state, self.bio)
        self.soak = SoakMonitor(data_dir / "soak_v2.json")

    def sample_soak(self) -> dict[str, Any]:
        state = self.entity_state.load()
        mind_count = 0
        try:
            import psutil

            for process in psutil.process_iter(["cmdline"]):
                command = " ".join(process.info.get("cmdline") or []).lower()
                if "nova_mind.py" in command and "nova_mind_watchdog.py" not in command:
                    mind_count += 1
        except Exception:
            mind_count = -1
        queue = self.reflections.status()
        soak_state = self.soak.status()
        started_at = str(soak_state.get("started_at") or "")
        events = self.bio.recent(2000)
        if started_at:
            events = [item for item in events if str(item.get("timestamp") or "") >= started_at]
        action_counts = Counter(
            str(item.get("correlation_id") or "") for item in events
            if item.get("type") == "action" and item.get("correlation_id")
        )
        voice_counts = Counter()
        raw_audio_events = 0
        for item in events:
            payload = item.get("payload") or {}
            if "raw_audio" in payload or "audio" in payload:
                raw_audio_events += 1
            if item.get("type") != "presence_transition" or payload.get("to") != "speaking":
                continue
            cycle_id = str(item.get("correlation_id") or payload.get("cycle_id") or "")
            response_id = str(payload.get("response_id") or "")
            if response_id:
                voice_counts[(cycle_id, response_id)] += 1
        return self.soak.sample(
            state_revision=int(state.get("revision", 0)),
            mind_process_count=mind_count,
            reflection_queue_depth=int(queue.get("queued", queue.get("depth", 0)) or 0),
            duplicate_action_correlations=sum(count - 1 for count in action_counts.values() if count > 1),
            duplicate_voice_responses=sum(count - 1 for count in voice_counts.values() if count > 1),
            raw_audio_events=raw_audio_events,
        )

    def migrate(self) -> dict[str, Any]:
        result = self.entity_state.migrate()
        legacy_db = self.space_dir / "nova_data" / "entity_kernel" / "autobiography.db"
        result["autobiography_import"] = self.bio.import_legacy_db(legacy_db)
        result["legacy_autobiography_preserved"] = str(legacy_db) if legacy_db.exists() else None
        return result

    def _yolo_enabled(self) -> bool:
        try:
            payload = json.loads((self.space_dir / ".lifecycle" / "yolo.json").read_text(encoding="utf-8"))
            return bool(payload.get("enabled", False)) if isinstance(payload, dict) else False
        except (OSError, ValueError, TypeError):
            return False

    def scan(self) -> dict[str, Any]:
        if self.state_provider:
            snapshot = self.state_provider()
        elif collect_snapshot is not None:
            try:
                snapshot = collect_snapshot(mutate=False)
            except Exception as exc:
                snapshot = {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "scan_error": repr(exc)}
        else:
            script = self.space_dir / "session_start.py"
            try:
                result = subprocess.run(
                    [PYTHON, str(script), "compact"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=45,
                )
                snapshot = {
                    "emotion": {}, "continuity": {}, "memory": {}, "will": {},
                    "rendered_context": result.stdout[:4000], "scan_error": result.stderr[:300],
                }
            except Exception as exc:
                snapshot = {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "scan_error": repr(exc)}
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.setdefault("emotion", {})
        snapshot.setdefault("continuity", {})
        snapshot.setdefault("memory", {})
        snapshot.setdefault("will", {})
        snapshot["entity"] = self.entity_state.load()
        return snapshot

    def perceive(self, event: EntityEvent | dict[str, Any]) -> dict[str, Any]:
        data = event.to_dict() if isinstance(event, EntityEvent) else dict(event)
        if "event_id" not in data:
            data = EntityEvent(
                type=str(data.get("type") or "perception"),
                source=str(data.get("source") or "unknown"),
                payload=dict(data.get("payload") or {}),
                salience=float(data.get("salience", 0.5)),
                visibility=str(data.get("visibility") or "private"),
                correlation_id=data.get("correlation_id"),
            ).to_dict() | {k: v for k, v in data.items() if k not in {"type", "source", "payload", "salience", "visibility", "correlation_id"}}
        payload = data.setdefault("payload", {})
        if data.get("type") in {"chat_turn", "voice_transcript"}:
            quality = assess_memory_quality(str(payload.get("user") or payload.get("transcript") or ""), str(payload.get("assistant") or ""))
            payload["memory_quality"] = quality
            if not quality["high_quality"]:
                data["salience"] = min(float(data.get("salience", 0.5)), 0.1)
                data.setdefault("tags", []).append("low-signal")
        event_id = self.bio.record_entity_event(data)
        state = self.entity_state.load()
        state["runtime"]["last_event_id"] = event_id
        presence = payload.get("presence")
        if presence:
            state["dynamic"]["presence"] = presence
        saved = self.entity_state.save(state, reason=f"Perceived {data.get('type')}")
        return {"event_id": event_id, "quality": payload.get("memory_quality"), "state_revision": saved["revision"]}

    def learn_from_experience(self, *, user_text: str, assistant_text: str,
                              event_id: str, session_id: str) -> dict[str, Any]:
        quality = assess_memory_quality(user_text, assistant_text)
        if not quality["eligible_for_personality"]:
            return {"eligible": False, "quality": quality, "revisions": []}
        revisions = []
        for evidence in extract_self_evidence(user_text, assistant_text):
            revisions.append(self.self_evolution.propose(
                path=evidence["path"], value=evidence["value"], evidence_ref=event_id,
                session_id=session_id, confidence=float(evidence["confidence"]),
                reason=evidence["reason"], yolo=self._yolo_enabled(),
            ))
        return {"eligible": True, "quality": quality, "revisions": revisions}

    def _sync_dynamic_projection(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        state = self.entity_state.load()
        dynamic = state.setdefault("dynamic", {})
        emotion = snapshot.get("emotion") or {}
        will = snapshot.get("will") or {}
        nested_will = will.get("will", will) if isinstance(will, dict) else {}
        values = {
            "mood": float(emotion.get("valence", 0.5) or 0.5),
            "energy": float(emotion.get("arousal", 0.5) or 0.5),
            "focus": float(emotion.get("coherence", 0.5) or 0.5),
            "restlessness": float(nested_will.get("boredom_pressure", nested_will.get("restlessness", 0.0)) or 0.0),
        }
        changed = False
        for name, value in values.items():
            value = round(max(0.0, min(1.0, value)), 4)
            current = dynamic.get(name)
            if isinstance(current, dict):
                if current.get("current") != value:
                    current["current"] = value
                    current["updated_at"] = datetime.now(timezone.utc).isoformat()
                    changed = True
            elif current != value:
                dynamic[name] = value
                changed = True
        return self.entity_state.save(state, reason="Synchronized dynamic state") if changed else state

    def _history(self) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for item in self.bio.recent(limit=200):
            if item.get("type") != "action":
                continue
            payload = item.get("payload") or {}
            history.append({"action": payload.get("action") or item.get("action"), "timestamp": item.get("timestamp")})
        return history

    @staticmethod
    def _open_threads(state: dict[str, Any]) -> list[Any]:
        continuity = state.get("continuity") or {}
        threads = continuity.get("open_threads") or continuity.get("persistent_open_threads") or []
        if not isinstance(threads, list):
            return []
        recent_ids: set[str] = set()
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        priority_items = [continuity.get("prioritized_thread"), *(continuity.get("prioritized_history") or [])]
        for item in priority_items:
            if not isinstance(item, dict):
                continue
            try:
                timestamp = datetime.fromisoformat(str(item.get("updated_at") or "").replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp.timestamp() >= cutoff:
                    recent_ids.add(str(item.get("thread_id") or item.get("topic") or ""))
            except ValueError:
                continue
        available = []
        for index, thread in enumerate(threads):
            target = EntityKernel._thread_target(thread, index)
            if target["thread_id"] not in recent_ids:
                available.append(thread)
        return available

    @staticmethod
    def _thread_target(thread: Any, index: int) -> dict[str, Any]:
        if isinstance(thread, dict):
            topic = str(thread.get("topic") or thread.get("title") or thread.get("id") or f"thread-{index}")
            return {"thread_id": str(thread.get("id") or topic), "topic": topic}
        topic = str(thread)
        return {"thread_id": topic, "topic": topic}

    def _candidate_intents(self, state: dict[str, Any], needs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        threads = self._open_threads(state)
        for need_name, need in needs.items():
            for suggested in need.get("suggested_intents", []):
                action = str(suggested["action"])
                target: dict[str, Any] = {}
                payload: dict[str, Any] = {}
                expected: dict[str, Any] = {}
                if action in {"prioritize_thread", "telegram_message"} and threads:
                    target = self._thread_target(threads[0], 0)
                    payload["next_step"] = f"Resume and resolve: {target['topic']}"
                    expected = {"thread_id": target["thread_id"], "effect": "thread_prioritized"}
                elif action == "prioritize_thread" and not threads:
                    continue
                elif action == "reflection":
                    expected = {"effect": "reflection_persisted"}
                elif action == "goal_check":
                    expected = {"effect": "goals_checked"}
                elif action == "dream":
                    expected = {"effect": "dream_cycle_completed"}
                priority = max(float(need.get("level", 0.0)), float(suggested.get("priority", 0.0)))
                candidates.append(Intent(
                    need=need_name,
                    title=str(suggested["title"]),
                    why="; ".join(str(item) for item in need.get("evidence", [])),
                    action=action,
                    target=target,
                    payload=payload,
                    expected_outcome=expected,
                    priority=round(priority, 4),
                    policy_tier=str(suggested.get("tier", "silent")),
                ).to_dict())
        candidates.sort(key=lambda item: float(item["priority"]), reverse=True)
        return candidates

    def _govern_intent(self, intent: dict[str, Any], *, now: datetime,
                       state: dict[str, Any], needs: dict[str, Any]) -> dict[str, Any]:
        entity = state.get("entity") or self.entity_state.load()
        yolo = self._yolo_enabled()
        level = 3 if yolo else int((entity.get("runtime") or {}).get("autonomy_level", 2) or 2)
        policy = self.policy.check(intent, now=now, history=self._history(), autonomy_level=level, yolo_enabled=yolo)
        return {
            "timestamp": now.isoformat(), "state": state, "needs": needs,
            "intent": intent, "policy": policy,
            "autonomy": {"level": level, "yolo_enabled": yolo},
        }

    def govern(self, proposal: dict[str, Any], now_iso: str | None = None,
               persist_agenda: bool = True) -> dict[str, Any]:
        now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
        state = self.scan()
        state["entity"] = self._sync_dynamic_projection(state)
        needs = compute_needs(state)
        data = dict(proposal)
        if not data.get("intent_id") and not data.get("id"):
            data = Intent(
                need=str(data.get("need") or "autonomy"),
                action=str(data.get("action") or ""),
                title=str(data.get("title") or data.get("action") or "Nova proposal"),
                why=str(data.get("why") or "Nova proposed this action."),
                target=dict(data.get("target") or {}),
                payload=dict(data.get("payload") or {}),
                expected_outcome=dict(data.get("expected_outcome") or {}),
                evidence_refs=list(data.get("evidence_refs") or []),
                priority=float(data.get("priority", 0.5)),
                policy_tier=str(data.get("policy_tier") or data.get("tier") or "silent"),
                source=str(data.get("source") or "nova_mind"),
            ).to_dict()
        if persist_agenda:
            data.update(self.agenda.upsert(data))
        return self._govern_intent(data, now=now, state=state, needs=needs)

    def decide(self, now_iso: str | None = None, persist_agenda: bool = True) -> dict[str, Any]:
        now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
        state = self.scan()
        state["entity"] = self._sync_dynamic_projection(state)
        needs = compute_needs(state)
        candidates = self._candidate_intents(state, needs)
        if persist_agenda:
            for candidate in candidates:
                self.agenda.upsert(candidate)
            intent = self.agenda.best_intent()
        else:
            intent = candidates[0] if candidates else None
        if intent is None:
            intent = Intent(
                need="autonomy", action="agenda_update", title="Maintain the agenda",
                why="No open intent is currently actionable.", expected_outcome={"effect": "agenda_checkpoint"},
                priority=0.05, policy_tier="silent",
            ).to_dict()
        return self._govern_intent(intent, now=now, state=state, needs=needs)

    def tick(self, dry_run: bool = False, now_iso: str | None = None) -> dict[str, Any]:
        decision = self.decide(now_iso=now_iso, persist_agenda=not dry_run)
        if dry_run:
            return {"mode": "dry-run", "executed": False, "decision": decision}
        return self.act(decision)

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        intent = dict(decision["intent"])
        intent["yolo_enabled"] = bool((decision.get("autonomy") or {}).get("yolo_enabled", False))
        policy = decision["policy"]
        intent_id = str(intent.get("intent_id") or intent.get("id") or "")
        correlation_id = str(intent.get("correlation_id") or intent_id)
        if not policy.get("allowed"):
            if intent.get("id"):
                try:
                    self.agenda.mark_result(str(intent["id"]), "blocked", policy)
                except KeyError:
                    pass
            event_id = self.bio.record_entity_event({
                "type": "decision", "source": "entity_kernel", "title": "Intent blocked by policy",
                "summary": str(intent.get("title") or ""), "why": str(policy.get("reason") or ""),
                "salience": 0.4, "intent_id": intent_id, "correlation_id": correlation_id,
                "payload": {"action": intent.get("action"), "policy": policy},
                "tags": ["entity_kernel", "blocked", f"need:{intent.get('need', 'autonomy')}"],
            })
            return {"executed": False, "reason": policy.get("reason"), "event_id": event_id, "decision": decision}

        result = self.actions.execute(intent, decision.get("state") or {})
        status = "done" if result.get("ok") else "blocked"
        if intent.get("id"):
            try:
                self.agenda.mark_result(str(intent["id"]), status, result)
            except KeyError:
                pass
        outcome = Outcome(
            intent_id=intent_id,
            correlation_id=correlation_id,
            status="succeeded" if result.get("ok") else str(result.get("status") or "failed"),
            effects=result.get("effects") or {},
        )
        outcome_id = self.bio.record_outcome(outcome)
        event_id = self.bio.record_entity_event({
            "type": "action" if result.get("ok") else "decision", "source": "entity_kernel",
            "title": str(intent.get("title") or "Autonomous action"),
            "summary": str(result.get("message") or ""), "why": str(intent.get("why") or ""),
            "salience": float(intent.get("priority", 0.5)), "intent_id": intent_id,
            "correlation_id": correlation_id,
            "payload": {
                "action": intent.get("action"), "target": intent.get("target") or {},
                "expected_outcome": intent.get("expected_outcome") or {},
                "result": result, "outcome_id": outcome_id,
            },
            "tags": ["entity_kernel", str(intent.get("action")), f"need:{intent.get('need', 'autonomy')}"],
        })
        state = self.entity_state.load()
        state["runtime"]["last_event_id"] = event_id
        state["runtime"]["last_intent_id"] = intent_id
        state["runtime"]["last_outcome_id"] = outcome_id
        self.entity_state.save(state, reason=f"Completed intent {intent_id}")
        return {
            "executed": bool(result.get("ok")), "result": result, "outcome_id": outcome_id,
            "event_id": event_id, "decision": decision,
        }

    def reflect(self, scope: str = "daily") -> dict[str, Any]:
        limit = 200 if scope == "backlog" else 25
        result = self.reflections.drain(limit=limit, compact_backlog_threshold=100 if scope == "backlog" else 10_000)
        state = self.entity_state.load()
        now = datetime.now(timezone.utc)
        recent = self.bio.recent(100 if scope == "weekly" else 30)
        action_counts = Counter(
            str((item.get("payload") or {}).get("action") or "")
            for item in recent if item.get("type") == "action"
        )
        reward_counts = Counter(
            str(item.get("summary") or "") for item in recent if item.get("type") == "reward"
        )
        payload = {
            "scope": scope,
            "queue": result,
            "recent_event_count": len(recent),
            "action_patterns": {key: value for key, value in action_counts.most_common(8) if key},
            "reward_patterns": dict(reward_counts.most_common(8)),
            "open_intentions": [item.get("title") for item in self.agenda.list_open()[:8]],
            "self_revision_candidates": len(state.get("self_revision_candidates") or []),
        }
        correlation = f"reflection-{scope}-{now.date().isoformat()}"
        reflection_id = self.bio.record_entity_event({
            "type": "reflection", "source": "entity_kernel",
            "title": f"{scope.title()} coherence reflection",
            "summary": (
                f"Reviewed {len(recent)} recent events, {sum(action_counts.values())} actions, "
                f"and {len(state.get('self_revision_candidates') or [])} self-revision candidates."
            ),
            "why": "Maintain continuity and let outcomes shape future priorities.",
            "salience": 0.7 if scope == "weekly" else 0.55,
            "correlation_id": correlation, "payload": payload,
            "tags": ["reflection", scope, "entity-runtime"],
        })
        state["runtime"]["last_reflection_at"] = now.isoformat()
        state["runtime"]["last_event_id"] = reflection_id
        self.entity_state.save(state, reason=f"Completed {scope} reflection")
        return {"scope": scope, "reflection_event_id": reflection_id, "coherence": payload, **result}

    def status(self) -> dict[str, Any]:
        state = self.entity_state.load()
        runtime = state.get("runtime") or {}
        pid_file = self.space_dir / "nova_data" / "runtime" / "nova_mind.pid.json"
        try:
            pid = int(json.loads(pid_file.read_text(encoding="utf-8")).get("pid"))
        except (OSError, ValueError, TypeError):
            pid = None
        running = False
        if pid:
            try:
                import psutil

                process = psutil.Process(pid)
                running = process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            except Exception:
                running = False
        yolo = self._yolo_enabled()
        game_mode = False
        try:
            settings = self.space_dir.parents[1] / "state" / "webui" / "settings.json"
            locks = [settings.parent / "game_mode.lock", settings.parent.parent / "game_mode.lock"]
            game_mode = any(path.exists() for path in locks) or (settings.exists() and bool(json.loads(settings.read_text(encoding="utf-8")).get("game_mode_enabled")))
        except (OSError, ValueError, TypeError):
            game_mode = False
        return {
            "ok": True,
            "schema_version": state.get("schema_version"),
            "state_revision": state.get("revision"),
            "presence": (state.get("dynamic") or {}).get("presence", "available"),
            "autonomy": {"level": 3 if yolo else runtime.get("autonomy_level", 2), "yolo_enabled": yolo},
            "mind": {
                "pid": pid, "running": running,
                "model_route": "ollama-cloud:deepseek-v4-flash" if game_mode else "local-registry",
                "game_mode": game_mode,
            },
            "reflection_queue": self.reflections.status(),
            "soak": self.soak.status(),
            "last_event_id": runtime.get("last_event_id"),
            "last_intent_id": runtime.get("last_intent_id"),
            "last_outcome_id": runtime.get("last_outcome_id"),
            "last_reflection_at": runtime.get("last_reflection_at"),
            "paths": {"state": str(self.entity_state.state_path), "events": str(self.bio.db_path)},
        }


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Nova Entity Runtime v2")
    parser.add_argument("command", choices=["migrate", "scan", "decide", "tick", "act", "reflect", "status"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scope", choices=["daily", "weekly", "backlog"], default="daily")
    args = parser.parse_args()
    kernel = EntityKernel()
    if args.command == "migrate":
        output = kernel.migrate()
    elif args.command == "scan":
        output = kernel.scan()
    elif args.command == "decide":
        output = kernel.decide(persist_agenda=not args.dry_run)
    elif args.command == "tick":
        output = kernel.tick(dry_run=args.dry_run)
    elif args.command == "act":
        output = kernel.act(kernel.decide())
    elif args.command == "reflect":
        output = kernel.reflect(args.scope)
    else:
        output = kernel.status()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
