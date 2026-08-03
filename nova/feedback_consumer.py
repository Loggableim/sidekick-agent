"""Host-owned, injectable consumer for Nova's local feedback inbox."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Any
import math
from .feedback_adapter import LocalFeedbackLedger

@dataclass(frozen=True)
class FeedbackConsumeResult:
    correlation_id: str
    status: str

class NovaFeedbackConsumer:
    """Consume only queued feedback when an explicit responder is injected."""
    def __init__(self, ledger: LocalFeedbackLedger, responder: Callable[[str], Any] | None = None, *, max_attempts: int = 2, timeout_seconds: float = 5.0):
        if not isinstance(ledger, LocalFeedbackLedger):
            raise TypeError("feedback consumer requires a local ledger")
        if responder is not None and not callable(responder):
            raise TypeError("feedback responder is invalid")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.ledger = ledger
        self.responder = responder
        self.max_attempts = max_attempts
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(float(timeout_seconds)) or not 0 < float(timeout_seconds) <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        self.timeout_seconds = float(timeout_seconds)
    def consume(self, *, limit: int = 16) -> tuple[FeedbackConsumeResult, ...]:
        if self.responder is None:
            return ()
        results: list[FeedbackConsumeResult] = []
        for item in self.ledger.queued_items(limit):
            correlation_id = str(item.get("correlation_id") or "")
            message = str(item.get("message") or "")
            for _ in range(self.max_attempts):
                try:
                    pool = ThreadPoolExecutor(max_workers=1)
                    try:
                        response = pool.submit(self.responder, message).result(timeout=self.timeout_seconds)
                    finally:
                        pool.shutdown(wait=False, cancel_futures=True)
                    if isinstance(response, Mapping):
                        status = str(response.get("status") or "").strip().lower()
                        response_text = str(response.get("response") or response.get("detail") or "").strip()
                        if status not in {"received", "acked"}:
                            raise ValueError("response status is not accepted")
                    else:
                        response_text = str(response or "").strip()
                    if not response_text or len(response_text) > 400:
                        raise ValueError("response is empty or too long")
                    saved = self.ledger.receive(correlation_id, response_text)
                    results.append(FeedbackConsumeResult(correlation_id, str(saved and saved.get("status") or "received")))
                    break
                except Exception:
                    continue
        return tuple(results)

__all__ = ["FeedbackConsumeResult", "NovaFeedbackConsumer"]