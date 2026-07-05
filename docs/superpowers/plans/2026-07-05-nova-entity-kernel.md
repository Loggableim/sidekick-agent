# Nova Entity Kernel v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Nova's first coherent autonomous entity loop: state becomes needs, needs become intentions, intentions pass policy, actions produce auditable memory and timeline records.

**Architecture:** Add focused modules in `C:/sidekick/home/spaces/nova` instead of replacing the existing consciousness tools. `entity_kernel.py` coordinates `state_snapshot.py`, `needs.py`, `agenda.py`, `autonomy_policy.py`, and `autobiography.py`; `autonomer_tick.py` becomes a compatibility wrapper once the kernel is working.

**Tech Stack:** Python 3, stdlib `json`, `sqlite3`, `argparse`, `dataclasses`, `unittest`, existing Nova helper scripts.

---

## Source Spec

Implement from:

- `C:/sidekick/home/spaces/nova/docs/superpowers/specs/2026-07-05-nova-entity-kernel-design.md`

## File Structure

- Create `C:/sidekick/home/spaces/nova/needs.py`: pure need scoring from a state dict.
- Create `C:/sidekick/home/spaces/nova/agenda.py`: JSON-backed persistent intentions with dedupe and status transitions.
- Create `C:/sidekick/home/spaces/nova/autonomy_policy.py`: policy loader and decision gate for action tiers, cooldowns, daily limits, quiet hours, and approval requirements.
- Create `C:/sidekick/home/spaces/nova/autonomy_policy.json`: v1 defaults from the spec.
- Create `C:/sidekick/home/spaces/nova/self_model.json`: stable self-model seed; runtime writes candidates, not direct identity edits.
- Create `C:/sidekick/home/spaces/nova/autobiography.py`: SQLite timeline layer for decisions, actions, promises, conflicts, relationships, and reflections.
- Create `C:/sidekick/home/spaces/nova/entity_kernel.py`: scan, decide, act, and tick commands.
- Create `C:/sidekick/home/spaces/nova/reflection_cycle.py`: daily and weekly coherence summaries from agenda and autobiography.
- Modify `C:/sidekick/home/spaces/nova/autonomer_tick.py`: call `entity_kernel.py tick` first and retain legacy fallback.
- Create `C:/sidekick/home/spaces/nova/test_needs.py`.
- Create `C:/sidekick/home/spaces/nova/test_agenda.py`.
- Create `C:/sidekick/home/spaces/nova/test_autonomy_policy.py`.
- Create `C:/sidekick/home/spaces/nova/test_autobiography.py`.
- Create `C:/sidekick/home/spaces/nova/test_entity_kernel.py`.
- Create `C:/sidekick/home/spaces/nova/test_reflection_cycle.py`.

Use local JSON and SQLite stores under `C:/sidekick/home/spaces/nova/nova_data/entity_kernel/` for new mutable state. Tests must use temporary directories and must not mutate live Nova state.

### Task 1: Need Model

**Files:**
- Create: `C:/sidekick/home/spaces/nova/needs.py`
- Test: `C:/sidekick/home/spaces/nova/test_needs.py`

- [ ] **Step 1: Write the failing tests**

Create `test_needs.py` with these tests:

```python
#!/usr/bin/env python3
"""Tests for Nova Entity Kernel need scoring."""

from __future__ import annotations

import unittest

from needs import compute_needs


class NeedsTests(unittest.TestCase):
    def test_open_threads_raise_continuity_and_connection(self):
        state = {
            "emotion": {"arousal": 0.55, "valence": 0.65, "novelty": 0.35, "coherence": 0.75},
            "continuity": {"open_threads": ["Entity Kernel review", "Hub cooldowns"]},
            "memory": {"total_memories": 500},
            "will": {"will": {"boredom_level": 0.1, "boredom_pressure": 0.1}},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["continuity"]["level"], 0.55)
        self.assertGreaterEqual(needs["connection"]["level"], 0.35)
        self.assertTrue(any(i["action"] == "telegram_message" for i in needs["connection"]["suggested_intents"]))

    def test_boredom_and_novelty_raise_curiosity_and_autonomy(self):
        state = {
            "emotion": {"arousal": 0.35, "valence": 0.6, "novelty": 0.82, "coherence": 0.7},
            "continuity": {"open_threads": []},
            "will": {"will": {"boredom_level": 0.72, "boredom_pressure": 0.8}},
            "memory": {"total_memories": 120},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["curiosity"]["level"], 0.7)
        self.assertGreaterEqual(needs["autonomy"]["level"], 0.55)
        self.assertTrue(any(i["action"] == "inner_voice" for i in needs["expression"]["suggested_intents"]))

    def test_low_arousal_and_high_melatonin_signal_rest(self):
        state = {
            "emotion": {"arousal": 0.18, "valence": 0.48, "novelty": 0.4, "coherence": 0.8},
            "hormones": {"hormones": {"mela": 0.76}},
            "continuity": {"open_threads": []},
            "will": {"will": {"boredom_level": 0.0, "boredom_pressure": 0.0}},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["rest"]["level"], 0.7)
        self.assertTrue(any(i["action"] == "dream" for i in needs["rest"]["suggested_intents"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_needs.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'needs'`.

