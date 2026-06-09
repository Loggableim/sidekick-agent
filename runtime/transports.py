"""Minimal compat shim for runtime.transports.

The real transport layer (agent/transports/) hasn't been ported to runtime/
yet.  This shim provides get_transport() so that run_agent.py and
auxiliary_client.py can import without crashing.  get_transport() always
returns None, signalling callers to use their legacy fallback paths.
"""

from __future__ import annotations


def get_transport(api_mode: str):
    """Return None — transport layer not yet ported to runtime/.

    The original agent/transports/ package registers transport classes
    per api_mode string (chat_completions, anthropic_messages, codex,
    bedrock).  Returning None lets every callsite fall back to its
    legacy code path transparently.
    """
    return None


__all__ = ["get_transport"]