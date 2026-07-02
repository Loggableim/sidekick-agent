def test_legacy_gateway_forwarders_import():
    import gateway.display_config  # noqa: F401
    import gateway.mirror  # noqa: F401
    import gateway.runtime_footer  # noqa: F401
    import gateway.session_context  # noqa: F401


def test_gateway_session_context_exports_helpers():
    from gateway.session_context import clear_session_vars, get_session_env, set_session_vars

    tokens = set_session_vars(platform="webui-smoke", session_key="test-session")
    try:
        assert get_session_env("HERMES_SESSION_PLATFORM") == "webui-smoke"
        assert get_session_env("HERMES_SESSION_KEY") == "test-session"
    finally:
        clear_session_vars(tokens)


def test_gateway_game_mode_block_result_is_non_llm_response():
    from runtime.gateway.run import _game_mode_gateway_block_result

    payload = _game_mode_gateway_block_result()

    assert payload["error_type"] == "game_mode_enabled"
    assert payload["game_mode_enabled"] is True
    assert payload["api_calls"] == 0
    assert payload["messages"] == []
    assert "Local model requests are blocked" in payload["final_response"]


def test_gateway_checks_game_mode_before_agent_construction():
    from pathlib import Path

    source = Path("runtime/gateway/run.py").read_text(encoding="utf-8")
    resolved = source.index("run_agent resolved: model=%s provider=%s session=%s")
    guard = source.index("if _game_mode_blocks_gateway_runtime(", resolved)
    agent_ctor = source.index("agent = AIAgent(", guard)

    assert resolved < guard < agent_ctor


def test_discord_adapter_rejects_send_without_token(monkeypatch):
    import asyncio

    from gateway.platforms.discord import DiscordAdapter
    from runtime.gateway.config import PlatformConfig

    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    adapter = DiscordAdapter(PlatformConfig(enabled=True))

    result = asyncio.run(adapter.send("123", "hello"))

    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_discord_adapter_delegates_send_to_rest_helper(monkeypatch):
    import asyncio

    from gateway.platforms.discord import DiscordAdapter
    from runtime.gateway.config import PlatformConfig
    from tools import send_message_tool

    calls = []

    async def fake_send_discord(token, chat_id, message, thread_id=None, media_files=None):
        calls.append((token, chat_id, message, thread_id, media_files))
        return {
            "success": True,
            "platform": "discord",
            "chat_id": chat_id,
            "message_id": "msg-1",
        }

    monkeypatch.setattr(send_message_tool, "_send_discord", fake_send_discord)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="bot-token"))

    result = asyncio.run(
        adapter.send("channel-1", content="hello", metadata={"thread_id": "thread-1"})
    )

    assert result == {"success": True, "message_id": "msg-1"}
    assert calls == [("bot-token", "channel-1", "hello", "thread-1", None)]


def test_discord_adapter_exposes_runner_lifecycle_aliases(monkeypatch):
    import asyncio

    from gateway.platforms.discord import DiscordAdapter
    from runtime.gateway.config import PlatformConfig

    calls = []
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="bot-token"))

    async def fake_start():
        calls.append("start")
        return True

    async def fake_stop():
        calls.append("stop")

    monkeypatch.setattr(adapter, "start", fake_start)
    monkeypatch.setattr(adapter, "stop", fake_stop)

    assert asyncio.run(adapter.connect()) is True
    asyncio.run(adapter.disconnect())
    assert calls == ["start", "stop"]


