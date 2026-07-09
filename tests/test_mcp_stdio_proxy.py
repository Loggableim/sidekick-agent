import sys


def test_stdio_proxy_identifies_jsonrpc_stdout_lines():
    from tools.mcp_stdio_proxy import is_jsonrpc_stdout_line

    assert is_jsonrpc_stdout_line('{"jsonrpc":"2.0","id":1,"result":{}}\n') is True
    assert is_jsonrpc_stdout_line("Successfully refreshed access token\n") is False


def test_stdio_command_is_wrapped_in_proxy():
    from tools.mcp_tool import _wrap_stdio_command

    command, args = _wrap_stdio_command("npx", ["-y", "firecrawl-mcp"])

    assert command == sys.executable
    assert args[0].endswith("mcp_stdio_proxy.py")
    assert args[1:] == ["npx", "-y", "firecrawl-mcp"]
