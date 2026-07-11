#!/usr/bin/env python3
"""Persistent intention agenda for Nova Entity Kernel v1."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nova.paths import get_nova_data_dir
from nova.entity_types import Intent

DEFAULT_PATH = get_nova_data_dir() / "agenda.json"


def _now() -> str:
    return datetime.now().isoformat()


def _intent_id(need: str, action: str) -> str:
    return f"intent-{need}-{action}-{int(time.time() * 1000)}"


class AgendaStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"open": [], "archive": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            backup = self.path.with_suffix(f".broken-{int(time.time())}.json")
            self.path.replace(backup)
            return {"open": [], "archive": []}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_duplicate(self, need: str, action: str, target: dict[str, Any] | None = None) -> dict[str, Any] | None:
        target = target or {}
        for item in self.data["open"]:
            if (
                item.get("need") == need
                and item.get("action") == action
                and (item.get("target") or {}) == target
                and item.get("status") in {"open", "active", "blocked"}
            ):
                return item
        return None

    def upsert_intent(self, need: str, title: str, why: str, action: str, priority: float,
                      tier: str = "silent", due_at: str | None = None, source: str = "entity_kernel",
                      target: dict[str, Any] | None = None, payload: dict[str, Any] | None = None,
                      expected_outcome: dict[str, Any] | None = None,
                      evidence_refs: list[str] | None = None,
                      correlation_id: str | None = None, intent_id: str | None = None) -> dict[str, Any]:
        target = target or {}
        existing = self._find_duplicate(need, action, target)
        now = _now()
        priority = round(max(0.0, min(1.0, float(priority))), 4)
        if existing:
            existing["updated_at"] = now
            existing["title"] = title
            existing["why"] = why
            existing["priority"] = max(float(existing.get("priority", 0.0)), priority)
            existing["tier"] = tier
            existing["payload"] = payload or existing.get("payload") or {}
            existing["expected_outcome"] = expected_outcome or existing.get("expected_outcome") or {}
            existing["evidence_refs"] = list(dict.fromkeys([*(existing.get("evidence_refs") or []), *(evidence_refs or [])]))
            if existing.get("status") == "blocked" and (existing.get("last_result") or {}).get("status") == "failed":
                existing["status"] = "open"
            self._save()
            return dict(existing)
        item = {
            "id": intent_id or _intent_id(need, action),
            "created_at": now,
            "updated_at": now,
            "status": "open",
            "need": need,
            "title": title,
            "why": why,
            "action": action,
            "tier": tier,
            "due_at": due_at,
            "priority": priority,
            "cooldown_until": None,
            "attempts": 0,
            "last_result": None,
            "source": source,
            "target": target,
            "payload": payload or {},
            "expected_outcome": expected_outcome or {},
            "evidence_refs": evidence_refs or [],
            "correlation_id": correlation_id,
        }
        self.data["open"].append(item)
        self._save()
        return dict(item)

    def upsert(self, intent: Intent | dict[str, Any]) -> dict[str, Any]:
        data = intent.to_dict() if isinstance(intent, Intent) else dict(intent)
        return self.upsert_intent(
            need=str(data.get("need") or "autonomy"),
            title=str(data.get("title") or data.get("action") or "Nova intent"),
            why=str(data.get("why") or "Nova proposed this intent."),
            action=str(data.get("action") or ""),
            priority=float(data.get("priority", 0.5)),
            tier=str(data.get("policy_tier") or data.get("tier") or "silent"),
            due_at=data.get("due_at"),
            source=str(data.get("source") or "entity_kernel"),
            target=data.get("target") or {},
            payload=data.get("payload") or {},
            expected_outcome=data.get("expected_outcome") or {},
            evidence_refs=data.get("evidence_refs") or [],
            correlation_id=data.get("correlation_id"),
            intent_id=data.get("intent_id") or data.get("id"),
        )

    def list_open(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.data.get("open", []) if item.get("status") in {"open", "active"}]

    def list_archive(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.data.get("archive", [])]

    def best_intent(self) -> dict[str, Any] | None:
        candidates = self.list_open()
        candidates.sort(key=lambda item: float(item.get("priority", 0.0)), reverse=True)
        return candidates[0] if candidates else None

    def mark_result(self, intent_id: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        if status not in {"done", "blocked", "dismissed", "active", "open"}:
            raise ValueError(f"invalid status: {status}")
        for item in list(self.data.get("open", [])):
            if item.get("id") == intent_id:
                item["status"] = status
                item["updated_at"] = _now()
                item["attempts"] = int(item.get("attempts", 0)) + 1
                item["last_result"] = result
                if status in {"done", "dismissed"}:
                    self.data["open"].remove(item)
                    self.data.setdefault("archive", []).append(item)
                self._save()
                return dict(item)
        raise KeyError(intent_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova agenda store")
    parser.add_argument("command", choices=["list", "archive", "best"])
    args = parser.parse_args()
    store = AgendaStore()
    if args.command == "list":
        print(json.dumps(store.list_open(), ensure_ascii=False, indent=2))
    elif args.command == "archive":
        print(json.dumps(store.list_archive(), ensure_ascii=False, indent=2))
    elif args.command == "best":
        print(json.dumps(store.best_intent(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
