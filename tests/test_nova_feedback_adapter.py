import time
from nova.feedback_adapter import LocalNovaFeedbackAdapter


def test_feedback_send_ack_and_timeout_offline():
    received = []
    adapter = LocalNovaFeedbackAdapter(lambda msg: received.append(msg) or "ack")
    assert adapter.send("hello", timeout=0.1).status == "acked"
    assert received == ["hello"]
    offline = LocalNovaFeedbackAdapter(None)
    assert offline.send("hello", timeout=0.01).status == "offline"


def test_feedback_timeout_is_bounded():
    def slow(_):
        time.sleep(0.2)
        return "ack"

    started = time.monotonic()
    result = LocalNovaFeedbackAdapter(slow).send("x", timeout=0.01)
    assert result.status == "timeout"
    assert time.monotonic() - started < 0.1


def test_feedback_rejects_invalid_timeout():
    adapter = LocalNovaFeedbackAdapter(lambda _: "ack")
    assert adapter.send("x", timeout=0).status == "rejected"
    assert adapter.send("x", timeout=float("nan")).status == "rejected"


def test_local_feedback_ledger_exactly_once_and_bounded(tmp_path):
    from nova.feedback_adapter import LocalFeedbackLedger
    ledger = LocalFeedbackLedger(tmp_path / "inbox.sqlite")
    first = ledger.queue(("secret=topsecret " * 500), correlation_id="corr-1")
    second = ledger.queue("different", correlation_id="corr-1")
    assert first == second
    assert ledger.latest_status("corr-1")["status"] == "queued"
    assert len(ledger.latest_status("corr-1")["message"]) <= 4000
    assert "secret" not in ledger.latest_status("corr-1")["message"][-10:]
    assert ledger.mark_received("corr-1") is True
    assert ledger.latest_status("corr-1")["status"] == "received"


def test_adapter_persists_offline_and_failed_states(tmp_path):
    from nova.feedback_adapter import LocalFeedbackLedger
    ledger = LocalFeedbackLedger(tmp_path / "inbox.sqlite")
    assert LocalNovaFeedbackAdapter(None, ledger=ledger).send("hello", correlation_id="off").status == "offline"
    assert ledger.latest_status("off")["status"] == "offline"
    def fail(_): raise RuntimeError("provider")
    assert LocalNovaFeedbackAdapter(fail, ledger=ledger).send("hello", correlation_id="bad").status == "offline"
    assert ledger.latest_status("bad")["status"] == "failed"



def test_ledger_receive_is_idempotent_and_only_queued(tmp_path):
    from nova.feedback_adapter import LocalFeedbackLedger
    ledger = LocalFeedbackLedger(tmp_path / "inbox.sqlite")
    ledger.queue("hello", correlation_id="corr")
    assert ledger.receive("corr", "token=secret " + "x" * 500)["status"] == "received"
    first = ledger.latest_status("corr")
    second = ledger.receive("corr", "changed")
    assert second == first
    ledger.queue("other", correlation_id="done")
    ledger.mark_received("done")
    assert ledger.receive("done", "late") == ledger.latest_status("done")


def test_ledger_receive_rejects_wrong_space_or_run(tmp_path):
    from nova.feedback_adapter import LocalFeedbackLedger
    ledger = LocalFeedbackLedger(tmp_path / "inbox.sqlite")
    ledger.queue("hello", correlation_id="run-1")
    assert ledger.receive("run-1", "reply", target_space_id="other-space")["status"] == "queued"
    assert ledger.receive("run-1", "reply", run_id="other-run")["status"] == "queued"
    assert ledger.receive("run-1", "reply", run_id="run-1")["status"] == "received"


def test_three_space_responder_isolation_and_revocation(tmp_path):
    from nova.feedback_adapter import LocalFeedbackLedger
    ledger = LocalFeedbackLedger(tmp_path / "inbox.sqlite")
    for space in ("alpha", "beta", "gamma"):
        ledger.queue("feedback", correlation_id=f"run-{space}")
    assert ledger.receive("run-alpha", "reply", run_id="run-beta")["status"] == "queued"
    assert ledger.receive("run-beta", "reply", target_space_id="alpha")["status"] == "queued"
    assert ledger.receive("run-gamma", "revoked", run_id="run-gamma")["status"] == "received"
    assert ledger.receive("run-gamma", "late", run_id="run-gamma")["detail"] == "revoked"
    assert [ledger.latest_status(f"run-{s}")["status"] for s in ("alpha", "beta")] == ["queued", "queued"]


def test_cloud_responder_gate_is_nova_space_and_run_bound():
    from nova.feedback_adapter import responder_allowed
    spaces = {"nova": True, "alpha": False, "beta": True}
    assert responder_allowed(target_space_id="nova", space_id="nova", yolo=True, enrolled=True, run_id="r1", correlation_id="r1")
    assert not responder_allowed(target_space_id="alpha", space_id="nova", yolo=True, enrolled=True, run_id="r1", correlation_id="r1")
    assert not responder_allowed(target_space_id="beta", space_id="beta", yolo=False, enrolled=True, run_id="r2", correlation_id="r2")
    assert not responder_allowed(target_space_id="beta", space_id="beta", yolo=True, enrolled=False, run_id="r2", correlation_id="r2")
    assert not responder_allowed(target_space_id="beta", space_id="beta", yolo=True, enrolled=True, run_id="r2", correlation_id="r1")
