"""Host-owned consumer for the redacted Nova supervision ticker feed.

The handler is intentionally not a shell runner.  It consumes only the fixed
event vocabulary written by the code-owned ticker and delegates execution to
``NovaSpaceSupervisionRuntime``.  That keeps model calls, worktrees, GitHub and
deploys behind the existing supervisor capability/action gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping

from nova.space_supervision_runtime import (
    NovaSpaceSupervisionRuntime,
    SupervisionPulseOutcome,
    append_ticker_outcomes,
    ticker_event_log_path,
)
from nova.space_supervisor import ManagedSpaceSupervisor


_MAX_EVENTS = 512
_SPACE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_REASON_TO_ACTION = {
    "git_change": "swarm_review",
    "kanban_change": "swarm_plan",
    "ci_change": "swarm_verify",
    "ci_failed": "swarm_repair",
    "periodic_check": "swarm_healthcheck",
    "multiple_changes": "swarm_reconcile",
}


def _publish_to_nova_entity(event: Mapping[str, Any]) -> bool:
    """Mirror one already-validated redacted Space signal into Nova memory."""
    try:
        space = str(event.get("space") or "").strip().lower()
        source = str(event.get("source") or "").strip().lower()
        stage = str(event.get("stage") or "").strip().lower()
        status = str(event.get("status") or "").strip().lower()
        reason = str(event.get("reason") or "").strip().lower()
        event_id = str(event.get("event_id") or "").strip().lower()
        if (_SPACE_RE.fullmatch(space) is None or source not in {"git","kanban","ci","heartbeat","bridge"}
                or stage not in {"observed","handled"} or status not in {"pending","handled","failed"}
                or not re.fullmatch(r"[0-9a-f]{16,128}", event_id)):
            return False
        from nova.entity_kernel import EntityKernel
        EntityKernel().perceive({
            "event_id": f"space-signal-{event_id}",
            "type": "space_supervision_signal",
            "source": "nova_space_supervision",
            "title": f"Space signal: {space}",
            "summary": f"{source} {reason} is {status}.",
            "why": "Governed Space supervision feedback.",
            "salience": 0.65 if status in {"pending","failed"} else 0.35,
            "visibility": "private",
            "correlation_id": event_id,
            "payload": {"space":space,"source":source,"stage":stage,"status":status,"reason":reason},
            "tags": ["space-supervision", "redacted", "entity-feed"],
        })
        return True
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class TickerHandlerResult:
    """Bounded, public visibility for one host-owned consumer pass."""

    pending_spaces: tuple[str, ...]
    outcomes: tuple[SupervisionPulseOutcome, ...]
    resonance_consumed: int = 0


def consume_pending_events(
    *,
    supervisor: ManagedSpaceSupervisor,
    runtime: NovaSpaceSupervisionRuntime,
    publish_entity: bool = False,
) -> TickerHandlerResult:
    """Consume current pending events through the governed runtime exactly once.

    The SQLite supervision state is authoritative for pending work.  The JSONL
    feed is only an additional, redacted wake-up/index; malformed or stale
    lines are ignored.  A second invocation after a successful pulse sees no
    pending state and therefore cannot dispatch a duplicate run.
    """
    if not isinstance(supervisor, ManagedSpaceSupervisor):
        raise TypeError("ticker handler supervisor is invalid")
    if not isinstance(runtime, NovaSpaceSupervisionRuntime):
        raise TypeError("ticker handler runtime is invalid")
    indexed_by_event = _pending_events_from_log(supervisor)
    indexed = set(indexed_by_event.values())
    status_by_space = {item.target_key: item for item in runtime.status()}
    pending = tuple(
        sorted(
            {
                space
                for space, item in status_by_space.items()
                if (item.pending or item.last_outcome == "paused") and _eligible(supervisor, space)
            }
            | {space for space in indexed if _eligible(supervisor, space)}
        )
    )
    # Resonance memory is a bounded, read-only ticker consumer; failures
    # must never block governed dispatch. It runs even without pending work.
    try:
        from nova.resonance_memory import consume_ticker_resonance_memory
        resonance_result = consume_ticker_resonance_memory(supervisor)
        resonance_consumed = max(0, min(int(resonance_result.consumed), 64))
        if publish_entity:
            from nova.resonance_memory import TickerResonanceMemory
            TickerResonanceMemory(supervisor=supervisor).publish_pending(_publish_to_nova_entity)
    except Exception:
        resonance_consumed = 0

    # Always give the runtime one cheap pulse, even when the durable ledger
    # currently has no pending rows. Production injects a read-only governance
    # snapshot provider which seeds the first periodic intent for a currently
    # enrolled YOLO Space. Returning above when `status()` is empty would
    # make that provider unreachable, leaving a quiet Space permanently idle
    # until an external Git/Kanban/CI event arrives. The runtime remains the
    # authority and performs no work when no provider or pending state exists.
    outcomes = runtime.pulse()
    if not pending and not outcomes:
        return TickerHandlerResult((), (), resonance_consumed)
    event_ids_by_space: dict[str, str] = {}
    for event_id, space in indexed_by_event.items():
        event_ids_by_space[space] = event_id
    append_ticker_outcomes(supervisor, outcomes, event_ids=event_ids_by_space)
    # Outcomes are appended after the first resonance pass. Consume this
    # bounded terminal delta immediately so Nova sees the final state in the
    # same host cycle instead of retaining observed/pending forever.
    try:
        from nova.resonance_memory import consume_ticker_resonance_memory
        terminal_result = consume_ticker_resonance_memory(supervisor)
        resonance_consumed = min(64, resonance_consumed + max(0, int(terminal_result.consumed)))
        if publish_entity:
            from nova.resonance_memory import TickerResonanceMemory
            TickerResonanceMemory(supervisor=supervisor).publish_pending(_publish_to_nova_entity)
    except Exception:
        pass
    remaining_pending = tuple(
        space for space in pending
        if not any(
            getattr(outcome, "target_key", "") == space
            and str(getattr(outcome, "status", "")).strip().lower() == "auto_resumed"
            for outcome in outcomes
        )
    )
    return TickerHandlerResult(remaining_pending, outcomes, resonance_consumed)


def _eligible(supervisor: ManagedSpaceSupervisor, target_key: str) -> bool:
    try:
        governance = supervisor.current_governance(target_key)
    except Exception:
        return False
    return bool(
        governance is not None
        and governance.yolo is True
        and governance.enrolled is True
    )


def _pending_events_from_log(supervisor: ManagedSpaceSupervisor) -> dict[str, str]:
    """Index only unhandled event records; never create or mutate the log."""
    path = ticker_event_log_path(supervisor)
    if not path.is_file():
        return {}
    latest: dict[str, str] = {}
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - (_MAX_EVENTS * 512)))
            raw_tail = handle.read()
        if size > len(raw_tail):
            _, _, raw_tail = raw_tail.partition(b"\n")
        lines = raw_tail.splitlines()[-_MAX_EVENTS:]
    except OSError:
        return {}
    # The JSONL file is only a wake-up/index. It is not an authority and can
    # be truncated or tampered with independently of the supervisor ledger.
    # Observed records must therefore carry a signal identity that was already
    # durably accepted by ``NovaSpaceSupervisionRuntime.ingest_signal``. This
    # keeps a forged local line from waking an enrolled Space autonomously.
    known_signal_digests: set[str] = set()
    heartbeat_checkpoints: dict[str, int] = {}
    try:
        with supervisor._supervision_state_reader() as connection:
            if connection is not None:
                rows = connection.execute(
                    "SELECT signal_digest FROM nova_supervision_signals"
                ).fetchall()
                known_signal_digests = {
                    str(row[0]).strip().lower()
                    for row in rows
                    if re.fullmatch(r"[0-9a-f]{64}", str(row[0]).strip().lower())
                }
                checkpoint_rows = connection.execute(
                    "SELECT target_key, latest_bucket FROM nova_supervision_heartbeat_checkpoints"
                ).fetchall()
                heartbeat_checkpoints = {
                    str(row[0]).strip().lower(): int(row[1])
                    for row in checkpoint_rows
                    if _SPACE_RE.fullmatch(str(row[0]).strip().lower()) and 0 <= int(row[1]) <= 10**15
                }
    except (AttributeError, OSError, RuntimeError, sqlite3.Error):
        # A missing/old ledger is not evidence of an accepted event. The
        # durable runtime state remains the source of truth for dispatch.
        known_signal_digests = set()
    for raw_line in lines:
        try:
            item = json.loads(raw_line.decode("utf-8", errors="replace"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(item, Mapping):
            continue
        space = str(item.get("space") or "").strip().lower()
        if _SPACE_RE.fullmatch(space) is None:
            continue
        reason = str(item.get("reason") or "").strip().lower()
        event_id = str(item.get("event_id") or "").strip().lower()
        source = str(item.get("source") or "").strip().lower()
        bucket = item.get("heartbeat_bucket")
        heartbeat_valid = (
            source == "heartbeat" and reason == "periodic_check"
            and space in heartbeat_checkpoints and isinstance(bucket, int)
            and bucket == heartbeat_checkpoints[space]
        )
        if not event_id or (event_id not in known_signal_digests and not heartbeat_valid):
            continue
        status = str(item.get("status") or "").strip().lower()
        stage = str(item.get("stage") or "").strip().lower()
        if stage == "handled" or status in {"handled", "failed"}:
            latest.pop(event_id, None)
            continue
        if reason not in _REASON_TO_ACTION:
            continue
        if stage == "observed" or status == "pending":
            latest[event_id] = space
    return latest


__all__ = ["TickerHandlerResult", "consume_pending_events"]