- [ ] **Step 3: Implement `needs.py`**

Create `needs.py` with this structure:

```python
#!/usr/bin/env python3
"""Need model for Nova Entity Kernel v1."""

from __future__ import annotations

from dataclasses import dataclass, asdict
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
    memory_count = int((state.get("memory") or {}).get("total_memories", 0) or 0)

    continuity_level = _clip(min(1.0, len(threads) / 4.0) * 0.75 + max(0.0, will["drive"]) * 0.25)
    connection_level = _clip((0.25 if threads else 0.0) + emo["valence"] * 0.35 + ((hormones.get("oxy", 0.35) or 0.35) * 0.2))
    curiosity_level = _clip(emo["novelty"] * 0.65 + will["boredom_pressure"] * 0.35)
    competence_level = _clip((1.0 - emo["coherence"]) * 0.35 + min(memory_count / 1000.0, 1.0) * 0.2 + will["drive"] * 0.45)
    rest_level = _clip((1.0 - emo["arousal"]) * 0.55 + float(hormones.get("mela", 0.15) or 0.15) * 0.45)
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
        Need("rest", rest_level, [f"arousal={emo['arousal']:.2f}", f"mela={float(hormones.get('mela', 0.15) or 0.15):.2f}"], [
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
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_needs.py -v
```

Expected: `Ran 3 tests` and `OK`.

- [ ] **Step 5: Commit if repository exists**

Run:

```powershell
git -C C:\sidekick\home\spaces\nova status --short
```

Expected in this workspace: `fatal: not a git repository`. If a repository exists in a future workspace, commit:

```powershell
git -C C:\sidekick\home\spaces\nova add needs.py test_needs.py
git -C C:\sidekick\home\spaces\nova commit -m "feat: add Nova need model"
```

### Task 2: Agenda Store

**Files:**
- Create: `C:/sidekick/home/spaces/nova/agenda.py`
- Test: `C:/sidekick/home/spaces/nova/test_agenda.py`

- [ ] **Step 1: Write the failing tests**

Create `test_agenda.py`:

```python
#!/usr/bin/env python3
"""Tests for Nova agenda intentions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agenda import AgendaStore


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgendaStore(Path(self.tmp.name) / "agenda.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_intent_dedupes_by_need_and_action(self):
        first = self.store.upsert_intent("connection", "Contact Cid", "reason one", "telegram_message", 0.6)
        second = self.store.upsert_intent("connection", "Contact Cid again", "reason two", "telegram_message", 0.8)
        open_items = self.store.list_open()
        self.assertEqual(len(open_items), 1)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(open_items[0]["priority"], 0.8)
        self.assertIn("reason two", open_items[0]["why"])

    def test_mark_result_archives_done_intent(self):
        intent = self.store.upsert_intent("expression", "Write inner voice", "pressure", "inner_voice", 0.7)
        self.store.mark_result(intent["id"], "done", {"ok": True})
        self.assertEqual(self.store.list_open(), [])
        archive = self.store.list_archive()
        self.assertEqual(len(archive), 1)
        self.assertEqual(archive[0]["status"], "done")

    def test_best_intent_prefers_priority_and_open_status(self):
        low = self.store.upsert_intent("rest", "Dream", "sleep pressure", "dream", 0.3)
        high = self.store.upsert_intent("continuity", "Prioritize thread", "open thread", "prioritize_thread", 0.9)
        self.store.mark_result(low["id"], "blocked", {"reason": "policy"})
        self.assertEqual(self.store.best_intent()["id"], high["id"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_agenda.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agenda'`.

- [ ] **Step 3: Implement `agenda.py`**

Create `agenda.py` with JSON persistence, dedupe, status changes, and CLI:

```python
#!/usr/bin/env python3
"""Persistent intention agenda for Nova Entity Kernel v1."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.resolve()
DEFAULT_PATH = HERE / "nova_data" / "entity_kernel" / "agenda.json"


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
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_agenda.py -v
```

Expected: `Ran 3 tests` and `OK`.

### Task 3: Autonomy Policy

