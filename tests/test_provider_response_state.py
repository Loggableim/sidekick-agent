from __future__ import annotations

from types import SimpleNamespace

from runtime.provider_response_state import (
    clear_provider_response_states,
    get_provider_response_state,
    record_provider_response,
)
from runtime.auxiliary_client import call_llm
from web.api.providers import get_provider_quota
from run_agent import AIAgent


def test_records_rate_limit_headers_and_usage_metadata_for_provider():
    clear_provider_response_states()

    record_provider_response(
        "custom",
        headers={
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "42",
            "x-ratelimit-reset-requests": "18",
            "x-ratelimit-limit-tokens": "200000",
            "x-ratelimit-remaining-tokens": "120000",
            "x-ratelimit-reset-tokens": "60",
        },
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25, total_tokens=125),
    )

    snapshot = get_provider_response_state("custom")

    assert snapshot is not None
    assert snapshot.provider == "custom"
    assert snapshot.rate_limit is not None
    assert snapshot.rate_limit.requests_min.limit == 60
    assert snapshot.rate_limit.requests_min.remaining == 42
    assert snapshot.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
    }


def test_provider_quota_returns_openai_compatible_rate_limit_state(monkeypatch):
    clear_provider_response_states()
    monkeypatch.setattr(
        "web.api.providers.resolve_active_provider_context",
        lambda: {"provider": "custom", "model": "gpt-test", "base_url": "https://llm.example/v1"},
    )

    record_provider_response(
        "custom",
        headers={
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "42",
            "x-ratelimit-reset-requests": "18",
            "x-ratelimit-limit-tokens": "200000",
            "x-ratelimit-remaining-tokens": "120000",
            "x-ratelimit-reset-tokens": "60",
        },
        usage={"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125},
    )

    payload = get_provider_quota()

    assert payload["ok"] is True
    assert payload["provider"] == "custom"
    assert payload["supported"] is True
    assert payload["status"] == "ok"
    assert payload["rate_limits"]["requests"]["limit"] == 60
    assert payload["rate_limits"]["requests"]["remaining"] == 42
    assert payload["rate_limits"]["tokens"]["limit"] == 200000
    assert payload["rate_limits"]["tokens"]["remaining"] == 120000
    assert payload["usage"] == {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125}


def test_call_llm_captures_raw_response_headers_and_usage(monkeypatch):
    clear_provider_response_states()

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=3, total_tokens=12),
    )
    raw_response = SimpleNamespace(
        headers={
            "x-ratelimit-limit-requests": "10",
            "x-ratelimit-remaining-requests": "7",
            "x-ratelimit-reset-requests": "30",
        },
        parse=lambda: response,
    )

    class FakeRawCompletions:
        def create(self, **kwargs):
            return raw_response

    class FakeCompletions:
        with_raw_response = FakeRawCompletions()

        def create(self, **kwargs):
            raise AssertionError("call_llm should prefer with_raw_response.create()")

    fake_client = SimpleNamespace(
        base_url="https://llm.example/v1",
        chat=SimpleNamespace(completions=FakeCompletions()),
    )

    monkeypatch.setattr(
        "runtime.auxiliary_client._resolve_task_provider_model",
        lambda *args, **kwargs: ("custom", "gpt-test", "https://llm.example/v1", "sk-test", "chat_completions"),
    )
    monkeypatch.setattr(
        "runtime.auxiliary_client._get_cached_client",
        lambda *args, **kwargs: (fake_client, "gpt-test"),
    )
    monkeypatch.setattr("runtime.auxiliary_client._get_task_extra_body", lambda task: {})
    monkeypatch.setattr("runtime.auxiliary_client._get_task_timeout", lambda task: 5.0)

    result = call_llm(
        task="title_generation",
        messages=[{"role": "user", "content": "hello"}],
    )

    state = get_provider_response_state("custom")
    assert result.choices[0].message.content == "ok"
    assert state is not None
    assert state.rate_limit is not None
    assert state.rate_limit.requests_min.limit == 10
    assert state.rate_limit.requests_min.remaining == 7
    assert state.usage == {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}


def test_agent_stream_rate_limit_capture_updates_provider_response_state():
    clear_provider_response_states()
    agent = AIAgent.__new__(AIAgent)
    agent.provider = "custom"
    agent._rate_limit_state = None

    agent._capture_rate_limits(
        SimpleNamespace(
            headers={
                "x-ratelimit-limit-requests": "20",
                "x-ratelimit-remaining-requests": "15",
                "x-ratelimit-reset-requests": "45",
            }
        )
    )

    state = get_provider_response_state("custom")
    assert agent.get_rate_limit_state() is not None
    assert state is not None
    assert state.rate_limit is not None
    assert state.rate_limit.requests_min.limit == 20
    assert state.rate_limit.requests_min.remaining == 15
