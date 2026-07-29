from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def _supervisor(tmp_path: Path) -> ManagedSpaceSupervisor:
    governance = ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()),
        canonical_root=tmp_path / "alpha",
        root_fingerprint="",
        yolo=True,
        enrolled=True,
        revision=1,
        policy_identity="space-governance:1",
    )
    return ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda _target: governance,
    )


class _Sender:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[int, str]] = []

    def send_private(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))
        if self.fail:
            raise RuntimeError("telegram provider failed with token=super-secret")


def _notifier(tmp_path: Path, sender: _Sender):
    from nova.notifications import NovaTelegramNotifications, PrivateTelegramTarget

    target = PrivateTelegramTarget.from_config(
        {"chat_id": 123456, "chat_type": "private"}
    )
    return NovaTelegramNotifications(
        supervisor=_supervisor(tmp_path),
        target=target,
        sender=sender,
    )


@pytest.mark.parametrize(
    "config",
    (
        {},
        {"chat_id": 123456, "chat_type": "group"},
        {"chat_id": 123456, "chat_type": "private", "fallback": 9},
        {"chat_id": True, "chat_type": "private"},
        {"chat_id": -100123456, "chat_type": "private"},
    ),
)
def test_private_telegram_target_rejects_default_group_or_fallback_configuration(
    config: dict[str, object],
) -> None:
    from nova.notifications import PrivateTelegramTarget

    with pytest.raises((TypeError, ValueError)):
        PrivateTelegramTarget.from_config(config)


def test_blocker_claim_is_durable_redacted_and_at_most_once(tmp_path: Path) -> None:
    sender = _Sender()
    notifier = _notifier(tmp_path, sender)
    raw_secret = "OPENAI_API_KEY=" + ("sk" + "-" + "this-must-never-leave-the-host")

    assert (
        notifier.send_blocker(
            space_id="space-alpha",
            display_name="Alpha " + raw_secret,
            run_id="raw-run-id-not-for-telegram",
            blocker_code="dispatch_failed",
        )
        == "sent"
    )
    assert (
        notifier.send_blocker(
            space_id="space-alpha",
            display_name="Alpha " + raw_secret,
            run_id="raw-run-id-not-for-telegram",
            blocker_code="dispatch_failed",
        )
        == "already_claimed"
    )

    assert len(sender.messages) == 1
    _chat_id, text = sender.messages[0]
    assert raw_secret not in text
    assert "raw-run-id-not-for-telegram" not in text
    assert "dispatch failed" in text.lower()

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(nova_notification_claims)")
        }
        row = connection.execute(
            "SELECT kind, result_code FROM nova_notification_claims"
        ).fetchone()
    assert columns == {"claim_digest", "kind", "result_code", "created_at", "updated_at"}
    assert row == ("blocker", "sent")


def test_daily_digest_allows_only_fixed_statuses_and_claims_once_per_utc_day(
    tmp_path: Path,
) -> None:
    sender = _Sender()
    notifier = _notifier(tmp_path, sender)

    assert (
        notifier.send_daily_digest(
            space_id="space-alpha",
            display_name="Alpha",
            status_counts={"completed": 3, "blocked": 1},
            utc_date="2026-07-29",
        )
        == "sent"
    )
    assert (
        notifier.send_daily_digest(
            space_id="space-alpha",
            display_name="Alpha",
            status_counts={"completed": 3, "blocked": 1},
            utc_date="2026-07-29",
        )
        == "already_claimed"
    )
    assert len(sender.messages) == 1

    with pytest.raises(ValueError):
        notifier.send_daily_digest(
            space_id="space-alpha",
            display_name="Alpha",
            status_counts={"model said: send arbitrary content": 1},
            utc_date="2026-07-30",
        )


def test_sender_failure_is_recorded_without_error_text_or_retry_storm(
    tmp_path: Path,
) -> None:
    sender = _Sender(fail=True)
    notifier = _notifier(tmp_path, sender)

    assert (
        notifier.send_blocker(
            space_id="space-alpha",
            display_name="Alpha",
            run_id="run-1",
            blocker_code="governance_revoked",
        )
        == "failed"
    )
    assert (
        notifier.send_blocker(
            space_id="space-alpha",
            display_name="Alpha",
            run_id="run-1",
            blocker_code="governance_revoked",
        )
        == "already_claimed"
    )
    assert len(sender.messages) == 1

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        result_codes = list(
            connection.execute("SELECT result_code FROM nova_notification_claims")
        )
    assert result_codes == [("failed",)]


def test_blocker_rejects_free_form_model_or_tool_text(tmp_path: Path) -> None:
    sender = _Sender()
    notifier = _notifier(tmp_path, sender)

    with pytest.raises(ValueError):
        notifier.send_blocker(
            space_id="space-alpha",
            display_name="Alpha",
            run_id="run-1",
            blocker_code="model said token=super-secret",
        )

    assert sender.messages == []