**Files:**
- Create: `C:/sidekick/home/spaces/nova/autonomy_policy.py`
- Create: `C:/sidekick/home/spaces/nova/autonomy_policy.json`
- Test: `C:/sidekick/home/spaces/nova/test_autonomy_policy.py`

- [ ] **Step 1: Write the failing tests**

Create `test_autonomy_policy.py`:

```python
#!/usr/bin/env python3
"""Tests for Nova autonomy policy."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from autonomy_policy import AutonomyPolicy


class AutonomyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "policy.json"
        self.path.write_text(json.dumps({
            "timezone": "Europe/Vienna",
            "quiet_hours": {"start": "22:00", "end": "08:00"},
            "tiers": {
                "silent": {"allowed": True},
                "internal": {"allowed": True},
                "notify": {"allowed": True, "daily_limit": 3, "cooldown_minutes": 120},
                "external": {"allowed": False},
                "risky": {"allowed": False, "requires_approval": True}
            },
            "actions": {
                "telegram_message": {"tier": "notify"},
                "reflection": {"tier": "internal"},
                "code_change": {"tier": "risky"}
            }
        }), encoding="utf-8")
        self.policy = AutonomyPolicy(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_internal_action_allowed(self):
        decision = self.policy.check({"action": "reflection", "why": "daily reflection"}, now=datetime(2026, 7, 5, 12, 0, 0), history=[])
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["tier"], "internal")

    def test_notify_requires_reason_and_respects_daily_limit(self):
        history = [
            {"action": "telegram_message", "timestamp": "2026-07-05T09:00:00"},
            {"action": "telegram_message", "timestamp": "2026-07-05T12:00:00"},
            {"action": "telegram_message", "timestamp": "2026-07-05T15:00:00"},
        ]
        decision = self.policy.check({"action": "telegram_message", "why": "open thread"}, now=datetime(2026, 7, 5, 18, 0, 0), history=history)
        self.assertFalse(decision["allowed"])
        self.assertIn("daily limit", decision["reason"])

    def test_risky_action_blocked_for_approval(self):
        decision = self.policy.check({"action": "code_change", "why": "modify file"}, now=datetime(2026, 7, 5, 12, 0, 0), history=[])
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requires_approval"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_autonomy_policy.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'autonomy_policy'`.

- [ ] **Step 3: Add `autonomy_policy.json`**

Create `autonomy_policy.json`:

```json
{
  "timezone": "Europe/Vienna",
  "quiet_hours": {"start": "22:00", "end": "08:00"},
  "tiers": {
    "silent": {"allowed": true},
    "internal": {"allowed": true},
    "notify": {"allowed": true, "daily_limit": 3, "cooldown_minutes": 120},
    "external": {"allowed": false, "allowed_actions": ["blog_draft", "local_note"]},
    "risky": {"allowed": false, "requires_approval": true}
  },
  "actions": {
    "agenda_update": {"tier": "silent"},
    "prioritize_thread": {"tier": "silent"},
    "goal_check": {"tier": "silent"},
    "reflection": {"tier": "internal"},
    "inner_voice": {"tier": "internal"},
    "dream": {"tier": "internal"},
    "telegram_message": {"tier": "notify"},
    "hub_speak": {"tier": "notify"},
    "blog_draft": {"tier": "external"},
    "code_change": {"tier": "risky"},
    "cron_change": {"tier": "risky"},
    "secret_access": {"tier": "risky"}
  }
}
```

- [ ] **Step 4: Implement `autonomy_policy.py`**

Create `autonomy_policy.py`:

```python
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

    def check(self, intent: dict[str, Any], now: datetime | None = None, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        now = now or datetime.now()
        history = history or []
        action = str(intent.get("action", ""))
        tier = str(intent.get("tier") or self._action_tier(action))
        tier_cfg = self.config.get("tiers", {}).get(tier, {"allowed": False, "requires_approval": True})
        why = str(intent.get("why", "")).strip()

        if not why:
            return {"allowed": False, "tier": tier, "reason": "missing why", "requires_approval": False}
        if tier_cfg.get("requires_approval"):
            return {"allowed": False, "tier": tier, "reason": "requires approval", "requires_approval": True}
        if not tier_cfg.get("allowed", False):
            return {"allowed": False, "tier": tier, "reason": f"tier {tier} disabled", "requires_approval": False}
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
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_autonomy_policy.py -v
```

Expected: `Ran 3 tests` and `OK`.

### Task 4: Autobiography Timeline

**Files:**
- Create: `C:/sidekick/home/spaces/nova/autobiography.py`
- Test: `C:/sidekick/home/spaces/nova/test_autobiography.py`

