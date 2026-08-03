"""Explicitly enabled Nova feedback responder over the Ollama Cloud transport."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import hashlib
import math
import re
from typing import Any, Mapping

from swarm_core.models import ModelRequest
from swarm_core.transport import ModelTransport, OllamaCloudTransport

_REDACT = re.compile(r"(?i)(secret|token|password|api[_-]?key)\s*[:=]\s*[^\s]+")

class NovaCloudResponder:
    """Host-injected, bounded responder; disabled and credential-free by default."""
    def __init__(self, transport: ModelTransport, *, model: str = "deepseek-v4-flash", timeout_seconds: float = 5.0, enabled: bool = False):
        if not hasattr(transport, "complete") or not callable(transport.complete):
            raise TypeError("Nova Cloud responder requires a model transport")
        if not isinstance(model, str) or not model.strip() or "gpt-oss" in model.casefold():
            raise ValueError("invalid Nova feedback model")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(float(timeout_seconds)) or not 0 < float(timeout_seconds) <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        self.transport = transport
        self.model = model.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.enabled = enabled

    def __call__(self, message: str) -> Mapping[str, str]:
        if not self.enabled:
            raise RuntimeError("Nova Cloud responder is disabled")
        prompt = _REDACT.sub(r"\1=[redacted]", str(message)[:4000])
        request = ModelRequest(
            run_id="feedback:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24],
            role="feedback",
            model=self.model,
            prompt=("Respond as Nova, concise and evidence-based. "
                    "Do not reveal secrets or paths.\n\n" + prompt),
            context={},
            required_fields=(),
        )
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            response = pool.submit(self.transport.complete, request).result(timeout=self.timeout_seconds)
            content = getattr(response, "content", None)
            if not isinstance(content, str) and isinstance(response, Mapping):
                content = response.get("content")
            bounded = _REDACT.sub(r"\1=[redacted]", str(content or "")).strip()[:400]
            if not bounded:
                raise ValueError("empty Nova feedback response")
            return {"status": "received", "response": bounded}
        except TimeoutError as exc:
            raise TimeoutError("Nova Cloud responder timed out") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

__all__ = ["NovaCloudResponder"]
