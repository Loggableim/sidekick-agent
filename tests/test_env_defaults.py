def test_managed_message_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_MANAGED", raising=False)

    from cli.config import format_managed_message

    message = format_managed_message("update Sidekick")

    assert "Cannot update Sidekick" in message


def test_runtime_provider_handles_unset_provider_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_INFERENCE_PROVIDER", raising=False)

    import cli.runtime_provider as runtime_provider

    monkeypatch.setattr(runtime_provider, "_get_model_config", lambda: {})

    assert runtime_provider.resolve_requested_provider(None) == "auto"


def test_oneshot_provider_without_model_handles_unset_model_env(monkeypatch, capsys):
    monkeypatch.delenv("SIDEKICK_INFERENCE_MODEL", raising=False)

    from cli.oneshot import run_oneshot

    assert run_oneshot("hello", provider="openrouter") == 2
    assert "--provider requires --model" in capsys.readouterr().err


def test_gateway_detached_mode_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_GATEWAY_DETACHED", raising=False)

    import cli.gateway as gateway

    monkeypatch.setattr(gateway, "is_windows", lambda: True)

    assert isinstance(gateway._windows_gateway_should_absorb_console_controls(), bool)


def test_gateway_restart_drain_timeout_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_RESTART_DRAIN_TIMEOUT", raising=False)

    import cli.gateway as gateway

    monkeypatch.setattr(gateway, "read_raw_config", lambda: {})

    assert gateway._get_restart_drain_timeout() == gateway.DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
