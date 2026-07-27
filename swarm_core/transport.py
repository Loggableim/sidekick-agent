"""The only model-call protocol used by Swarm Core."""

from __future__ import annotations

from contextlib import nullcontext
import json
from typing import Any, Callable, ContextManager, Mapping, Protocol

from .models import ModelRequest, ModelResponse, OLLAMA_CLOUD_PROVIDER


_PROVIDER_ERROR_MODULE_PREFIXES = (
    "aiohttp",
    "httpx",
    "openai",
    "requests",
    "urllib3",
)
_TIMEOUT_ERROR_NAMES = frozenset(
    {
        "APITimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "Timeout",
        "TimeoutException",
        "WriteTimeout",
    }
)
_PROVIDER_ERROR_NAMES = frozenset(
    {
        "APIConnectionError",
        "APIStatusError",
        "ConnectError",
        "HTTPError",
        "HTTPStatusError",
        "NetworkError",
        "ProtocolError",
        "ProxyError",
        "RequestError",
        "RequestException",
        "TransportError",
    }
)


class RetryableModelTransportError(RuntimeError):
    """A provider-side failure for which the configured model chain may retry.

    This is deliberately narrower than ``Exception``.  Model execution must
    not hide a bug in an adapter, a policy guard, or a checkpoint callback by
    silently treating it as an Ollama Cloud failure and trying another model.
    """


class ModelProviderError(RetryableModelTransportError):
    """The selected cloud model/provider could not serve the request."""


class ModelTimeoutError(RetryableModelTransportError):
    """The selected cloud model/provider exceeded its request deadline."""


class ModelTransport(Protocol):
    # A transport may opt in only when repeating the exact same provider call
    # after a process crash is externally idempotent.  Ollama Cloud's existing
    # Sidekick call path does not provide that guarantee.
    supports_idempotent_replay: bool

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one explicit model request."""


class OllamaCloudTransport:
    """Invoke an injected Sidekick-compatible call function.

    The concrete adapter is supplied outside core. This module deliberately
    imports neither Sidekick runtime modules nor a direct Ollama client.
    """

    supports_idempotent_replay = False

    def __init__(
        self,
        call_llm: Callable[..., Any],
        *,
        call_guard: Callable[[ModelRequest], ContextManager[None]] | None = None,
    ) -> None:
        self._call_llm = call_llm
        self._call_guard = call_guard

    def complete(self, request: ModelRequest) -> ModelResponse:
        guard = (
            self._call_guard(request) if self._call_guard is not None else nullcontext()
        )
        try:
            with guard:
                raw_response = self._call_llm(
                    task="swarm",
                    provider=OLLAMA_CLOUD_PROVIDER,
                    model=request.model,
                    messages=[{"role": "user", "content": request.render_prompt()}],
                )
        except Exception as exc:
            retryable = _classify_retryable_cloud_error(exc)
            if retryable is not None:
                raise retryable from exc
            raise
        content = _response_content(raw_response)
        return ModelResponse(
            model=request.model,
            content=content,
            data=_structured_data(content),
        )


def _response_content(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, Mapping):
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            content = response.get("content")
            if content is not None:
                return str(content)
    try:
        return str(response.choices[0].message.content)
    except (AttributeError, IndexError, TypeError) as exc:
        raise TypeError("Unsupported Sidekick model response") from exc


def _structured_data(content: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _classify_retryable_cloud_error(exc: Exception) -> RetryableModelTransportError | None:
    """Map only known provider-library failures into the fallback protocol.

    The Core intentionally does not import an OpenAI/HTTP client just to
    classify failures.  The injected Sidekick call may use one of those
    libraries, so their narrowly named exception families are recognized by
    module and class name.  Everything else remains an implementation error
    and propagates to the host unchanged.
    """
    if isinstance(exc, RetryableModelTransportError):
        return exc
    if isinstance(exc, TimeoutError):
        return ModelTimeoutError("Ollama Cloud request timed out")
    if isinstance(exc, ConnectionError):
        return ModelProviderError("Ollama Cloud provider connection failed")
    error_type = type(exc)
    module = error_type.__module__
    if not module.startswith(_PROVIDER_ERROR_MODULE_PREFIXES):
        return None
    if error_type.__name__ in _TIMEOUT_ERROR_NAMES:
        return ModelTimeoutError("Ollama Cloud request timed out")
    if error_type.__name__ in _PROVIDER_ERROR_NAMES:
        return ModelProviderError("Ollama Cloud provider request failed")
    return None
