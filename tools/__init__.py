"""Minimal tools stub package for the monorepo migration.

The real tools/ package (60+ modules, ~80K LOC) lives in
cids-hermes-agent/tools/ and will be ported in a follow-up phase.
This stub prevents ImportError crashes from lazy imports in CLI/WebUI.
"""
from __future__ import annotations

# Tools registry stub
class _RegistryStub:
    tools: dict = {}
    tool_handlers: dict = {}
    def register(self, name, schema, handler, **kw):
        self.tools[name] = schema
        self.tool_handlers[name] = handler
    def dispatch(self, name, args, **kw):
        return f"[stub] tool {name} not available in migration"
    def has_tool(self, name):
        return False

registry = _RegistryStub()

def discover_builtin_tools(*args, **kwargs):
    """Stub — tools not ported yet."""
    pass

__all__ = ["registry", "discover_builtin_tools"]
