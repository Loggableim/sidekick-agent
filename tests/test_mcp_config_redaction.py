from runtime.redact import redact_sensitive_text


def test_firecrawl_key_in_mcp_url_is_redacted_for_display():
    url = "https://mcp.firecrawl.dev/fc-exampleSecretValue123456/v2/mcp"
    displayed = redact_sensitive_text(url, force=True)
    assert "exampleSecretValue" not in displayed
    assert "fc-" in displayed
    assert "..." in displayed
    assert displayed != url
