#!/usr/bin/env python3
"""Daily and weekly coherence reflection for Nova."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from nova.agenda import AgendaStore
from nova.autobiography import AutobiographyStore


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


def run_daily_reflection(dry_run: bool = False, agenda: AgendaStore | None = None, bio: AutobiographyStore | None = None) -> dict:
    if dry_run:
        reflection = build_daily_reflection(agenda, bio)
        reflection["mode"] = "dry-run"
        return reflection
    reflection = save_daily_reflection(agenda, bio)
    reflection["mode"] = "live"
    return reflection


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova reflection cycle")
    parser.add_argument("command", choices=["daily"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "daily":
        print(json.dumps(run_daily_reflection(args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
