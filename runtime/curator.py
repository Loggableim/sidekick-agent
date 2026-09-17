"""Skill curator — lifecycle management for agent-created skills.

This module is referenced by ``cli/curator.py`` (status/run/pause/resume) and
by the gateway cron ticker (``runtime/gateway/run.py``) but was never migrated
into this repository — the legacy migration scripts referenced an
``agent/curator.py`` that did not come across. It is re-implemented here
against the contracts those callers already rely on:

    is_enabled()             -> bool
    load_state()             -> dict
    save_state(state)        -> None
    set_paused(bool)         -> None
    get_interval_hours()     -> int
    get_min_idle_hours()     -> int
    get_stale_after_days()   -> int
    get_archive_after_days() -> int
    run_curator_review(on_summary=None, synchronous=True, dry_run=False) -> dict
    maybe_run_curator(idle_for_seconds=inf, on_summary=None) -> None

Lifecycle states are owned by ``tools/skill_usage.py``:

    active -> stale -> archived   (archive is recoverable — never deleted)
    pinned bypasses all automatic transitions.

Safety properties:

  - Only agent-created skills are ever touched (provenance via
    ``skill_usage.agent_created_report()``; bundled/hub skills are excluded).
  - A pre-run snapshot is taken through ``runtime/curator_backup.py`` before
    any mutating pass. Dry-run never mutates and never snapshots.
  - The LLM review pass is **report-only**: it may suggest consolidations but
    never rewrites, renames, or archives skills on its own.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_INTERVAL_HOURS = 24 * 7
_DEFAULT_MIN_IDLE_HOURS = 2
_DEFAULT_STALE_AFTER_DAYS = 30
_DEFAULT_ARCHIVE_AFTER_DAYS = 90


def _load_config() -> Dict[str, Any]:
    """Read the ``curator`` section of config.yaml. Never raises."""
    try:
        from sidekick_cli.config import load_config

        cfg = load_config()
    except Exception as e:
        logger.debug("curator: failed to load config: %s", e)
        return {}
    if not isinstance(cfg, dict):
        return {}
    cur = cfg.get("curator") or {}
    return cur if isinstance(cur, dict) else {}


def _cfg_int(key: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(_load_config().get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def is_enabled() -> bool:
    """Default ON — matches the config default (``curator.enabled: true``)."""
    return bool(_load_config().get("enabled", True))


def get_interval_hours() -> int:
    return _cfg_int("interval_hours", _DEFAULT_INTERVAL_HOURS, minimum=1)


def get_min_idle_hours() -> int:
    return _cfg_int("min_idle_hours", _DEFAULT_MIN_IDLE_HOURS, minimum=0)


def get_stale_after_days() -> int:
    return _cfg_int("stale_after_days", _DEFAULT_STALE_AFTER_DAYS, minimum=1)


def get_archive_after_days() -> int:
    return _cfg_int("archive_after_days", _DEFAULT_ARCHIVE_AFTER_DAYS, minimum=1)


# ---------------------------------------------------------------------------
# State file (~/.sidekick/skills/.curator_state)
# ---------------------------------------------------------------------------

def _skills_dir() -> Path:
    from runtime._compat.shim_constants import get_sidekick_home

    return get_sidekick_home() / "skills"


def _state_file() -> Path:
    return _skills_dir() / ".curator_state"


def _reports_dir() -> Path:
    return _skills_dir() / ".curator_reports"


def load_state() -> Dict[str, Any]:
    """Read the curator state JSON. Returns ``{}`` on missing/corrupt file."""
    path = _state_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("curator: failed to read state: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: Dict[str, Any]) -> None:
    """Persist the curator state. Best-effort — failures log at debug."""
    if not isinstance(state, dict):
        return
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.debug("curator: failed to save state: %s", e)


def set_paused(paused: bool) -> None:
    state = load_state()
    state["paused"] = bool(paused)
    save_state(state)


def is_paused() -> bool:
    return bool(load_state().get("paused", False))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _idle_days(record: Dict[str, Any]) -> Optional[int]:
    """Days since the skill's last activity (view / use / patch).

    Falls back to ``created_at`` so a skill that was authored but never used
    can still be pruned — otherwise never-touched skills would be immortal.
    Returns None only when both fields are missing or unparseable.
    """
    ts = record.get("last_activity_at") or record.get("created_at")
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _hours_since_last_run(state: Dict[str, Any]) -> Optional[float]:
    dt = _parse_iso(state.get("last_run_at"))
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Automatic transitions (deterministic — no LLM involved)
# ---------------------------------------------------------------------------

def _auto_transitions(*, dry_run: bool = False) -> Dict[str, int]:
    """Apply stale/archive/reactivate transitions based on usage telemetry.

    Returns a report dict with keys ``checked``, ``marked_stale``,
    ``archived``, ``reactivated``. Pinned and already-archived skills are
    skipped. In dry-run mode nothing is mutated but the counts still reflect
    what *would* happen.
    """
    from tools import skill_usage

    stale_days = get_stale_after_days()
    archive_days = get_archive_after_days()

    checked = marked_stale = archived = reactivated = 0

    try:
        report = skill_usage.agent_created_report()
    except Exception as e:
        logger.debug("curator: agent_created_report failed: %s", e)
        return {
            "checked": 0,
            "marked_stale": 0,
            "archived": 0,
            "reactivated": 0,
        }

    for rec in report:
        if rec.get("pinned"):
            continue
        current_state = rec.get("state") or skill_usage.STATE_ACTIVE
        if current_state == skill_usage.STATE_ARCHIVED:
            continue

        idle = _idle_days(rec)
        if idle is None:
            continue
        checked += 1
        name = rec.get("name")
        if not name:
            continue

        if idle >= archive_days:
            if dry_run:
                archived += 1
            else:
                ok, msg = skill_usage.archive_skill(name)
                if ok:
                    archived += 1
                else:
                    logger.debug("curator: archive %s failed: %s", name, msg)
        elif idle >= stale_days:
            if current_state != skill_usage.STATE_STALE:
                if not dry_run:
                    skill_usage.set_state(name, skill_usage.STATE_STALE)
                marked_stale += 1
        else:
            # Recently active again — lift a stale mark.
            if current_state == skill_usage.STATE_STALE:
                if not dry_run:
                    skill_usage.set_state(name, skill_usage.STATE_ACTIVE)
                reactivated += 1

    return {
        "checked": checked,
        "marked_stale": marked_stale,
        "archived": archived,
        "reactivated": reactivated,
    }


# ---------------------------------------------------------------------------
# LLM review pass (report-only)
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = (
    "You are the skill curator for a personal AI agent. You review the agent's "
    "self-authored skills and suggest consolidations. You NEVER delete anything. "
    "Reply with strict JSON only."
)

_LLM_USER_TEMPLATE = (
    "Review these agent-created skills. Identify groups that overlap enough to "
    "be consolidated into one umbrella skill, and flag skills that look "
    "obsolete. Respond with JSON of this shape:\n"
    '{{"consolidations": [{{"into": "<umbrella name>", "from": ["<skill>", ...], '
    '"reason": "<short>"}}], "obsolete": [{{"name": "<skill>", "reason": "<short>"}}], '
    '"notes": "<optional>"}}\n\n'
    "Skills:\n{skills}\n"
)


def _llm_review_pass(skills: List[Dict[str, Any]], *, on_summary=None) -> Dict[str, Any]:
    """Run a report-only LLM review. Never mutates skills.

    Returns a dict with ``ran`` (bool), plus ``suggestions`` and
    ``report_path`` on success, or ``reason`` on failure.
    """
    if not skills:
        return {"ran": False, "reason": "no agent-created skills"}

    try:
        from runtime.auxiliary_client import call_llm
    except Exception as e:
        return {"ran": False, "reason": f"auxiliary client unavailable: {e}"}

    lines = []
    for rec in skills[:60]:
        idle = _idle_days(rec)
        lines.append(
            f"- {rec.get('name')} (state={rec.get('state', 'active')}, "
            f"use={rec.get('use_count', 0)}, view={rec.get('view_count', 0)}, "
            f"patches={rec.get('patch_count', 0)}, idle_days={idle})"
        )

    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {"role": "user", "content": _LLM_USER_TEMPLATE.format(skills="\n".join(lines))},
    ]

    try:
        response = call_llm(task="curator", messages=messages, max_tokens=1500)
        text = ""
        try:
            text = response.choices[0].message.content or ""
        except Exception:
            text = str(response)
    except Exception as e:
        logger.debug("curator: LLM review pass failed: %s", e)
        return {"ran": False, "reason": f"llm call failed: {e}"}

    suggestions: Dict[str, Any] = {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip a fenced code block if the model wrapped its JSON.
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            suggestions = parsed
    except (json.JSONDecodeError, TypeError):
        suggestions = {"notes": text[:1000]}

    report_path = None
    try:
        reports = _reports_dir()
        reports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        report_path = reports / f"{stamp}.json"
        report_path.write_text(
            json.dumps(
                {"generated_at": _now_iso(), "suggestions": suggestions},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug("curator: failed to write report: %s", e)
        report_path = None

    if on_summary:
        try:
            count = len(suggestions.get("consolidations") or [])
            on_summary(f"llm: {count} consolidation suggestion(s) — report only")
        except Exception:
            pass

    return {
        "ran": True,
        "suggestions": suggestions,
        "report_path": str(report_path) if report_path else None,
    }


# ---------------------------------------------------------------------------
# Review pass
# ---------------------------------------------------------------------------

def run_curator_review(
    on_summary=None,
    synchronous: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run one curator review pass.

    Args:
        on_summary: Optional callback receiving human-readable progress lines.
        synchronous: When False the LLM pass is skipped (the caller has
            already returned); auto-transitions always run inline.
        dry_run: Report only — no snapshots, no state changes, no archives.

    Returns a result dict::

        {
            "auto_transitions": {"checked": N, "marked_stale": N,
                                 "archived": N, "reactivated": N},
            "llm": {"ran": bool, ...},
            "dry_run": bool,
        }
    """
    def _emit(msg: str) -> None:
        if on_summary:
            try:
                on_summary(msg)
            except Exception:
                pass

    # 1. Pre-run snapshot (mutating passes only).
    if not dry_run:
        try:
            from runtime import curator_backup

            snap = curator_backup.snapshot_skills(reason="pre-run")
            if snap is not None:
                _emit(f"backup: snapshot at {snap.name}")
        except Exception as e:
            logger.debug("curator: pre-run snapshot failed: %s", e)

    # 2. Deterministic auto-transitions.
    auto = _auto_transitions(dry_run=dry_run)
    if dry_run:
        _emit(
            f"auto (preview): checked={auto['checked']} "
            f"would_stale={auto['marked_stale']} would_archive={auto['archived']} "
            f"would_reactivate={auto['reactivated']}"
        )
    else:
        _emit(
            f"auto: checked={auto['checked']} stale={auto['marked_stale']} "
            f"archived={auto['archived']} reactivated={auto['reactivated']}"
        )

    # 3. LLM review pass (report-only).
    llm_result: Dict[str, Any] = {"ran": False, "reason": "skipped"}
    if synchronous:
        try:
            from tools import skill_usage

            skills = skill_usage.agent_created_report()
        except Exception as e:
            skills = []
            logger.debug("curator: could not list skills for LLM pass: %s", e)
        llm_result = _llm_review_pass(skills, on_summary=_emit)
        if not llm_result.get("ran"):
            _emit(f"llm: skipped ({llm_result.get('reason', 'unknown')})")
    else:
        _emit("llm: deferred (background mode)")

    # 4. State bookkeeping.
    # Dry-run still records the report path (the CLI tells users to read the
    # report via `sidekick curator status`), but never touches last_run_at /
    # run_count / last_run_summary — those gate the real schedule.
    if dry_run:
        if llm_result.get("report_path"):
            state = load_state()
            state["last_report_path"] = llm_result["report_path"]
            save_state(state)
    else:
        state = load_state()
        state["last_run_at"] = _now_iso()
        state["run_count"] = int(state.get("run_count") or 0) + 1
        if llm_result.get("report_path"):
            state["last_report_path"] = llm_result["report_path"]

        summary_lines = [
            f"auto: checked={auto['checked']} stale={auto['marked_stale']} "
            f"archived={auto['archived']} reactivated={auto['reactivated']}"
        ]
        if llm_result.get("ran"):
            count = len((llm_result.get("suggestions") or {}).get("consolidations") or [])
            summary_lines.append(f"llm: {count} consolidation suggestion(s)")
        else:
            summary_lines.append(f"llm: {llm_result.get('reason', 'skipped')}")
        state["last_run_summary"] = "\n".join(summary_lines)
        save_state(state)

    return {
        "auto_transitions": auto,
        "llm": llm_result,
        "dry_run": bool(dry_run),
    }


