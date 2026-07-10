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

    def _find_duplicate(self, need: str, action: str) -> dict[str, Any] | None:
        for item in self.data["open"]:
            if item.get("need") == need and item.get("action") == action and item.get("status") in {"open", "active", "blocked"}:
                return item
        return None

    def upsert_intent(self, need: str, title: str, why: str, action: str, priority: float,
                      tier: str = "silent", due_at: str | None = None, source: str = "entity_kernel") -> dict[str, Any]:
        existing = self._find_duplicate(need, action)
        now = _now()
        priority = round(max(0.0, min(1.0, float(priority))), 4)
        if existing:
            existing["updated_at"] = now
            existing["title"] = title
            existing["why"] = why
            existing["priority"] = max(float(existing.get("priority", 0.0)), priority)
            existing["tier"] = tier
            self._save()
            return dict(existing)
        item = {
            "id": _intent_id(need, action),
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
        }
        self.data["open"].append(item)
        self._save()
        return dict(item)

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
