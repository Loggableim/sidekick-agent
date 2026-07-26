"""Drain and compact Nova's lifecycle reflection queue."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nova.autobiography import AutobiographyStore
from nova.memory_quality import assess_memory_quality
from nova.paths import get_nova_space_root


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _keywords(items: list[dict[str, Any]], limit: int = 12) -> list[str]:
    stop = {"und", "oder", "aber", "der", "die", "das", "ein", "eine", "ich", "du", "nova", "the", "and", "this", "that", "with", "from"}
    words: Counter[str] = Counter()
    for item in items:
        text = f"{item.get('user', '')} {item.get('assistant', '')}".lower()
        for word in re.findall(r"[a-zA-ZäöüÄÖÜß0-9_-]{4,}", text):
            if word not in stop and not word.isdigit():
                words[word] += 1
    return [word for word, _ in words.most_common(limit)]


class ReflectionWorker:
    def __init__(self, space_dir: Path | None = None, bio: AutobiographyStore | None = None):
        self.space_dir = Path(space_dir) if space_dir else get_nova_space_root()
        self.queue_path = self.space_dir / ".lifecycle" / "reflection_queue.json"
        self.archive_dir = self.space_dir / "nova_data" / "entity" / "reflection_archives"
        self.bio = bio or AutobiographyStore()

    def status(self) -> dict[str, Any]:
        queue = _read(self.queue_path, [])
        queue = queue if isinstance(queue, list) else []
        return {
            "queued": sum(1 for item in queue if item.get("status", "queued") == "queued"),
            "total": len(queue),
            "path": str(self.queue_path),
        }

    def drain(self, *, limit: int = 25, compact_backlog_threshold: int = 100) -> dict[str, Any]:
        queue = _read(self.queue_path, [])
        if not isinstance(queue, list) or not queue:
            return {"processed": 0, "remaining": 0, "compacted": False}
        queued = [item for item in queue if item.get("status", "queued") == "queued"]
        if not queued:
            return {"processed": 0, "remaining": 0, "compacted": False}

        compacted = len(queued) >= compact_backlog_threshold
        selected = queued if compacted else queued[: max(1, int(limit))]
        seen: set[str] = set()
        high_signal: list[dict[str, Any]] = []
        low_signal: list[dict[str, Any]] = []
        for item in selected:
            quality = assess_memory_quality(str(item.get("user", "")), str(item.get("assistant", "")), recent_fingerprints=seen)
            item["quality"] = quality
            seen.add(quality["fingerprint"])
            (high_signal if quality["high_quality"] else low_signal).append(item)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.archive_dir / f"reflection-queue-{stamp}.json"
        _write(archive_path, selected)

        summary = {
            "source_count": len(selected),
            "high_signal_count": len(high_signal),
            "low_signal_count": len(low_signal),
            "themes": _keywords(high_signal),
            "source_event_ids": [str(item.get("source_event_id") or "") for item in high_signal[:50]],
            "archive_path": str(archive_path),
        }
        self.bio.record_entity_event({
            "event_id": f"reflection-{stamp}",
            "type": "reflection",
            "source": "reflection_worker",
            "title": "Backlog reflection" if compacted else "Reflection batch",
            "summary": f"Processed {len(selected)} queued experiences; themes: {', '.join(summary['themes']) or 'none'}.",
            "why": "Continuously integrate experience into Nova's autobiographical timeline.",
            "salience": 0.7 if compacted else 0.55,
            "payload": summary,
            "tags": ["reflection", "backlog" if compacted else "batch"],
            "correlation_id": f"reflection-{stamp}",
        })

        selected_ids = {id(item) for item in selected}
        remaining = [item for item in queue if id(item) not in selected_ids]
        _write(self.queue_path, remaining)
        return {
            "processed": len(selected),
            "remaining": sum(1 for item in remaining if item.get("status", "queued") == "queued"),
            "compacted": compacted,
            "summary": summary,
        }
