import sys
import io


def test_stdio_proxy_forwards_unicode_stdout_and_stderr_without_crashing(monkeypatch):
    from tools import mcp_stdio_proxy

    class FakeTextStream:
        def __init__(self):
            self.buffer = io.BytesIO()
            self.writes = []

        def write(self, text):
            text.encode("cp1252")
            self.writes.append(text)
            return len(text)

        def flush(self):
            pass

    stdout = FakeTextStream()
    stderr = FakeTextStream()
    monkeypatch.setattr(mcp_stdio_proxy.sys, "stdout", stdout)
    monkeypatch.setattr(mcp_stdio_proxy.sys, "stderr", stderr)

    mcp_stdio_proxy._forward_stdout(
        iter([
            '{"jsonrpc":"2.0","id":1,"result":{"text":"hello → world"}}\n',
            "banner → line\n",
        ]),
        "firecrawl",
    )

    assert stdout.buffer.getvalue() == b'{"jsonrpc":"2.0","id":1,"result":{"text":"hello \xe2\x86\x92 world"}}\n'
    assert b"banner \xe2\x86\x92 line" in stderr.buffer.getvalue()


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
