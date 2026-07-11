#!/usr/bin/env python3
"""Nova Entity Kernel v1."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agenda import AgendaStore
from autonomy_policy import AutonomyPolicy
from autobiography import AutobiographyStore
from needs import compute_needs
from notification_gate import NotificationGate
from runtime_utf8 import configure_utf8_stdio

try:
    from state_snapshot import collect_snapshot
except Exception:
    collect_snapshot = None

HERE = Path(__file__).parent.resolve()
PYTHON = sys.executable


class EntityKernel:
    def __init__(self, space_dir: Path = HERE, state_provider: Callable[[], dict[str, Any]] | None = None):
        self.space_dir = Path(space_dir)
        self.state_provider = state_provider
        data_dir = self.space_dir / "nova_data" / "entity_kernel"
        self.agenda = AgendaStore(data_dir / "agenda.json")
        self.bio = AutobiographyStore(data_dir / "autobiography.db")
        self.policy = AutonomyPolicy(self.space_dir / "autonomy_policy.json")

    def _yolo_enabled(self) -> bool:
        """Read the WebUI-controlled, persistent Nova autonomy override."""
        try:
            payload = json.loads((self.space_dir / ".lifecycle" / "yolo.json").read_text(encoding="utf-8"))
            return bool(payload.get("enabled", False)) if isinstance(payload, dict) else False
        except (OSError, ValueError, TypeError):
            return False

    def _self_model(self) -> dict[str, Any]:
        try:
            payload = json.loads((self.space_dir / "self_model.json").read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _record_self_model_update(self, intent: dict[str, Any], result: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
        """Keep identity changes reviewable; YOLO may apply a reflection directly."""
        if intent.get("action") not in {"reflection", "inner_voice", "mind_diary"}:
            return None
        path = self.space_dir / "self_model.json"
        model = self._self_model()
        if not model:
            return None
        candidate = {
            "timestamp": datetime.now().isoformat(),
            "source": str(intent.get("source") or "entity_kernel"),
            "intent_id": intent.get("id"),
            "summary": str(result.get("message") or intent.get("title") or "Nova reflection"),
            "status": "applied" if policy.get("bypassed") else "proposed",
        }
        if policy.get("bypassed"):
            model.setdefault("revision_history", []).append({
                "timestamp": candidate["timestamp"],
                "reason": f"YOLO self-model update: {candidate['summary']}",
                "source": candidate["source"],
            })
        else:
            model.setdefault("self_model_candidates", []).append(candidate)
        path.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return candidate

    def scan(self) -> dict[str, Any]:
        if self.state_provider:
            snapshot = self.state_provider()
            if isinstance(snapshot, dict):
                snapshot.setdefault("self_model", self._self_model())
            return snapshot
        if collect_snapshot is not None:
            try:
                snapshot = collect_snapshot(mutate=False)
                if isinstance(snapshot, dict):
                    snapshot.setdefault("self_model", self._self_model())
                    return snapshot
            except Exception as exc:
                return {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "scan_error": repr(exc)}
        script = self.space_dir / "session_start.py"
        result = subprocess.run([PYTHON, str(script), "compact"], capture_output=True, text=True, encoding="utf-8", timeout=45)
        return {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "self_model": self._self_model(), "rendered_context": result.stdout[:4000], "scan_error": result.stderr[:300]}

    def _history(self) -> list[dict[str, Any]]:
        return [
            {"action": item.get("action"), "timestamp": item.get("timestamp")}
            for item in self.bio.recent(limit=100)
            if item.get("type") == "action"
        ]

    def _candidate_intents(self, needs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for need_name, need in needs.items():
            for suggested in need.get("suggested_intents", []):
                priority = max(float(need.get("level", 0.0)), float(suggested.get("priority", 0.0)))
                candidates.append({
                    "need": need_name,
                    "title": suggested["title"],
                    "why": "; ".join(str(item) for item in need.get("evidence", [])),
                    "action": suggested["action"],
                    "tier": suggested.get("tier", "silent"),
                    "priority": round(priority, 4),
                })
        candidates.sort(key=lambda item: item["priority"], reverse=True)
        return candidates

    def _govern_intent(self, intent: dict[str, Any], *, now: datetime, state: dict[str, Any], needs: dict[str, Any]) -> dict[str, Any]:
        if self._yolo_enabled():
            policy = {
                "allowed": True,
                "tier": str(intent.get("tier") or "yolo"),
                "reason": "nova_yolo_override",
                "requires_approval": False,
                "bypassed": True,
            }
        else:
            policy = self.policy.check(intent, now=now, history=self._history())
        return {"timestamp": now.isoformat(), "state": state, "needs": needs, "intent": intent, "policy": policy}

    def govern(self, proposal: dict[str, Any], now_iso: str | None = None, persist_agenda: bool = True) -> dict[str, Any]:
        """Turn a Nova Mind proposal into the authoritative governance decision."""
        now = datetime.fromisoformat(now_iso) if now_iso else datetime.now()
        state = self.scan()
        needs = compute_needs(state)
        intent = dict(proposal)
        intent.setdefault("source", "nova_mind")
        intent.setdefault("need", "autonomy")
        intent.setdefault("priority", 0.5)
        intent.setdefault("title", str(intent.get("action") or "Nova Mind proposal"))
        intent.setdefault("why", "Nova Mind proposed this action.")
        if persist_agenda:
            saved = self.agenda.upsert_intent(
                need=str(intent["need"]),
                title=str(intent["title"]),
                why=str(intent["why"]),
                action=str(intent.get("action") or ""),
                priority=float(intent["priority"]),
                tier=str(intent.get("tier") or "silent"),
                source=str(intent["source"]),
            )
            intent.update(saved)
        return self._govern_intent(intent, now=now, state=state, needs=needs)

    def decide(self, now_iso: str | None = None, persist_agenda: bool = True) -> dict[str, Any]:
        now = datetime.fromisoformat(now_iso) if now_iso else datetime.now()
        state = self.scan()
        needs = compute_needs(state)
        candidates = self._candidate_intents(needs)
        if persist_agenda:
            for candidate in candidates:
                self.agenda.upsert_intent(**candidate)
            intent = self.agenda.best_intent()
        else:
            intent = candidates[0] if candidates else None
        if intent is None:
            intent = {"action": "agenda_update", "tier": "silent", "why": "no open agenda item", "title": "No action", "priority": 0.0}
        return self._govern_intent(intent, now=now, state=state, needs=needs)

    def tick(self, dry_run: bool = False, now_iso: str | None = None) -> dict[str, Any]:
        decision = self.decide(now_iso=now_iso, persist_agenda=not dry_run)
        if dry_run:
            return {"mode": "dry-run", "executed": False, "decision": decision}
        return self.act(decision)

    def _execute_allowed_action(self, intent: dict[str, Any]) -> dict[str, Any]:
        action = intent.get("action")
        if action in {"agenda_update", "prioritize_thread", "goal_check"}:
            return {"ok": True, "action": action, "message": "Silent agenda action recorded."}
        if action in {"reflection", "inner_voice", "dream"}:
            return {"ok": True, "action": action, "message": f"Internal action {action} recorded for v1."}
        if action == "mind_diary":
            path = self.space_dir / "nova_data" / "mind_diary.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"timestamp": datetime.now().isoformat(), "intent": intent}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return {"ok": True, "action": action, "message": "Nova Mind diary intent recorded."}
        if action == "aces_cycle":
            return self._run_aces_cycle(intent)
        if action in {"telegram_message", "hub_speak"}:
            gate = NotificationGate(self.space_dir / "nova_data" / "notification_state.json")
            event = {"action": action, "success": True, "emotion": {}, "open_threads": len(intent.get("open_threads", [])), "will": {}}
            allowed, reason = gate.should_notify(event)
            return {"ok": False, "action": action, "message": "Notification delivery is not configured.", "notification_gate": {"allowed": allowed, "reason": reason}}
        return {"ok": False, "action": action, "message": "Unknown action blocked by dispatcher."}

    def _run_aces_cycle(self, intent: dict[str, Any]) -> dict[str, Any]:
        """Run ACES only after this kernel has granted the proposal."""
        cli = self.space_dir / "aces_cli.py"
        if not cli.exists():
            return {"ok": False, "action": "aces_cycle", "message": "ACES CLI is not installed."}
        args = [PYTHON, str(cli), "--cycle"]
        if bool(intent.get("apply", False)):
            args.append("--apply")
        else:
            args.append("--dry-run")
        try:
            env = None
            if self._yolo_enabled():
                env = {**os.environ, "NOVA_YOLO_MODE": "1"}
            proc = subprocess.run(args, cwd=str(self.space_dir), env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return {"ok": False, "action": "aces_cycle", "message": f"ACES execution failed: {exc}"}
        if proc.returncode != 0:
            return {"ok": False, "action": "aces_cycle", "message": str(payload.get("message") or proc.stderr[-500:])}
        return {"ok": True, "action": "aces_cycle", "message": str(payload.get("message") or "ACES cycle completed."), "report": payload}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        intent = decision["intent"]
        policy = decision["policy"]
        if not policy.get("allowed"):
            if intent.get("id"):
                self.agenda.mark_result(intent["id"], "blocked", policy)
            self.bio.record_event(
                "decision",
                "Intent blocked by policy",
                intent.get("title", ""),
                policy.get("reason", ""),
                ["Nova"],
                0.4,
                decision.get("state", {}).get("emotion", {}),
                decision.get("needs", {}),
                intent.get("id"),
                [],
                ["entity_kernel", "blocked", f"need:{intent.get('need', 'autonomy')}", *( ["yolo"] if policy.get("bypassed") else [] )],
            )
            return {"executed": False, "reason": policy.get("reason"), "decision": decision}

        result = self._execute_allowed_action(intent)
        self_model_update = self._record_self_model_update(intent, result, policy)
        if self_model_update is not None:
            result["self_model_update"] = self_model_update
        status = "done" if result.get("ok") else "blocked"
        if intent.get("id"):
            self.agenda.mark_result(intent["id"], status, result)
        self.bio.record_event(
            "action" if result.get("ok") else "decision",
            intent.get("title", "Autonomous action"),
            result["message"],
            intent.get("why", ""),
            ["Nova"],
            float(intent.get("priority", 0.5)),
            decision.get("state", {}).get("emotion", {}),
            decision.get("needs", {}),
            intent.get("id"),
            [],
            ["entity_kernel", str(intent.get("action")), f"need:{intent.get('need', 'autonomy')}", *( ["yolo"] if policy.get("bypassed") else [] )],
        )
        return {"executed": bool(result.get("ok")), "result": result, "decision": decision}


def main() -> int:
    # Sidekick's packaged runtime is canonical. This private-space module stays
    # as a recovery implementation and CLI compatibility entrypoint.
    sidekick_src = Path(__file__).resolve().parents[3] / "sidekick"
    if (sidekick_src / "nova" / "__init__.py").exists() and str(sidekick_src) not in sys.path:
        sys.path.insert(0, str(sidekick_src))
    try:
        from nova.entity_kernel import main as canonical_main
    except ImportError:
        canonical_main = None
    if canonical_main is not None:
        return canonical_main()
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Nova Entity Kernel")
    parser.add_argument("command", choices=["scan", "decide", "tick", "act"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    kernel = EntityKernel()
    if args.command == "scan":
        output = kernel.scan()
    elif args.command == "decide":
        output = kernel.decide()
    elif args.command == "tick":
        output = kernel.tick(dry_run=args.dry_run)
    else:
        output = kernel.act(kernel.decide())
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
