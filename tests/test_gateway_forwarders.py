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