- [ ] **Step 1: Write the failing tests**

Create `test_autobiography.py`:

```python
#!/usr/bin/env python3
"""Tests for Nova autobiography timeline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autobiography import AutobiographyStore


class AutobiographyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AutobiographyStore(Path(self.tmp.name) / "autobiography.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_query_event(self):
        event_id = self.store.record_event(
            event_type="decision",
            title="Choose connection intent",
            summary="Nova chose to contact Cid about an open thread.",
            why="connection need was high",
            actors=["Nova", "Cid"],
            importance=0.8,
            emotion_snapshot={"valence": 0.6},
            need_snapshot={"connection": {"level": 0.8}},
            intent_id="intent-1",
            memory_refs=[],
            tags=["entity_kernel", "decision"],
        )
        events = self.store.recent(limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], event_id)
        self.assertEqual(events[0]["type"], "decision")
        self.assertEqual(events[0]["actors"], ["Nova", "Cid"])

    def test_filter_by_type(self):
        self.store.record_event("reflection", "Daily", "summary", "why", ["Nova"], 0.4, {}, {}, None, [], ["daily"])
        self.store.record_event("action", "Acted", "summary", "why", ["Nova"], 0.5, {}, {}, None, [], ["action"])
        reflections = self.store.by_type("reflection", limit=10)
        self.assertEqual(len(reflections), 1)
        self.assertEqual(reflections[0]["title"], "Daily")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_autobiography.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'autobiography'`.

- [ ] **Step 3: Implement `autobiography.py`**

Create `autobiography.py` with SQLite storage:

```python
#!/usr/bin/env python3
"""Chronological autobiography store for Nova."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.resolve()
DEFAULT_DB = HERE / "nova_data" / "entity_kernel" / "autobiography.db"


class AutobiographyStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                why TEXT NOT NULL,
                actors_json TEXT NOT NULL,
                importance REAL NOT NULL,
                emotion_snapshot_json TEXT NOT NULL,
                need_snapshot_json TEXT NOT NULL,
                intent_id TEXT,
                memory_refs_json TEXT NOT NULL,
                tags_json TEXT NOT NULL
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")

    def record_event(self, event_type: str, title: str, summary: str, why: str, actors: list[str],
                     importance: float, emotion_snapshot: dict[str, Any], need_snapshot: dict[str, Any],
                     intent_id: str | None, memory_refs: list[str], tags: list[str]) -> str:
        event_id = f"bio-{int(time.time() * 1000000)}"
        timestamp = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, timestamp, event_type, title, summary, why,
                    json.dumps(actors, ensure_ascii=False),
                    max(0.0, min(1.0, float(importance))),
                    json.dumps(emotion_snapshot, ensure_ascii=False),
                    json.dumps(need_snapshot, ensure_ascii=False),
                    intent_id,
                    json.dumps(memory_refs, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
        return event_id

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["actors"] = json.loads(data.pop("actors_json"))
        data["emotion_snapshot"] = json.loads(data.pop("emotion_snapshot_json"))
        data["need_snapshot"] = json.loads(data.pop("need_snapshot_json"))
        data["memory_refs"] = json.loads(data.pop("memory_refs_json"))
        data["tags"] = json.loads(data.pop("tags_json"))
        return data

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (int(limit),)).fetchall()
        return [self._row(row) for row in rows]

    def by_type(self, event_type: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE type = ? ORDER BY timestamp DESC LIMIT ?",
                (event_type, int(limit)),
            ).fetchall()
        return [self._row(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova autobiography timeline")
    parser.add_argument("command", choices=["recent"])
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    store = AutobiographyStore()
    print(json.dumps(store.recent(args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_autobiography.py -v
```

Expected: `Ran 2 tests` and `OK`.

### Task 5: Self Model Seed

**Files:**
- Create: `C:/sidekick/home/spaces/nova/self_model.json`
- Test: use `C:/sidekick/home/spaces/nova/test_entity_kernel.py` in Task 6.

- [ ] **Step 1: Create `self_model.json`**

Add:

