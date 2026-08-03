"""Bounded private Telegram notifications for Nova-managed Spaces.

This module has no credential loading, default chat, group fallback, or
provider implementation.  A future host must explicitly inject a private
target and sender after its own credential checks.  Durable local claims give
at-most-once *send attempts*; Telegram itself cannot promise global delivery
exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import os
import re
import sqlite3
from typing import Mapping, Protocol
import urllib.request

from nova.space_supervisor import ManagedSpaceSupervisor
from runtime.redact import redact_sensitive_text


_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_BLOCKER_CODES = frozenset(
    {
        "governance_revoked",
        "ticker_thread_escalated",
        "root_mismatch",
        "dispatch_failed",
        "verification_failed",
        "tests_failed",
        "deployment_failed",
        "no_eligible_model",
    }
)
_DIGEST_STATUSES = frozenset({"completed", "blocked", "paused", "active"})
_MAX_MESSAGE_LENGTH = 280
_NOTIFICATION_CLAIM_SCHEMA_OBJECTS = ("nova_notification_claims",)


class PrivateTelegramSender(Protocol):
    """Minimal injected transport; it receives a fixed rendered template only."""

    def send_private(self, chat_id: int, text: str) -> object:
        """Send one already-redacted message to the explicit private target."""


@dataclass(frozen=True, slots=True)
class PrivateTelegramTarget:
    """An explicit private chat; no token, group, or fallback is represented."""

    chat_id: int

    def __post_init__(self) -> None:
        _private_chat_id(self.chat_id)

    @classmethod
    def from_config(cls, value: Mapping[str, object]) -> "PrivateTelegramTarget":
        if not isinstance(value, Mapping) or set(value) != {"chat_id", "chat_type"}:
            raise ValueError("Telegram target must be one explicit private chat")
        if value.get("chat_type") != "private":
            raise ValueError("Telegram target must declare chat_type private")
        return cls(chat_id=_private_chat_id(value.get("chat_id")))


class TelegramBotPrivateSender:
    """Minimal Telegram Bot API sender bound to one injected token."""

    def __init__(self, token: str, *, endpoint: str = "https://api.telegram.org") -> None:
        if not isinstance(token, str) or not token.strip() or len(token) > 512:
            raise ValueError("Telegram bot token is invalid")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ValueError("Telegram endpoint must use HTTPS")
        self._token = token.strip()
        self._endpoint = endpoint.rstrip("/")

    def send_private(self, chat_id: int, text: str) -> object:
        payload = json.dumps({"chat_id": chat_id, "text": text}, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self._endpoint}/bot{self._token}/sendMessage",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise RuntimeError("Telegram notification was not accepted")
        return result



def build_env_notifier(
    supervisor: ManagedSpaceSupervisor,
    *,
    env: Mapping[str, str] | None = None,
) -> "NovaTelegramNotifications | None":
    """Build only from an explicit private target and token.

    No target discovery, update polling, group fallback, or default activation
    is permitted. Missing or malformed configuration keeps notifications off.
    """
    if not isinstance(supervisor, ManagedSpaceSupervisor):
        raise TypeError("Nova Telegram notifications require a supervisor")
    source = os.environ if env is None else env
    token = str(source.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = str(source.get("NOVA_TELEGRAM_CHAT_ID", "") or "").strip()
    chat_type = str(source.get("NOVA_TELEGRAM_CHAT_TYPE", "") or "").strip().lower()
    if not token or not chat_id or chat_type != "private":
        return None
    try:
        target = PrivateTelegramTarget.from_config(
            {"chat_id": int(chat_id), "chat_type": chat_type}
        )
        sender = TelegramBotPrivateSender(token)
    except (TypeError, ValueError):
        return None
    return NovaTelegramNotifications(
        supervisor=supervisor,
        target=target,
        sender=sender,
        allowed_space_ids=_discover_managed_space_ids(supervisor),
    )
class NovaTelegramNotifications:
    """Fixed-template notifier with central durable at-most-once claims."""

    def __init__(
        self,
        *,
        supervisor: ManagedSpaceSupervisor,
        target: PrivateTelegramTarget,
        sender: PrivateTelegramSender,
        allowed_space_ids: set[str] | frozenset[str],
    ) -> None:
        if not isinstance(supervisor, ManagedSpaceSupervisor):
            raise TypeError("Nova Telegram notifications require a supervisor")
        if not isinstance(target, PrivateTelegramTarget):
            raise TypeError("Nova Telegram notifications require a private target")
        if not callable(getattr(sender, "send_private", None)):
            raise TypeError("Nova Telegram notifications require a private sender")
        self._supervisor = supervisor
        self._target = target
        self._sender = sender
        # Host-resolved YOLO+enrolled IDs form the notification boundary.
        if not isinstance(allowed_space_ids, (set, frozenset)):
            raise TypeError("Nova Telegram notifications require managed Space IDs")
        self._allowed_space_ids = frozenset(
            value for value in allowed_space_ids
            if isinstance(value, str) and _OPAQUE_ID_RE.fullmatch(value)
        )

    def send_blocker(
        self,
        *,
        space_id: str,
        display_name: str,
        run_id: str,
        blocker_code: str,
    ) -> str:
        """Send one fixed blocker template, at most once for its durable key."""
        # Callers may retain a UI label for compatibility, but no caller/model
        # text is allowed to become part of an external notification.
        del display_name
        space = _opaque_id(space_id, "space id")
        run = _opaque_id(run_id, "run id")
        if space not in self._allowed_space_ids:
            return "ignored_unmanaged_space"
        if blocker_code not in _BLOCKER_CODES:
            raise ValueError("Nova blocker code is not allowlisted")
        claim_digest = _claim_digest(
            {
                "kind": "blocker",
                "space_id": space,
                "run_id": run,
                "blocker_code": blocker_code,
            }
        )
        if not self._claim("blocker", claim_digest):
            return "already_claimed"
        message = _render_blocker(_space_label(space), run, blocker_code)
        return self._send_claimed(claim_digest, message)

    def send_daily_digest(
        self,
        *,
        space_id: str,
        display_name: str,
        status_counts: Mapping[str, int],
        utc_date: str,
    ) -> str:
        """Send one UTC-dated fixed status digest, at most once locally."""
        # See ``send_blocker``: only a deterministic opaque Space label is
        # permitted in the fixed external template.
        del display_name
        space = _opaque_id(space_id, "space id")
        day = _utc_date(utc_date)
        if space not in self._allowed_space_ids:
            return "ignored_unmanaged_space"
        counts = _status_counts(status_counts)
        claim_digest = _claim_digest(
            {"kind": "daily_digest", "space_id": space, "utc_date": day}
        )
        if not self._claim("daily_digest", claim_digest):
            return "already_claimed"
        message = _render_daily_digest(_space_label(space), counts)
        return self._send_claimed(claim_digest, message)

    def _claim(self, kind: str, claim_digest: str) -> bool:
        with self._supervisor._supervision_state_transaction(
            schema_objects=_NOTIFICATION_CLAIM_SCHEMA_OBJECTS,
            schema_initializer=_ensure_claim_schema,
        ) as connection:
            existing = connection.execute(
                "SELECT 1 FROM nova_notification_claims WHERE claim_digest = ?",
                (claim_digest,),
            ).fetchone()
            if existing is not None:
                return False
            now = _timestamp()
            connection.execute(
                """INSERT INTO nova_notification_claims
                   (claim_digest, kind, result_code, created_at, updated_at)
                   VALUES (?, ?, 'pending', ?, ?)""",
                (claim_digest, kind, now, now),
            )
        return True

    def _send_claimed(self, claim_digest: str, message: str) -> str:
        # Catch BaseException: an injected worker must not leave an active
        # retry path after SystemExit/KeyboardInterrupt-like sender failure.
        try:
            self._sender.send_private(self._target.chat_id, message)
        except BaseException:
            result = "failed"
        else:
            result = "sent"
        try:
            with self._supervisor._supervision_state_transaction(
                schema_objects=_NOTIFICATION_CLAIM_SCHEMA_OBJECTS,
                schema_initializer=_ensure_claim_schema,
            ) as connection:
                connection.execute(
                    """UPDATE nova_notification_claims
                       SET result_code = ?, updated_at = ?
                       WHERE claim_digest = ? AND result_code = 'pending'""",
                    (result, _timestamp(), claim_digest),
                )
        except BaseException:
            # The durable pre-send claim is already enough to prevent a retry
            # storm.  Never persist provider detail or re-raise it into an
            # autonomous worker.
            return "failed"
        return result


def _discover_managed_space_ids(supervisor: ManagedSpaceSupervisor) -> frozenset[str]:
    """Resolve only currently enrolled YOLO Space IDs without writing state."""
    try:
        from web.api.space_engine import get_all_spaces
        spaces = get_all_spaces()
    except Exception:
        return frozenset()
    ids: set[str] = set()
    for space in spaces:
        target = str(getattr(space, "slug", "") or "").strip().lower()
        if not target:
            continue
        try:
            governance = supervisor.current_governance(target)
        except Exception:
            continue
        if governance is None or governance.yolo is not True or governance.enrolled is not True:
            continue
        value = str(governance.space_id or "")
        if _OPAQUE_ID_RE.fullmatch(value):
            ids.add(value)
    return frozenset(ids)

def _ensure_claim_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS nova_notification_claims (
            claim_digest TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            result_code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )


def _opaque_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _OPAQUE_ID_RE.fullmatch(value) is None:
        raise ValueError(f"Nova notification {label} is invalid")
    return value


def _private_chat_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Telegram private chat id must be an integer")
    # Negative IDs identify groups/channels in Telegram. The host only accepts
    # a positive user chat id and never derives one from a default or fallback.
    if not 1 <= value < (1 << 63):
        raise ValueError("Telegram target must be a positive private chat id")
    return value


def _space_label(space_id: str) -> str:
    """Render an opaque deterministic Space label without exposing caller text."""
    return "Space " + sha256(space_id.encode("utf-8")).hexdigest()[:8]


def _utc_date(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Nova notification UTC date is required")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Nova notification UTC date is invalid")
    return value


def _status_counts(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("Nova daily digest requires fixed status counts")
    normalized: list[tuple[str, int]] = []
    for status, count in value.items():
        if status not in _DIGEST_STATUSES:
            raise ValueError("Nova daily digest status is not allowlisted")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 100_000:
            raise ValueError("Nova daily digest count is invalid")
        normalized.append((status, count))
    return tuple(sorted(normalized))


def _render_blocker(display_name: str, run_id: str, blocker_code: str) -> str:
    opaque_run = sha256(run_id.encode("utf-8")).hexdigest()[:8]
    return _render(
        f"Nova — {display_name}: Blocker {blocker_code.replace('_', ' ')} "
        f"(Run {opaque_run})."
    )


def _render_daily_digest(display_name: str, counts: tuple[tuple[str, int], ...]) -> str:
    summary = ", ".join(f"{status} {count}" for status, count in counts)
    return _render(f"Nova — {display_name}: Tagesstatus {summary}.")


def _render(value: str) -> str:
    # Defense in depth: templates are constructed only from allowlisted values,
    # and all text still receives forced redaction immediately before send.
    return redact_sensitive_text(value, force=True)[:_MAX_MESSAGE_LENGTH]


def _claim_digest(value: Mapping[str, str]) -> str:
    return sha256(
        json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
