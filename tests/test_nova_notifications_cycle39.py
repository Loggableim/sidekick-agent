"""Private Telegram notification isolation and at-most-once regression."""

from pathlib import Path
from uuid import uuid4

import pytest

from nova.notifications import NovaTelegramNotifications, PrivateTelegramTarget
from nova.space_supervisor import ManagedSpaceSupervisor


class _Sender:
    def __init__(self): self.messages = []
    def send_private(self, chat_id, message): self.messages.append((chat_id, message))


def test_three_space_private_target_redaction_and_at_most_once(tmp_path: Path) -> None:
    supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / "supervisor.sqlite", governance_resolver=lambda _key: None)
    aquarium_id, finance_id, nova_id = uuid4().hex, uuid4().hex, uuid4().hex
    sender = _Sender()
    notifier = NovaTelegramNotifications(
        supervisor=supervisor,
        target=PrivateTelegramTarget(chat_id=123456),
        sender=sender,
        allowed_space_ids={aquarium_id},
    )
    assert notifier.send_blocker(space_id=finance_id, display_name="Finance", run_id=uuid4().hex, blocker_code="governance_revoked") == "ignored_unmanaged_space"
    assert notifier.send_blocker(space_id=nova_id, display_name="Nova", run_id=uuid4().hex, blocker_code="governance_revoked") == "ignored_unmanaged_space"
    run_id = uuid4().hex
    assert notifier.send_blocker(space_id=aquarium_id, display_name="secret=do-not-send", run_id=run_id, blocker_code="governance_revoked") == "sent"
    assert notifier.send_blocker(space_id=aquarium_id, display_name="again", run_id=run_id, blocker_code="governance_revoked") == "already_claimed"
    assert notifier.send_daily_digest(space_id=aquarium_id, display_name="secret", status_counts={"completed": 1}, utc_date="2026-08-03") == "sent"
    assert notifier.send_daily_digest(space_id=aquarium_id, display_name="secret", status_counts={"completed": 1}, utc_date="2026-08-03") == "already_claimed"
    assert len(sender.messages) == 2
    rendered = " ".join(message for _chat, message in sender.messages)
    assert "secret" not in rendered and "do-not-send" not in rendered
    assert all(chat_id == 123456 for chat_id, _message in sender.messages)


def test_telegram_target_must_be_one_private_chat() -> None:
    with pytest.raises(ValueError):
        PrivateTelegramTarget.from_config({"chat_id": -1, "chat_type": "group"})


