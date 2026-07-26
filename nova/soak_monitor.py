"""Persistent acceptance monitor for the Nova Entity Runtime v2 soak."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


class SoakMonitor:
    def __init__(self, path: Path, *, duration_hours: int = 24,
                 now: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.duration_hours = duration_hours
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def sample(self, *, state_revision: int, mind_process_count: int,
               reflection_queue_depth: int, duplicate_action_correlations: int = 0,
               duplicate_voice_responses: int = 0, raw_audio_events: int = 0) -> dict[str, Any]:
        now = self._now()
        data = self._read()
        if not data:
            data = {
                "schema_version": 1,
                "started_at": now.isoformat(),
                "required_hours": self.duration_hours,
                "samples": [],
                "violations": [],
            }
        samples = data.setdefault("samples", [])
        previous = samples[-1] if samples else None
        violations: list[dict[str, Any]] = data.setdefault("violations", [])

        def flag(code: str, detail: str) -> None:
            if not any(item.get("code") == code and item.get("detail") == detail for item in violations):
                violations.append({"timestamp": now.isoformat(), "code": code, "detail": detail})

        if mind_process_count != 1:
            flag("mind_process_count", f"expected 1, observed {mind_process_count}")
        if reflection_queue_depth < 0:
            flag("invalid_queue_depth", str(reflection_queue_depth))
        if duplicate_action_correlations:
            flag("duplicate_action_correlation", str(duplicate_action_correlations))
        if duplicate_voice_responses:
            flag("duplicate_voice_response", str(duplicate_voice_responses))
        if raw_audio_events:
            flag("raw_audio_persisted", str(raw_audio_events))
        if previous and state_revision < int(previous.get("state_revision", 0)):
            flag("state_revision_regressed", f"{previous.get('state_revision')} -> {state_revision}")
        samples.append({
            "timestamp": now.isoformat(),
            "state_revision": int(state_revision),
            "mind_process_count": int(mind_process_count),
            "reflection_queue_depth": int(reflection_queue_depth),
            "duplicate_action_correlations": int(duplicate_action_correlations),
            "duplicate_voice_responses": int(duplicate_voice_responses),
            "raw_audio_events": int(raw_audio_events),
        })
        data["samples"] = samples[-400:]
        started = datetime.fromisoformat(str(data["started_at"]).replace("Z", "+00:00"))
        due = started + timedelta(hours=int(data.get("required_hours", self.duration_hours)))
        data["due_at"] = due.isoformat()
        data["last_sample_at"] = now.isoformat()
        data["elapsed_hours"] = round(max(0.0, (now - started).total_seconds() / 3600), 3)
        data["complete"] = now >= due
        data["passed"] = bool(data["complete"] and not violations)
        self._write(data)
        return data

    def status(self) -> dict[str, Any]:
        return self._read()