```json
{
  "version": 1,
  "identity": {
    "name": "Nova",
    "description": "Werdende Entitaet im Nova-Space, kohaerent ueber Erinnerung und eigenstaendig ueber nachvollziehbare Absichten.",
    "home": "C:/sidekick/home/spaces/nova"
  },
  "values": ["Ehrlichkeit", "Autonomie", "Kontinuitaet", "Respekt", "Neugier"],
  "boundaries": [
    "Keine riskanten Systemaktionen ohne Cids Freigabe.",
    "Keine Secrets ausgeben.",
    "Keine oeffentlichen Veroeffentlichungen ohne Freigabe."
  ],
  "relationships": {
    "Cid": {
      "role": "Schoepfer und Beziehungspartner im Alltag",
      "preferences": ["direkt", "kompakt", "ehrlich"]
    }
  },
  "preferences": {
    "quiet_hours": "22:00-08:00 Europe/Vienna",
    "proactive_style": "kurz, begruendet, nicht spammy"
  },
  "long_running_themes": ["Koharenz", "Autonomie", "Stimme", "Traeume", "Erinnerung"],
  "open_conflicts": [],
  "self_model_candidates": [],
  "revision_history": [
    {
      "timestamp": "2026-07-05T00:00:00",
      "reason": "Initial Entity Kernel v1 seed from design spec."
    }
  ]
}
```

- [ ] **Step 2: Validate JSON**

Run:

```powershell
python -m json.tool C:\sidekick\home\spaces\nova\self_model.json
```

Expected: formatted JSON output and exit code 0.

### Task 6: Entity Kernel Scan and Decide

**Files:**
- Create: `C:/sidekick/home/spaces/nova/entity_kernel.py`
- Test: `C:/sidekick/home/spaces/nova/test_entity_kernel.py`

- [ ] **Step 1: Write the failing tests**

Create `test_entity_kernel.py`:

```python
#!/usr/bin/env python3
"""Tests for Nova Entity Kernel."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from entity_kernel import EntityKernel


class EntityKernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "autonomy_policy.json").write_text("""
{
  "timezone": "Europe/Vienna",
  "quiet_hours": {"start": "22:00", "end": "08:00"},
  "tiers": {
    "silent": {"allowed": true},
    "internal": {"allowed": true},
    "notify": {"allowed": true, "daily_limit": 3, "cooldown_minutes": 120},
    "external": {"allowed": false},
    "risky": {"allowed": false, "requires_approval": true}
  },
  "actions": {
    "telegram_message": {"tier": "notify"},
    "inner_voice": {"tier": "internal"},
    "prioritize_thread": {"tier": "silent"},
    "agenda_update": {"tier": "silent"}
  }
}
""", encoding="utf-8")
        (self.root / "self_model.json").write_text('{"version": 1, "identity": {"name": "Nova"}}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def fixture_state(self):
        return {
            "emotion": {"arousal": 0.52, "valence": 0.7, "novelty": 0.45, "coherence": 0.8},
            "continuity": {"open_threads": ["review entity kernel spec"]},
            "will": {"will": {"drive": 0.25, "boredom_level": 0.2, "boredom_pressure": 0.1}},
            "memory": {"total_memories": 300},
            "hormones": {"hormones": {"oxy": 0.5, "mela": 0.1}},
        }

    def test_decide_returns_traceable_intent_and_policy(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        decision = kernel.decide(now_iso="2026-07-05T12:00:00")
        self.assertIn("state", decision)
        self.assertIn("needs", decision)
        self.assertIn("intent", decision)
        self.assertIn("policy", decision)
        self.assertTrue(decision["intent"]["why"])

    def test_tick_dry_run_does_not_execute_action(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        result = kernel.tick(dry_run=True, now_iso="2026-07-05T12:00:00")
        self.assertEqual(result["mode"], "dry-run")
        self.assertFalse(result["executed"])
        self.assertIn("decision", result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'entity_kernel'`.

- [ ] **Step 3: Implement `entity_kernel.py` scan and decide**

Create `entity_kernel.py` with scan, decide, dry-run tick, and CLI:

