"""The only model-call protocol used by Swarm Core."""

from __future__ import annotations

from contextlib import nullcontext
import json
from typing import Any, Callable, ContextManager, Mapping, Protocol

from .models import ModelRequest, ModelResponse, OLLAMA_CLOUD_PROVIDER


class ModelTransport(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one explicit model request."""


class OllamaCloudTransport:
    """Invoke an injected Sidekick-compatible call function.

    The concrete adapter is supplied outside core. This module deliberately
    imports neither Sidekick runtime modules nor a direct Ollama client.
    """

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
        with guard:
            raw_response = self._call_llm(
                task="swarm",
                provider=OLLAMA_CLOUD_PROVIDER,
                model=request.model,
                messages=[{"role": "user", "content": request.render_prompt()}],
            )
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
