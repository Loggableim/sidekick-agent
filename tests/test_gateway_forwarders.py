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