def test_platform_base_exports_proxy_helpers(monkeypatch):
    from gateway.platforms.base import (
        BasePlatformAdapter,
        proxy_kwargs_for_aiohttp,
        resolve_proxy_url,
        utf16_len,
    )

    monkeypatch.delenv("DISCORD_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    assert resolve_proxy_url(platform_env_var="DISCORD_PROXY") is None
    assert proxy_kwargs_for_aiohttp(None) == ({}, {})

    monkeypatch.setenv("DISCORD_PROXY", "socks5://127.0.0.1:9050")

    assert resolve_proxy_url(platform_env_var="DISCORD_PROXY") == "socks5://127.0.0.1:9050"
    assert proxy_kwargs_for_aiohttp("http://127.0.0.1:8080") == (
        {},
        {"proxy": "http://127.0.0.1:8080"},
    )
    assert utf16_len("a😀") == 3
    assert BasePlatformAdapter.truncate_message("a😀b", 3, len_fn=utf16_len) == [
        "a😀",
        "b",
    ]


def test_base_platform_adapter_exposes_runner_lifecycle_contract():
    import asyncio

    from gateway.platforms.base import BasePlatformAdapter

    class Adapter(BasePlatformAdapter):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def start(self):
            self.calls.append("start")
            return True

        async def stop(self):
            self.calls.append("stop")

    adapter = Adapter()

    assert asyncio.run(adapter.connect()) is True
    asyncio.run(adapter.cancel_background_tasks())
    asyncio.run(adapter.disconnect())
    assert adapter.calls == ["start", "stop"]


def test_base_platform_adapter_send_fails_when_not_implemented():
    import asyncio

    from gateway.platforms.base import BasePlatformAdapter

    result = asyncio.run(BasePlatformAdapter().send("chat", "hello"))

    assert result["success"] is False
    assert "does not implement send" in result["error"]


def test_api_server_adapter_is_inbound_only_and_has_runner_cleanup():
    import asyncio

    from gateway.platforms.api_server import APIServerAdapter

    adapter = APIServerAdapter()

    assert asyncio.run(adapter.connect()) is True
    asyncio.run(adapter.cancel_background_tasks())
    asyncio.run(adapter.disconnect())

    result = asyncio.run(adapter.send("client", "hello"))

    assert result["success"] is False
    assert "inbound" in result["error"].lower()


def test_delivery_router_preserves_adapter_send_failure():
    import asyncio

    from runtime.gateway.config import GatewayConfig, Platform
    from runtime.gateway.delivery import DeliveryRouter, DeliveryTarget

    class FailingAdapter:
        async def send(self, chat_id, message, metadata=None):
            return {"success": False, "error": "provider rejected message"}

    router = DeliveryRouter(
        GatewayConfig(),
        adapters={Platform.SLACK: FailingAdapter()},
    )

    result = asyncio.run(
        router.deliver(
            "hello",
            [DeliveryTarget(platform=Platform.SLACK, chat_id="C12345678")],
        )
    )

    assert result["slack:C12345678"]["success"] is False
    assert "provider rejected message" in result["slack:C12345678"]["error"]


def test_delivery_router_preserves_object_style_send_failure():
    import asyncio
    from types import SimpleNamespace

    from runtime.gateway.config import GatewayConfig, Platform
    from runtime.gateway.delivery import DeliveryRouter, DeliveryTarget

    class FailingAdapter:
        async def send(self, chat_id, message, metadata=None):
            return SimpleNamespace(success=False, error="object style failure")

    router = DeliveryRouter(
        GatewayConfig(),
        adapters={Platform.DISCORD: FailingAdapter()},
    )

    result = asyncio.run(
        router.deliver(
            "hello",
            [DeliveryTarget(platform=Platform.DISCORD, chat_id="123")],
        )
    )

    assert result["discord:123"]["success"] is False
    assert result["discord:123"]["error"] == "object style failure"


def test_delivery_router_resolves_platform_home_channel():
    import asyncio

    from runtime.gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
    from runtime.gateway.delivery import DeliveryRouter, DeliveryTarget

    class RecordingAdapter:
        def __init__(self):
            self.calls = []

        async def send(self, chat_id, message, metadata=None):
            self.calls.append((chat_id, message, metadata))
            return {"success": True, "message_id": "home-msg"}

    adapter = RecordingAdapter()
    config = GatewayConfig(
        platforms={
            Platform.SLACK: PlatformConfig(
                enabled=True,
                token="xoxb-token",
                home_channel=HomeChannel(
                    platform=Platform.SLACK,
                    chat_id="C-HOME",
                    name="Ops",
                    thread_id="1710000000.000100",
                ),
            )
        }
    )
    router = DeliveryRouter(config, adapters={Platform.SLACK: adapter})

    result = asyncio.run(
        router.deliver("deploy finished", [DeliveryTarget.parse("slack")])
    )

    assert result["slack"]["success"] is True
    assert adapter.calls == [
        (
            "C-HOME",
            "deploy finished",
            {"thread_id": "1710000000.000100"},
        )
    ]


def test_delivery_router_does_not_resolve_explicit_empty_chat_to_home():
    import asyncio

    from runtime.gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
    from runtime.gateway.delivery import DeliveryRouter, DeliveryTarget

    class RecordingAdapter:
        async def send(self, chat_id, message, metadata=None):
            raise AssertionError("explicit empty chat ID must not be sent")

    config = GatewayConfig(
        platforms={
            Platform.SLACK: PlatformConfig(
                enabled=True,
                token="xoxb-token",
                home_channel=HomeChannel(
                    platform=Platform.SLACK,
                    chat_id="C-HOME",
                    name="Ops",
                ),
            )
        }
    )
    router = DeliveryRouter(config, adapters={Platform.SLACK: RecordingAdapter()})

    result = asyncio.run(
        router.deliver("deploy finished", [DeliveryTarget.parse("slack:")])
    )

    assert result["slack"]["success"] is False
    assert "No chat ID" in result["slack"]["error"]


def test_delivery_router_local_delivery_uses_unique_paths(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime as real_datetime
    from pathlib import Path

    import runtime.gateway.delivery as delivery
    from runtime.gateway.config import GatewayConfig, Platform
    from runtime.gateway.delivery import DeliveryRouter, DeliveryTarget

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls):
            return cls(2026, 6, 22, 12, 34, 56)

    monkeypatch.setattr(delivery, "datetime", FixedDatetime)
    router = DeliveryRouter(GatewayConfig())
    router.output_dir = tmp_path

    first = asyncio.run(
        router.deliver(
            "first run",
            [DeliveryTarget(platform=Platform.LOCAL)],
            job_id="job-1",
        )
    )
    second = asyncio.run(
        router.deliver(
            "second run",
            [DeliveryTarget(platform=Platform.LOCAL)],
            job_id="job-1",
        )
    )

    first_path = first["local"]["result"]["path"]
    second_path = second["local"]["result"]["path"]

    assert first_path != second_path
    assert "first run" in Path(first_path).read_text(encoding="utf-8")
    assert "second run" in Path(second_path).read_text(encoding="utf-8")


def test_delivery_router_truncated_platform_output_uses_router_output_dir(tmp_path):
    import asyncio

    from runtime.gateway.config import GatewayConfig, Platform
    from runtime.gateway.delivery import DeliveryRouter, DeliveryTarget

    class RecordingAdapter:
        def __init__(self):
            self.messages = []

        async def send(self, chat_id, message, metadata=None):
            self.messages.append(message)
            return {"success": True, "message_id": "msg-1"}

    adapter = RecordingAdapter()
    router = DeliveryRouter(GatewayConfig(), adapters={Platform.SLACK: adapter})
    router.output_dir = tmp_path

    result = asyncio.run(
        router.deliver(
            "x" * 4100,
            [DeliveryTarget(platform=Platform.SLACK, chat_id="C123")],
            metadata={"job_id": "oversized-job"},
        )
    )

    assert result["slack:C123"]["success"] is True
    saved_path = next(tmp_path.glob("oversized-job_*.txt"))
    assert saved_path.read_text(encoding="utf-8") == "x" * 4100
    assert str(saved_path) in adapter.messages[0]


def test_slack_adapter_exposes_message_limit_and_rejects_missing_token(monkeypatch):
    import asyncio

    from gateway.platforms.slack import SlackAdapter
    from runtime.gateway.config import PlatformConfig

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    adapter = SlackAdapter(PlatformConfig(enabled=True))

    assert SlackAdapter.MAX_MESSAGE_LENGTH == 40000
    result = asyncio.run(adapter.send("C12345678", "hello"))

    assert result["success"] is False
    assert "token" in result["error"].lower()


def test_slack_adapter_delegates_send_to_rest_helper(monkeypatch):
    import asyncio

    from gateway.platforms.slack import SlackAdapter
    from runtime.gateway.config import PlatformConfig
    from tools import send_message_tool

    calls = []

    async def fake_send_slack(token, chat_id, message):
        calls.append((token, chat_id, message))
        return {
            "success": True,
            "platform": "slack",
            "chat_id": chat_id,
            "message_id": "1700000000.000100",
        }

    monkeypatch.setattr(send_message_tool, "_send_slack", fake_send_slack)
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-token"))

    result = asyncio.run(adapter.send("C12345678", content="hello"))

    assert result == {"success": True, "message_id": "1700000000.000100"}
    assert result.success is True
    assert calls == [("xoxb-token", "C12345678", "hello")]


def test_slack_adapter_exposes_runner_lifecycle_aliases(monkeypatch):
    import asyncio

    from gateway.platforms.slack import SlackAdapter
    from runtime.gateway.config import PlatformConfig

    calls = []
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-token"))

    async def fake_start():
        calls.append("start")
        return True

    async def fake_stop():
        calls.append("stop")

    monkeypatch.setattr(adapter, "start", fake_start)
    monkeypatch.setattr(adapter, "stop", fake_stop)

    assert asyncio.run(adapter.connect()) is True
    asyncio.run(adapter.disconnect())
    assert calls == ["start", "stop"]


def test_send_to_slack_does_not_crash_when_feishu_adapter_imports(monkeypatch):
    import asyncio

    from runtime.gateway.config import Platform, PlatformConfig
    from tools import send_message_tool

    calls = []

    async def fake_send_slack(token, chat_id, message):
        calls.append((token, chat_id, message))
        return {
            "success": True,
            "platform": "slack",
            "chat_id": chat_id,
            "message_id": "1700000000.000200",
        }

    monkeypatch.setattr(send_message_tool, "_send_slack", fake_send_slack)

    result = asyncio.run(
        send_message_tool._send_to_platform(
            Platform.SLACK,
            PlatformConfig(enabled=True, token="xoxb-token"),
            "C12345678",
            "hello",
        )
    )

    assert result["success"] is True
    assert calls == [("xoxb-token", "C12345678", "hello")]


def test_webhook_adapter_validates_signature_and_dispatches_event():
    import asyncio
    import hashlib
    import hmac
    import json

    from gateway.platforms.webhook import WebhookAdapter
    from runtime.gateway.config import PlatformConfig

    body = json.dumps({"event_type": "push", "message": "build finished"}).encode()
    signature = "sha256=" + hmac.new(b"route-secret", body, hashlib.sha256).hexdigest()
    events = []

    class Request:
        match_info = {"name": "build"}
        headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
        }
        remote = "127.0.0.1"

        async def read(self):
            return body

    async def handle(event):
        events.append(event)

    adapter = WebhookAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "routes": {
                    "build": {
                        "secret": "route-secret",
                        "events": ["push"],
                        "prompt": "Summarize this webhook:",
                    }
                }
            },
        )
    )
    adapter.set_message_handler(handle)

    response = asyncio.run(adapter._handle_webhook(Request()))

    assert response.status == 200
    assert len(events) == 1
    assert events[0].platform == "webhook"
    assert events[0].chat_id == "build"
    assert events[0].user_id == "127.0.0.1"
    assert "Summarize this webhook:" in events[0].text
    assert "build finished" in events[0].text


def test_webhook_adapter_rejects_bad_signature():
    import asyncio
    import json

    from gateway.platforms.webhook import WebhookAdapter
    from runtime.gateway.config import PlatformConfig

    body = json.dumps({"event_type": "push"}).encode()
    events = []

    class Request:
        match_info = {"name": "build"}
        headers = {"X-Hub-Signature-256": "sha256=bad"}
        remote = "127.0.0.1"

        async def read(self):
            return body

    async def handle(event):
        events.append(event)

    adapter = WebhookAdapter(
        PlatformConfig(
            enabled=True,
            extra={"routes": {"build": {"secret": "route-secret"}}},
        )
    )
    adapter.set_message_handler(handle)

    response = asyncio.run(adapter._handle_webhook(Request()))

    assert response.status == 401
    assert events == []