# ---------------------------------------------------------------------------
# Scheduler hook (gateway ticker + CLI startup)
# ---------------------------------------------------------------------------

def maybe_run_curator(
    idle_for_seconds: float = float("inf"),
    on_summary=None,
) -> None:
    """Run a curator pass if the schedule says we're due.

    Called from the gateway cron ticker and from CLI session startup. Runs
    the review in a daemon thread so callers are never blocked.

    First-run deferral: when the curator has never run, the state is seeded
    with ``last_run_at = now`` instead of firing immediately — this keeps a
    fresh skill library from being reviewed the moment the gateway starts.
    """
    if not is_enabled():
        return

    state = load_state()
    if state.get("paused"):
        return

    hours_since = _hours_since_last_run(state)
    if hours_since is None:
        # Never run — seed the schedule and defer the first real pass.
        state["last_run_at"] = _now_iso()
        state.setdefault("run_count", 0)
        state["last_run_summary"] = "auto: deferred first pass (seeded schedule)"
        save_state(state)
        return

    if hours_since < get_interval_hours():
        return

    min_idle_seconds = get_min_idle_hours() * 3600
    if idle_for_seconds < min_idle_seconds:
        return

    def _worker() -> None:
        try:
            run_curator_review(on_summary=on_summary, synchronous=True, dry_run=False)
        except Exception as e:
            logger.debug("curator: background pass failed: %s", e)

    threading.Thread(target=_worker, name="curator-review", daemon=True).start()
