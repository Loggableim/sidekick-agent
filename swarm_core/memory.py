"""Evidence-governed, project-local Swarm memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .store import ProjectSwarmStore


MEMORY_KINDS = frozenset({"fact", "opinion", "decision", "evidence"})
MEMORY_LIFECYCLES = frozenset({"active", "stale", "expired"})


@dataclass(frozen=True)
class MemoryItem:
    """One immutable claim with explicit provenance and lifecycle state."""

    item_id: str
    kind: str
    claim_key: str
    statement: str
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    lifecycle: str
    created_at: datetime
    updated_at: datetime
    revalidated_at: datetime | None
    revalidate_after: datetime | None
    expires_at: datetime | None
    lesson_opt_in: bool
    redacted_statement: str | None


@dataclass(frozen=True)
class ClarificationTask:
    """A durable request to resolve contradictory same-key statements."""

    clarification_id: str
    kind: str
    claim_key: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryEvent:
    """An auditable project-memory event independent from any individual run."""

    event_id: str
    sequence: int
    timestamp: datetime
    event_type: str
    payload: dict[str, object]


class ProjectMemory:
    """Store classified claims without automatic sync, overwrite, or deletion."""

    def __init__(self, store: ProjectSwarmStore) -> None:
        self.store = store

    def remember(
        self,
        kind: str,
        statement: str,
        *,
        claim_key: str,
        source_refs: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        revalidate_after: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem:
        """Persist a claim, preserving its kind and creating conflicts explicitly."""
        normalized_kind = _require_kind(kind)
        normalized_statement = _require_text(statement, "Memory statement")
        normalized_claim_key = _require_text(claim_key, "Memory claim key")
        revalidate_after, expires_at = _normalize_deadlines(
            revalidate_after,
            expires_at,
        )
        data, _created = self.store.remember_memory_item(
            kind=normalized_kind,
            statement=normalized_statement,
            claim_key=normalized_claim_key,
            source_refs=_normalize_references(source_refs, "source"),
            evidence_refs=_normalize_references(evidence_refs, "evidence"),
            revalidate_after=revalidate_after,
            expires_at=expires_at,
        )
        return _to_memory_item(data)

    def get(self, item_id: str, *, now: datetime | None = None) -> MemoryItem | None:
        data = self.store.get_memory_item(item_id)
        return _to_memory_item(data, now=_read_now(now)) if data is not None else None

    def list(
        self,
        *,
        audit: bool = False,
        now: datetime | None = None,
    ) -> list[MemoryItem]:
        """Return active memory by default, or every lifecycle state for audit."""
        read_now = _read_now(now)
        items = [
            _to_memory_item(data, now=read_now)
            for data in self.store.list_memory_items(audit=True)
        ]
        if audit:
            return items
        return [item for item in items if item.lifecycle == "active"]

    def mark_stale(self, item_id: str) -> MemoryItem:
        return self._set_lifecycle(item_id, "stale")

    def expire(self, item_id: str) -> MemoryItem:
        """Mark a claim expired while retaining its statement and evidence for audit."""
        return self._set_lifecycle(item_id, "expired")

    def revalidate(
        self,
        item_id: str,
        *,
        revalidate_after: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem:
        """Restore a claim and explicitly replace its persisted deadlines."""
        revalidate_after, expires_at = _normalize_deadlines(
            revalidate_after,
            expires_at,
        )
        return _to_memory_item(
            self.store.revalidate_memory_item(
                item_id,
                revalidate_after=revalidate_after,
                expires_at=expires_at,
            )
        )

    def mark_lesson_opt_in(
        self,
        item_id: str,
        redacted_statement: str,
    ) -> MemoryItem:
        """Explicitly mark a locally redacted statement as exportable lesson text."""
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"Unknown memory item: {item_id}")
        redacted_statement = _require_text(
            redacted_statement,
            "Redacted lesson statement",
        )
        if redacted_statement == item.statement:
            raise ValueError("Redacted lesson statement must differ from the original")
        data = self.store.mark_memory_lesson_opt_in(
            item_id,
            redacted_statement=redacted_statement,
        )
        return _to_memory_item(data)

    def list_clarifications(self) -> list[ClarificationTask]:
        return [
            _to_clarification_task(data)
            for data in self.store.list_clarification_tasks()
        ]

    def list_events(self) -> list[MemoryEvent]:
        return [_to_memory_event(data) for data in self.store.list_memory_events()]

    def list_exportable_lessons(
        self,
        *,
        now: datetime | None = None,
    ) -> list[MemoryItem]:
        """Return only locally marked redactions; callers never receive source data."""
        read_now = _read_now(now)
        return [
            item
            for data in self.store.list_exportable_memory_lessons()
            if (item := _to_memory_item(data, now=read_now)).lifecycle == "active"
        ]

    def _set_lifecycle(self, item_id: str, lifecycle: str) -> MemoryItem:
        if lifecycle not in MEMORY_LIFECYCLES:
            raise ValueError(f"Unsupported memory lifecycle: {lifecycle}")
        return _to_memory_item(
            self.store.update_memory_lifecycle(item_id, lifecycle=lifecycle)
        )


def _require_kind(kind: str) -> str:
    normalized = _require_text(kind, "Memory kind").lower()
    if normalized not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    return normalized


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _normalize_references(references: Iterable[str], label: str) -> tuple[str, ...]:
    if isinstance(references, str):
        raise TypeError(f"{label.title()} references must be an iterable of strings")
    normalized = tuple(
        _require_text(reference, f"{label.title()} reference")
        for reference in references
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label.title()} references must not contain duplicates")
    return normalized


def _normalize_deadlines(
    revalidate_after: datetime | None,
    expires_at: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    normalized_revalidate_after = _normalize_timestamp(
        revalidate_after,
        "Revalidate-after deadline",
    )
    normalized_expires_at = _normalize_timestamp(expires_at, "Expiry deadline")
    if (
        normalized_revalidate_after is not None
        and normalized_expires_at is not None
        and normalized_revalidate_after >= normalized_expires_at
    ):
        raise ValueError("Revalidate-after deadline must be before expiry deadline")
    return normalized_revalidate_after, normalized_expires_at


def _normalize_timestamp(value: datetime | None, label: str) -> datetime | None:
    if value is None:
        return None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _read_now(now: datetime | None) -> datetime:
    return _normalize_timestamp(now, "Read time") or datetime.now(timezone.utc)


def _effective_lifecycle(
    durable_lifecycle: str,
    revalidate_after: datetime | None,
    expires_at: datetime | None,
    now: datetime,
) -> str:
    if durable_lifecycle != "active":
        return durable_lifecycle
    if expires_at is not None and now >= expires_at:
        return "expired"
    if revalidate_after is not None and now >= revalidate_after:
        return "stale"
    return "active"


def _to_memory_item(
    data: dict[str, object],
    *,
    now: datetime | None = None,
) -> MemoryItem:
    now = _read_now(now)
    revalidate_after = data["revalidate_after"]
    expires_at = data["expires_at"]
    return MemoryItem(
        item_id=str(data["item_id"]),
        kind=str(data["kind"]),
        claim_key=str(data["claim_key"]),
        statement=str(data["statement"]),
        source_refs=tuple(data["source_refs"]),  # type: ignore[arg-type]
        evidence_refs=tuple(data["evidence_refs"]),  # type: ignore[arg-type]
        lifecycle=_effective_lifecycle(
            str(data["lifecycle"]),
            revalidate_after,  # type: ignore[arg-type]
            expires_at,  # type: ignore[arg-type]
            now,
        ),
        created_at=data["created_at"],  # type: ignore[arg-type]
        updated_at=data["updated_at"],  # type: ignore[arg-type]
        revalidated_at=data["revalidated_at"],  # type: ignore[arg-type]
        revalidate_after=revalidate_after,  # type: ignore[arg-type]
        expires_at=expires_at,  # type: ignore[arg-type]
        lesson_opt_in=bool(data["lesson_opt_in"]),
        redacted_statement=data["redacted_statement"],  # type: ignore[arg-type]
    )


def _to_clarification_task(data: dict[str, object]) -> ClarificationTask:
    return ClarificationTask(
        clarification_id=str(data["clarification_id"]),
        kind=str(data["kind"]),
        claim_key=str(data["claim_key"]),
        status=str(data["status"]),
        created_at=data["created_at"],  # type: ignore[arg-type]
        updated_at=data["updated_at"],  # type: ignore[arg-type]
    )


def _to_memory_event(data: dict[str, object]) -> MemoryEvent:
    return MemoryEvent(
        event_id=str(data["event_id"]),
        sequence=int(data["sequence"]),
        timestamp=data["timestamp"],  # type: ignore[arg-type]
        event_type=str(data["event_type"]),
        payload=dict(data["payload"]),  # type: ignore[arg-type]
    )
