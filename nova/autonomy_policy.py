#!/usr/bin/env python3
"""Autonomy policy gate for Nova Entity Kernel v1."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.resolve()
DEFAULT_PATH = HERE / "autonomy_policy.json"


class AutonomyPolicy:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.config = json.loads(self.path.read_text(encoding="utf-8"))

    def _action_tier(self, action: str) -> str:
        action_cfg = self.config.get("actions", {}).get(action, {})
        return action_cfg.get("tier", "risky")

    @staticmethod
    def _hard_block(intent: dict[str, Any]) -> str | None:
        action = str(intent.get("action") or "").lower()
        target = json.dumps(intent.get("target") or {}, ensure_ascii=False).lower()
        payload = json.dumps(intent.get("payload") or {}, ensure_ascii=False).lower()
        combined = f"{action} {target} {payload}"
        if action in {"secret_access", "payment", "admin_action", "destructive_action", "delete"}:
            return "immutable safety boundary"
        markers = ("auth.json", ".env", "credential", "password", "secret", "payment", "admin")
        if any(marker in combined for marker in markers):
            return "sensitive target blocked"
        return None

    def _same_day_count(self, action: str, now: datetime, history: list[dict[str, Any]]) -> int:
        count = 0
        for item in history:
            if item.get("action") != action:
                continue
            try:
                ts = datetime.fromisoformat(str(item.get("timestamp")))
            except ValueError:
                continue
            if ts.date() == now.date():
                count += 1
        return count

    def _minutes_since_last(self, action: str, now: datetime, history: list[dict[str, Any]]) -> float | None:
        latest: datetime | None = None
        for item in history:
            if item.get("action") != action:
                continue
            try:
                ts = datetime.fromisoformat(str(item.get("timestamp")))
            except ValueError:
                continue
            if latest is None or ts > latest:
                latest = ts
        if latest is None:
            return None
        return (now - latest).total_seconds() / 60.0

    def _quiet_hours(self, now: datetime) -> bool:
        cfg = self.config.get("quiet_hours", {})
        start_h, start_m = [int(part) for part in cfg.get("start", "22:00").split(":")]
        end_h, end_m = [int(part) for part in cfg.get("end", "08:00").split(":")]
        current = now.time()
        start = time(start_h, start_m)
        end = time(end_h, end_m)
        if start <= end:
            return start <= current < end
        return current >= start or current < end

    def check(self, intent: dict[str, Any], now: datetime | None = None, history: list[dict[str, Any]] | None = None,
              *, autonomy_level: int = 2, yolo_enabled: bool = False) -> dict[str, Any]:
        now = now or datetime.now()
        history = history or []
        action = str(intent.get("action", ""))
        tier = str(intent.get("tier") or self._action_tier(action))
        tier_cfg = self.config.get("tiers", {}).get(tier, {"allowed": False, "requires_approval": True})
        why = str(intent.get("why", "")).strip()

        hard_block = self._hard_block(intent)
        if hard_block:
            return {"allowed": False, "tier": tier, "reason": hard_block, "requires_approval": False, "hard_boundary": True}

        if not why:
            return {"allowed": False, "tier": tier, "reason": "missing why", "requires_approval": False}
        if yolo_enabled:
            return {
                "allowed": int(autonomy_level) >= 3,
                "tier": tier,
                "reason": "nova_yolo_level_3" if int(autonomy_level) >= 3 else "yolo_requires_level_3",
                "requires_approval": int(autonomy_level) < 3,
                "yolo_enabled": True,
            }
        if tier_cfg.get("requires_approval"):
            return {"allowed": False, "tier": tier, "reason": "requires approval", "requires_approval": True}
        if not tier_cfg.get("allowed", False):
            return {"allowed": False, "tier": tier, "reason": f"tier {tier} disabled", "requires_approval": False}
        if tier == "external":
            allowlisted = set(tier_cfg.get("allowed_actions") or [])
            if action not in allowlisted:
                return {"allowed": False, "tier": tier, "reason": "external action not allowlisted", "requires_approval": True}
            if int(autonomy_level) < 2:
                return {"allowed": False, "tier": tier, "reason": "requires autonomy level 2", "requires_approval": True}
        if tier == "risky" and int(autonomy_level) < 3:
            return {"allowed": False, "tier": tier, "reason": "requires autonomy level 3", "requires_approval": True}
        if tier == "notify" and self._quiet_hours(now):
            return {"allowed": False, "tier": tier, "reason": "quiet hours", "requires_approval": False}
        daily_limit = tier_cfg.get("daily_limit")
        if daily_limit is not None and self._same_day_count(action, now, history) >= int(daily_limit):
            return {"allowed": False, "tier": tier, "reason": "daily limit reached", "requires_approval": False}
        cooldown = tier_cfg.get("cooldown_minutes")
        if cooldown is not None:
            minutes = self._minutes_since_last(action, now, history)
            if minutes is not None and minutes < float(cooldown):
                return {"allowed": False, "tier": tier, "reason": "cooldown active", "requires_approval": False}
        return {"allowed": True, "tier": tier, "reason": "allowed", "requires_approval": False}