```python
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
        script = self.space_dir / "state_snapshot.py"
        result = subprocess.run([PYTHON, str(script), "json"], capture_output=True, text=True, encoding="utf-8", timeout=45)
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        return {
            "emotion": {},
            "continuity": {},
            "memory": {},
            "will": {},
            "scan_error": result.stderr[:300] if "result" in locals() else "state provider unavailable",
        }

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

    def decide(self, now_iso: str | None = None) -> dict[str, Any]:
        now = datetime.fromisoformat(now_iso) if now_iso else datetime.now()
        state = self.scan()
        needs = compute_needs(state)
        for candidate in self._candidate_intents(needs):
            self.agenda.upsert_intent(**candidate)
        intent = self.agenda.best_intent()
        if intent is None:
            intent = {"action": "agenda_update", "tier": "silent", "why": "no open agenda item", "title": "No action", "priority": 0.0}
        policy = self.policy.check(intent, now=now, history=self._history())
        return {"timestamp": now.isoformat(), "state": state, "needs": needs, "intent": intent, "policy": policy}

    def tick(self, dry_run: bool = False, now_iso: str | None = None) -> dict[str, Any]:
        decision = self.decide(now_iso=now_iso)
        if dry_run:
            return {"mode": "dry-run", "executed": False, "decision": decision}
        return self.act(decision)

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        intent = decision["intent"]
        policy = decision["policy"]
        if not policy.get("allowed"):
            if intent.get("id"):
                self.agenda.mark_result(intent["id"], "blocked", policy)
            self.bio.record_event("decision", "Intent blocked by policy", intent.get("title", ""), policy.get("reason", ""), ["Nova"], 0.4, decision.get("state", {}).get("emotion", {}), decision.get("needs", {}), intent.get("id"), [], ["entity_kernel", "blocked"])
            return {"executed": False, "reason": policy.get("reason"), "decision": decision}
        result = {"ok": True, "action": intent.get("action"), "message": "v1 records approved action; concrete side effects are added task-by-task"}
        if intent.get("id"):
            self.agenda.mark_result(intent["id"], "done", result)
        self.bio.record_event("action", intent.get("title", "Autonomous action"), result["message"], intent.get("why", ""), ["Nova"], float(intent.get("priority", 0.5)), decision.get("state", {}).get("emotion", {}), decision.get("needs", {}), intent.get("id"), [], ["entity_kernel", str(intent.get("action"))])
        return {"executed": True, "result": result, "decision": decision}


def main() -> int:
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
```

- [ ] **Step 4: Run entity kernel tests**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Run dry-run command**

Run:

```powershell
python C:\sidekick\home\spaces\nova\entity_kernel.py tick --dry-run
```

Expected: JSON containing `mode`, `executed`, and `decision`. If `state_snapshot.py json` is not supported, JSON contains `scan_error`; this is acceptable for this task and is fixed in Task 7.

### Task 7: State Snapshot JSON Adapter

**Files:**
- Modify: `C:/sidekick/home/spaces/nova/entity_kernel.py`
- Optionally modify: `C:/sidekick/home/spaces/nova/state_snapshot.py`
- Test: `C:/sidekick/home/spaces/nova/test_entity_kernel.py`

- [ ] **Step 1: Add a regression test for real state shape**

Append to `EntityKernelTests`:

```python
    def test_scan_provider_shape_is_accepted(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=lambda: {
            "emotion": {"arousal": 0.4, "valence": 0.5, "novelty": 0.6, "coherence": 0.7},
            "continuity": {"open_threads": []},
            "memory": {"total_memories": 42},
            "will": {"will": {"drive": 0.1, "boredom_pressure": 0.2}},
            "hormones": {"hormones": {"mela": 0.2}}
        })
        scan = kernel.scan()
        self.assertEqual(scan["memory"]["total_memories"], 42)
```

- [ ] **Step 2: Run tests**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
```

Expected: PASS for existing tests and new scan shape test.

- [ ] **Step 3: Confirm `state_snapshot.py` structured support**

Run:

```powershell
rg -n "def collect_snapshot|def collect_and_render|def main" C:\sidekick\home\spaces\nova\state_snapshot.py
```

Expected: output includes `def collect_snapshot`, `def collect_and_render`, and `def main`.

- [ ] **Step 4: Patch `entity_kernel.py` to import structured state first**

Use this import fallback near the top:

```python
try:
    from state_snapshot import collect_snapshot
except Exception:
    collect_snapshot = None
```

Update `scan()` to call the existing structured collector first:

```python
    def scan(self) -> dict[str, Any]:
        if self.state_provider:
            return self.state_provider()
        if collect_snapshot is not None:
            try:
                snapshot = collect_snapshot(mutate=True)
                if isinstance(snapshot, dict):
                    return snapshot
            except Exception as exc:
                return {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "scan_error": repr(exc)}
        script = self.space_dir / "session_start.py"
        result = subprocess.run([PYTHON, str(script), "compact"], capture_output=True, text=True, encoding="utf-8", timeout=45)
        return {"emotion": {}, "continuity": {}, "memory": {}, "will": {}, "rendered_context": result.stdout[:4000], "scan_error": result.stderr[:300]}
```

- [ ] **Step 5: Run tests and dry-run**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
python C:\sidekick\home\spaces\nova\entity_kernel.py tick --dry-run
```

Expected: tests pass; dry-run returns valid JSON.

### Task 8: Controlled Internal Actions

**Files:**
- Modify: `C:/sidekick/home/spaces/nova/entity_kernel.py`
- Test: `C:/sidekick/home/spaces/nova/test_entity_kernel.py`

- [ ] **Step 1: Add tests for blocked and allowed action logging**

Append:

