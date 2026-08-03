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


def test_env_notifier_is_disabled_without_explicit_private_target_or_token(monkeypatch) -> None:
    from nova.notifications import build_env_notifier

    monkeypatch.delenv("NOVA_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("NOVA_TELEGRAM_CHAT_TYPE", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    supervisor = object.__new__(ManagedSpaceSupervisor)
    assert build_env_notifier(supervisor) is None

    monkeypatch.setenv("NOVA_TELEGRAM_CHAT_ID", "123456")
    monkeypatch.setenv("NOVA_TELEGRAM_CHAT_TYPE", "private")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    notifier = build_env_notifier(supervisor)
    assert notifier is not None

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
        allowed_space_ids={"space-alpha"},
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


@pytest.mark.parametrize("chat_id", (True, -100123456, 0, 1 << 63))
def test_private_telegram_target_direct_constructor_enforces_private_chat_invariants(
    chat_id: object,
) -> None:
    from nova.notifications import PrivateTelegramTarget

    with pytest.raises((TypeError, ValueError)):
        PrivateTelegramTarget(chat_id=chat_id)  # type: ignore[arg-type]


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


def test_fixed_templates_ignore_arbitrary_caller_display_names(tmp_path: Path) -> None:
    """Caller/model text must not become an outbound Telegram message field."""
    sender = _Sender()
    notifier = _notifier(tmp_path, sender)
    untrusted_label = "model output: violet-harbor-493-send-this-verbatim"

    assert (
        notifier.send_blocker(
            space_id="space-alpha",
            display_name=untrusted_label,
            run_id="run-1",
            blocker_code="dispatch_failed",
        )
        == "sent"
    )
    assert (
        notifier.send_daily_digest(
            space_id="space-alpha",
            display_name=untrusted_label,
            status_counts={"completed": 1},
            utc_date="2026-07-29",
        )
        == "sent"
    )

    assert len(sender.messages) == 2
    assert all(untrusted_label not in text for _chat_id, text in sender.messages)
    assert all("Space " in text for _chat_id, text in sender.messages)


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


def test_notification_cross_space_isolation_fails_closed_before_claim_or_send(
    tmp_path: Path,
) -> None:
    """Only host-resolved YOLO Space IDs may reach the private chat."""
    from nova.notifications import NovaTelegramNotifications, PrivateTelegramTarget

    sender = _Sender()
    supervisor = _supervisor(tmp_path)
    notifier = NovaTelegramNotifications(
        supervisor=supervisor,
        target=PrivateTelegramTarget.from_config(
            {"chat_id": 123456, "chat_type": "private"}
        ),
        sender=sender,
        allowed_space_ids={"space-alpha"},
    )

    assert notifier.send_blocker(
        space_id="space-beta",
        display_name="Finanzjunkie",
        run_id="run-beta",
        blocker_code="dispatch_failed",
    ) == "ignored_unmanaged_space"
    assert notifier.send_daily_digest(
        space_id="space-beta",
        display_name="Finanzjunkie",
        status_counts={"blocked": 1},
        utc_date="2026-08-03",
    ) == "ignored_unmanaged_space"
    assert sender.messages == []
    # The rejected Space must not even create a durable notification claim.
    assert not (tmp_path / "supervisor.sqlite").exists()