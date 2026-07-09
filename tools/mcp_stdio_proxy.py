#!/usr/bin/env python3
"""Proxy a stdio MCP server while filtering stray stdout noise.

The MCP stdio transport requires JSON-RPC frames on stdout. Some third-party
servers occasionally print human-facing banners or token refresh messages to
stdout, which breaks the JSON-RPC reader in the MCP SDK. This proxy preserves
valid JSON-RPC stdout traffic and diverts any non-JSON stdout lines to stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Iterable


def is_jsonrpc_stdout_line(line: str) -> bool:
    """Return True when *line* is a JSON-RPC message line."""
    try:
        payload = json.loads(line)
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"


def _forward_stdin(child_stdin) -> None:
    try:
        for line in sys.stdin:
            if child_stdin is None:
                break
            child_stdin.write(line)
            child_stdin.flush()
    except Exception:
        pass
    finally:
        try:
            if child_stdin is not None:
                child_stdin.close()
        except Exception:
            pass


def _forward_stdout(child_stdout, command_name: str) -> None:
    try:
        for line in child_stdout:
            if is_jsonrpc_stdout_line(line):
                sys.stdout.write(line)
                sys.stdout.flush()
                continue
            stripped = line.rstrip("\n")
            if stripped:
                sys.stderr.write(
                    f"[mcp-stdio-proxy] dropped non-JSON stdout from {command_name}: {stripped}\n"
                )
                sys.stderr.flush()
    except Exception as exc:
        sys.stderr.write(f"[mcp-stdio-proxy] stdout forwarder stopped: {exc}\n")
        sys.stderr.flush()


def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write("usage: mcp_stdio_proxy.py <command> [args...]\n")
        sys.stderr.flush()
        return 2

    command, child_args = args[0], args[1:]
    try:
        proc = subprocess.Popen(
            [command, *child_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"[mcp-stdio-proxy] failed to start {command}: {exc}\n")
        sys.stderr.flush()
        return 127

    stdin_thread = threading.Thread(
        target=_forward_stdin,
        args=(proc.stdin,),
        daemon=True,
    )
    stdin_thread.start()

    if proc.stdout is not None:
        _forward_stdout(proc.stdout, command)

    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:
        pass

    try:
        return int(proc.wait(timeout=10))
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            return int(proc.wait(timeout=10))
        except subprocess.TimeoutExpired:
            proc.kill()
            return int(proc.wait())


if __name__ == "__main__":
    raise SystemExit(main())