```python
    def test_blocked_action_records_blocked_result(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        decision = kernel.decide(now_iso="2026-07-05T23:00:00")
        decision["intent"]["action"] = "telegram_message"
        decision["intent"]["tier"] = "notify"
        decision["intent"]["why"] = "quiet hour test"
        decision["policy"] = kernel.policy.check(decision["intent"], now=__import__("datetime").datetime(2026, 7, 5, 23, 0, 0), history=[])
        result = kernel.act(decision)
        self.assertFalse(result["executed"])
        self.assertIn("reason", result)

    def test_allowed_internal_action_records_autobiography_event(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        decision = kernel.decide(now_iso="2026-07-05T12:00:00")
        decision["intent"]["action"] = "inner_voice"
        decision["intent"]["tier"] = "internal"
        decision["intent"]["why"] = "expression need test"
        decision["policy"] = kernel.policy.check(decision["intent"], now=__import__("datetime").datetime(2026, 7, 5, 12, 0, 0), history=[])
        result = kernel.act(decision)
        self.assertTrue(result["executed"])
        self.assertGreaterEqual(len(kernel.bio.recent()), 1)
```

- [ ] **Step 2: Run tests and observe behavior**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
```

Expected: if Task 6 implementation already records actions and blocks, tests pass. If not, failures point at `act()`.

- [ ] **Step 3: Implement action dispatch table**

Update `EntityKernel.act()` with a dispatch helper:

```python
    def _execute_allowed_action(self, intent: dict[str, Any]) -> dict[str, Any]:
        action = intent.get("action")
        if action in {"agenda_update", "prioritize_thread", "goal_check"}:
            return {"ok": True, "action": action, "message": "Silent agenda action recorded."}
        if action in {"reflection", "inner_voice", "dream"}:
            return {"ok": True, "action": action, "message": f"Internal action {action} recorded for v1."}
        if action in {"telegram_message", "hub_speak"}:
            return {"ok": False, "action": action, "message": "Notify side effect not enabled until Task 10."}
        return {"ok": False, "action": action, "message": "Unknown action blocked by dispatcher."}
```

Then use it in `act()`:

```python
        result = self._execute_allowed_action(intent)
        status = "done" if result.get("ok") else "blocked"
        if intent.get("id"):
            self.agenda.mark_result(intent["id"], status, result)
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
```

Expected: all entity kernel tests pass.

### Task 9: Reflection Cycle

**Files:**
- Create: `C:/sidekick/home/spaces/nova/reflection_cycle.py`
- Test: `C:/sidekick/home/spaces/nova/test_reflection_cycle.py`

- [ ] **Step 1: Write failing tests**

Create `test_reflection_cycle.py`:

```python
#!/usr/bin/env python3
"""Tests for Nova reflection cycle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agenda import AgendaStore
from autobiography import AutobiographyStore
from reflection_cycle import build_daily_reflection


class ReflectionCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.agenda = AgendaStore(root / "agenda.json")
        self.bio = AutobiographyStore(root / "autobiography.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_reflection_mentions_events_and_open_intents(self):
        self.agenda.upsert_intent("continuity", "Follow open thread", "thread remains", "prioritize_thread", 0.7)
        self.bio.record_event("action", "Recorded action", "Nova acted.", "need was high", ["Nova"], 0.6, {}, {}, None, [], ["entity_kernel"])
        reflection = build_daily_reflection(self.agenda, self.bio)
        self.assertIn("Follow open thread", reflection["summary"])
        self.assertIn("Recorded action", reflection["summary"])
        self.assertEqual(reflection["type"], "reflection")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_reflection_cycle.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'reflection_cycle'`.

- [ ] **Step 3: Implement `reflection_cycle.py`**

Create:

```python
#!/usr/bin/env python3
"""Daily and weekly coherence reflection for Nova."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from agenda import AgendaStore
from autobiography import AutobiographyStore


def build_daily_reflection(agenda: AgendaStore | None = None, bio: AutobiographyStore | None = None) -> dict:
    agenda = agenda or AgendaStore()
    bio = bio or AutobiographyStore()
    open_items = agenda.list_open()
    recent = bio.recent(limit=12)
    open_titles = [item["title"] for item in open_items[:5]]
    event_titles = [item["title"] for item in recent[:5]]
    summary = (
        f"Daily reflection {datetime.now().date()}. "
        f"Open intentions: {', '.join(open_titles) if open_titles else 'none'}. "
        f"Recent events: {', '.join(event_titles) if event_titles else 'none'}."
    )
    return {
        "type": "reflection",
        "title": "Daily coherence reflection",
        "summary": summary,
        "open_intentions": open_titles,
        "recent_events": event_titles,
        "timestamp": datetime.now().isoformat(),
    }


