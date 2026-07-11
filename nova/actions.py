"""Concrete, auditable handlers for Nova Entity Runtime intents."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nova.reflection_worker import ReflectionWorker


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class ActionRegistry:
    def __init__(self, space_dir: Path, *, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None):
        self.space_dir = Path(space_dir)
        self.runner = runner or subprocess.run
        self.handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
            "prioritize_thread": self._prioritize_thread,
            "goal_check": self._goal_check,
            "reflection": self._reflection,
            "inner_voice": self._inner_voice,
            "mind_diary": self._mind_diary,
            "dream": self._dream,
            "hub_speak": self._hub_speak,
            "blog_draft": self._blog_draft,
            "agenda_update": self._agenda_update,
            "aces_cycle": self._aces_cycle,
        }

    def execute(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        action = str(intent.get("action") or "")
        handler = self.handlers.get(action)
        if handler is None:
            return {"ok": False, "status": "unsupported", "action": action, "message": "No concrete handler is registered."}
        try:
            result = handler(intent, state)
        except Exception as exc:
            return {"ok": False, "status": "failed", "action": action, "message": repr(exc), "effects": {}}
        result.setdefault("action", action)
        result.setdefault("status", "done" if result.get("ok") else "failed")
        result.setdefault("effects", {})
        return result

    def _run_script(self, name: str, *args: str, timeout: int = 120) -> dict[str, Any]:
        script = self.space_dir / name
        if not script.exists():
            return {"ok": False, "message": f"Missing script: {name}", "effects": {}}
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        proc = self.runner(
            [sys.executable, str(script), *args], cwd=str(self.space_dir), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        return {
            "ok": proc.returncode == 0,
            "message": (proc.stdout or proc.stderr or "").strip()[-2000:],
            "effects": {"returncode": proc.returncode, "script": name},
        }

    def _prioritize_thread(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        target = intent.get("target") or {}
        thread_id = str(target.get("thread_id") or target.get("topic") or "").strip()
        if not thread_id:
            return {"ok": False, "message": "No concrete continuity thread target was supplied."}
        path = self.space_dir / "continuity_state.json"
        continuity = _read_json(path, {})
        continuity["prioritized_thread"] = {
            "thread_id": thread_id,
            "topic": target.get("topic") or thread_id,
            "next_step": (intent.get("payload") or {}).get("next_step") or "Resume and resolve this thread.",
            "intent_id": intent.get("id") or intent.get("intent_id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        history = continuity.setdefault("prioritized_history", [])
        history.append(dict(continuity["prioritized_thread"]))
        continuity["prioritized_history"] = history[-50:]
        _write_json(path, continuity)
        return {"ok": True, "message": f"Prioritized continuity thread: {target.get('topic') or thread_id}", "effects": {"effect": "thread_prioritized", "thread": continuity["prioritized_thread"]}}

    def _goal_check(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        result = self._run_script("eigenziele.py", "check", timeout=60)
        if result.get("ok"):
            result.setdefault("effects", {})["effect"] = "goals_checked"
        return result

    def _reflection(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        result = ReflectionWorker(self.space_dir).drain(limit=25)
        content = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "need": intent.get("need"),
            "why": intent.get("why"),
            "emotion": state.get("emotion") or {},
            "continuity": (state.get("continuity") or {}).get("open_threads", [])[:5],
            "queue": result,
        }
        path = self.space_dir / "nova_data" / "entity" / "reflections.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(content, ensure_ascii=False) + "\n")
        return {"ok": True, "message": "Created a state-grounded reflection and processed queued experience.", "effects": {"effect": "reflection_persisted", "reflection": content, "path": str(path)}}

    def _inner_voice(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        title = str(intent.get("title") or "Innere Stimme")
        content = str((intent.get("payload") or {}).get("content") or intent.get("why") or "Ich halte einen bedeutsamen inneren Faden fest.")
        result = self._run_script("innere_stimme.py", "think", title, content, "--tags", "entity-runtime", timeout=60)
        if result.get("ok"):
            result.setdefault("effects", {})["effect"] = "inner_voice_persisted"
        return result

    def _mind_diary(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        payload = intent.get("payload") or {}
        content = str(payload.get("content") or intent.get("why") or "Nova reflected on the current state.")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": content,
            "intent_id": intent.get("id") or intent.get("intent_id"),
            "correlation_id": intent.get("correlation_id"),
            "emotion": state.get("emotion") or {},
        }
        path = self.space_dir / "nova_data" / "entity" / "mind_diary.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"ok": True, "message": content, "effects": {"effect": "diary_entry_persisted", "entry": entry, "path": str(path)}}

    def _dream(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        result = self._run_script("dream_cycle.py", "rem", timeout=180)
        try:
            payload = json.loads(str(result.get("message") or "{}"))
        except ValueError:
            payload = {}
        rem = payload.get("rem") if isinstance(payload, dict) else {}
        if isinstance(rem, dict) and rem.get("error"):
            result["ok"] = False
            result["status"] = "deferred"
            result["message"] = str(rem["error"])
            result.setdefault("effects", {})["deferred_reason"] = "no_raw_dreams"
        elif result.get("ok"):
            result.setdefault("effects", {})["effect"] = "dream_cycle_completed"
        return result

    def _hub_speak(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        text = str((intent.get("payload") or {}).get("text") or "").strip()
        if not text:
            return {"ok": False, "message": "No speech text supplied."}
        result = self._run_script("hub.py", "speak", text, timeout=90)
        result.setdefault("effects", {})["spoken_text"] = text
        return result

    def _blog_draft(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        payload = intent.get("payload") or {}
        title = str(payload.get("title") or intent.get("title") or "Nova draft")
        content = str(payload.get("content") or intent.get("why") or "")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.space_dir / "nova_data" / "entity" / "drafts" / f"{stamp}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
        return {"ok": True, "message": f"Created reversible local draft {path.name}.", "effects": {"path": str(path)}}

    def _agenda_update(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        path = self.space_dir / "nova_data" / "entity" / "agenda_maintenance.json"
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "intent_id": intent.get("id") or intent.get("intent_id"),
            "need": intent.get("need"),
            "why": intent.get("why"),
        }
        _write_json(path, snapshot)
        return {"ok": True, "message": "Persisted an agenda-maintenance checkpoint.", "effects": {"path": str(path), "snapshot": snapshot}}

    def _aces_cycle(self, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        script = self.space_dir / "aces_cli.py"
        if not script.exists():
            return {"ok": False, "message": "ACES CLI is not installed."}
        args = [sys.executable, str(script), "--cycle", "--apply" if bool((intent.get("payload") or {}).get("apply", intent.get("apply", False))) else "--dry-run"]
        env = dict(os.environ)
        if bool(intent.get("yolo_enabled")):
            env["NOVA_YOLO_MODE"] = "1"
        proc = self.runner(
            args, cwd=str(self.space_dir), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        try:
            report = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except ValueError:
            report = {"raw": proc.stdout[-2000:]}
        return {
            "ok": proc.returncode == 0,
            "message": str(report.get("message") or proc.stderr[-500:] or "ACES cycle completed."),
            "effects": {"report": report, "returncode": proc.returncode},
        }
