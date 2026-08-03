from nova.feedback_adapter import LocalFeedbackLedger
from nova.feedback_consumer import NovaFeedbackConsumer


def test_consumer_is_fail_closed_without_responder(tmp_path):
    ledger = LocalFeedbackLedger(tmp_path / "feedback.sqlite")
    ledger.queue("hello", correlation_id="c1")
    assert NovaFeedbackConsumer(ledger).consume() == ()
    assert ledger.latest_status("c1")["status"] == "queued"


def test_consumer_marks_valid_response_once_and_redacts(tmp_path):
    ledger = LocalFeedbackLedger(tmp_path / "feedback.sqlite")
    ledger.queue("hello", correlation_id="c1")
    consumer = NovaFeedbackConsumer(ledger, lambda _: {"status": "acked", "response": "secret=hidden " + "ok"})
    result = consumer.consume()
    assert result[0].status == "received"
    before = ledger.latest_status("c1")
    assert consumer.consume() == ()
    assert ledger.latest_status("c1") == before


def test_consumer_retries_bounded_and_leaves_pending_on_failure(tmp_path):
    ledger = LocalFeedbackLedger(tmp_path / "feedback.sqlite")
    ledger.queue("hello", correlation_id="c1")
    calls = []
    def fail(_):
        calls.append(1)
        raise TimeoutError("slow")
    assert NovaFeedbackConsumer(ledger, fail, max_attempts=2).consume() == ()
    assert len(calls) == 2
    assert ledger.latest_status("c1")["status"] == "queued"

def test_consumer_timeout_does_not_block_ticker_or_receive(tmp_path):
    import time
    ledger = LocalFeedbackLedger(tmp_path / "feedback.sqlite")
    ledger.queue("hello", correlation_id="c1")
    def slow(_):
        time.sleep(0.2)
        return "late"
    started = time.monotonic()
    assert NovaFeedbackConsumer(ledger, slow, max_attempts=1, timeout_seconds=0.02).consume() == ()
    assert time.monotonic() - started < 0.12
    assert ledger.latest_status("c1")["status"] == "queued"