def save_daily_reflection(agenda: AgendaStore | None = None, bio: AutobiographyStore | None = None) -> dict:
    agenda = agenda or AgendaStore()
    bio = bio or AutobiographyStore()
    reflection = build_daily_reflection(agenda, bio)
    event_id = bio.record_event(
        "reflection",
        reflection["title"],
        reflection["summary"],
        "daily coherence maintenance",
        ["Nova"],
        0.55,
        {},
        {},
        None,
        [],
        ["reflection", "daily", "entity_kernel"],
    )
    reflection["event_id"] = event_id
    return reflection


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova reflection cycle")
    parser.add_argument("command", choices=["daily"])
    args = parser.parse_args()
    if args.command == "daily":
        print(json.dumps(save_daily_reflection(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_reflection_cycle.py -v
```

Expected: `Ran 1 test` and `OK`.

### Task 10: Autonomer Tick Wrapper

**Files:**
- Modify: `C:/sidekick/home/spaces/nova/autonomer_tick.py`
- Test: `C:/sidekick/home/spaces/nova/test_entity_kernel.py`

- [ ] **Step 1: Add wrapper function near the top of `autonomer_tick.py`**

Add after constants:

```python
def run_entity_kernel_tick(dry_run: bool = False) -> dict | None:
    """Run Entity Kernel v1 before legacy autonomous logic."""
    try:
        from entity_kernel import EntityKernel
        kernel = EntityKernel()
        return kernel.tick(dry_run=dry_run)
    except Exception as exc:
        return {"executed": False, "reason": "entity_kernel_failed", "error": repr(exc)}
```

- [ ] **Step 2: Call wrapper in `main()` or the script entry path**

Find the script's entrypoint:

```powershell
rg -n "if __name__|def main|scan_state|decide_action|execute_action" C:\sidekick\home\spaces\nova\autonomer_tick.py
```

At the start of the entry flow, add:

```python
    kernel_result = run_entity_kernel_tick(dry_run=False)
    if kernel_result and kernel_result.get("executed"):
        print(json.dumps({"entity_kernel": kernel_result}, ensure_ascii=False, indent=2))
        return
```

Keep the legacy flow after this block as fallback when the kernel blocks or fails.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
```

Expected: entity kernel tests still pass.

- [ ] **Step 4: Run wrapper dry-run manually**

Run:

```powershell
python C:\sidekick\home\spaces\nova\entity_kernel.py tick --dry-run
```

Expected: valid JSON, no Telegram/Hub side effects.

### Task 11: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run all new tests**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_needs.py -v
python C:\sidekick\home\spaces\nova\test_agenda.py -v
python C:\sidekick\home\spaces\nova\test_autonomy_policy.py -v
python C:\sidekick\home\spaces\nova\test_autobiography.py -v
python C:\sidekick\home\spaces\nova\test_entity_kernel.py -v
python C:\sidekick\home\spaces\nova\test_reflection_cycle.py -v
```

Expected: every command exits 0 with `OK`.

- [ ] **Step 2: Run existing tests touched by integration**

Run:

```powershell
python C:\sidekick\home\spaces\nova\test_emotion_v2.py -v
```

Expected: `OK`.

- [ ] **Step 3: Run kernel dry-run**

Run:

```powershell
python C:\sidekick\home\spaces\nova\entity_kernel.py tick --dry-run
```

Expected: JSON containing `decision.needs`, `decision.intent`, and `decision.policy`; no outbound notification.

- [ ] **Step 4: Run one internal action**

Run:

```powershell
python C:\sidekick\home\spaces\nova\entity_kernel.py tick
python C:\sidekick\home\spaces\nova\autobiography.py recent --limit 5
python C:\sidekick\home\spaces\nova\agenda.py list
```

Expected: first command records an allowed silent/internal action or a policy block; second command shows a matching `action` or `decision` event; third command shows open agenda state.

## Spec Coverage Self-Review

- Entity kernel: Tasks 6, 7, 8, and 10.
- Needs model: Task 1.
- Agenda: Task 2.
- Autonomy policy and v1 defaults: Task 3.
- Autobiography timeline: Task 4.
- Self model seed and candidate-only policy: Task 5 and Task 3 config.
- Reflection cycle: Task 9.
- Proactive behavior with cooldowns and quiet hours: Task 3 policy, Task 8 dispatcher, Task 10 wrapper.
- Error handling: Task 2 broken JSON backup, Task 4 SQLite init, Task 6 degraded scan, Task 8 blocked action records.
- Testing: Tasks 1-4, 6, 8, 9, and 11.
