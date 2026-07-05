#!/usr/bin/env python3
"""Nova Entity Kernel v1."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agenda import AgendaStore
from autonomy_policy import AutonomyPolicy
from autobiography import AutobiographyStore
from needs import compute_needs
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

    def scan(self) -> dict[str, Any]:
        if self.state_provider:
            return self.state_provider()
        if collect_snapshot is not None:
            try:
                snapshot = collect_snapshot(mutate=False)
                if isinstance(snapshot, dict):
                    return snapshot
            except Exception as exc:
                return {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "scan_error": repr(exc)}
        script = self.space_dir / "session_start.py"
        result = subprocess.run([PYTHON, str(script), "compact"], capture_output=True, text=True, encoding="utf-8", timeout=45)
        return {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "rendered_context": result.stdout[:4000], "scan_error": result.stderr[:300]}

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
        policy = self.policy.check(intent, now=now, history=self._history())
        return {"timestamp": now.isoformat(), "state": state, "needs": needs, "intent": intent, "policy": policy}

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
        if action in {"telegram_message", "hub_speak"}:
            return {"ok": False, "action": action, "message": "Notify side effect not enabled until explicit side-effect task."}
        return {"ok": False, "action": action, "message": "Unknown action blocked by dispatcher."}

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
                ["entity_kernel", "blocked"],
            )
            return {"executed": False, "reason": policy.get("reason"), "decision": decision}

        result = self._execute_allowed_action(intent)
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
            ["entity_kernel", str(intent.get("action"))],
        )
        return {"executed": bool(result.get("ok")), "result": result, "decision": decision}


def main() -> int:
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
