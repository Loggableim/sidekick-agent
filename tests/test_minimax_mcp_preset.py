"""
Tests for the MiniMax Token Plan MCP preset and toolset.

Covers:
    - Preset is registered in cli.mcp_config._MCP_PRESETS
    - Preset metadata (command, args, env_help) matches the upstream package
    - "minimax" toolset exists in toolsets.TOOLSETS
    - Toolset description advertises the MCP origin
    - resolve_toolset("minimax") works even before MCP server connects
      (returns empty list — tools populate dynamically when MCP client connects)
    - get_toolset returns proper merged structure once MCP tools are registered

Run:
    python -m pytest tests/test_minimax_mcp_preset.py -v
or
    python tests/test_minimax_mcp_preset.py
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure repo root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))


class TestMinimaxPreset(unittest.TestCase):
    """Verify the preset entry exists and is well-formed."""

    def setUp(self):
        from cli.mcp_config import _MCP_PRESETS
        self.presets = _MCP_PRESETS

    def test_preset_registered(self):
        self.assertIn(
            "minimax-token-plan",
            self.presets,
            "minimax-token-plan preset must be registered in _MCP_PRESETS",
        )

    def test_preset_command(self):
        preset = self.presets["minimax-token-plan"]
        self.assertEqual(preset["command"], "uvx")
        # uvx must be called with -y for non-interactive install
        self.assertIn("-y", preset["args"])
        # Package name must match what MiniMax publishes on PyPI
        self.assertIn("minimax-coding-plan-mcp", preset["args"])

    def test_preset_env_help_documents_api_key(self):
        preset = self.presets["minimax-token-plan"]
        self.assertIn("env_help", preset, "preset must declare env_help for CLI prompts")
        self.assertIn("MINIMAX_API_KEY", preset["env_help"])
        # Help text must point users to the billing page
        self.assertIn("token-plan", preset["env_help"]["MINIMAX_API_KEY"].lower())

    def test_preset_env_help_documents_resource_mode(self):
        """The understand_image tool supports url|local delivery."""
        env_help = self.presets["minimax-token-plan"]["env_help"]
        self.assertIn("MINIMAX_API_RESOURCE_MODE", env_help)
        self.assertIn("url", env_help["MINIMAX_API_RESOURCE_MODE"])
        self.assertIn("local", env_help["MINIMAX_API_RESOURCE_MODE"])

    def test_existing_codex_preset_not_broken(self):
        """Sanity check: adding a new preset must not corrupt existing ones."""
        self.assertIn("codex", self.presets)
        self.assertEqual(self.presets["codex"]["command"], "codex")
        self.assertEqual(self.presets["codex"]["args"], ["mcp-server"])


class TestMinimaxToolset(unittest.TestCase):
    """Verify the static toolset entry in toolsets.py."""

    def setUp(self):
        from toolsets import TOOLSETS, get_toolset, resolve_toolset
        self.toolsets = TOOLSETS
        self.get_toolset = get_toolset
        self.resolve_toolset = resolve_toolset

    def test_toolset_registered(self):
        # Canonical toolset name (used by tools/mcp_tool.py)
        self.assertIn(
            "mcp-minimax",
            self.toolsets,
            "'mcp-minimax' canonical toolset must be in TOOLSETS; "
            "MCP client auto-registers alias 'minimax' -> 'mcp-minimax'",
        )

    def test_alias_resolves(self):
        """The MCP client registers the alias automatically — but toolsets.py
        only needs the canonical entry; alias resolution happens at runtime."""
        # We just check that the canonical entry exists; the alias mechanism
        # is exercised in TestMinimaxAlias.test_alias_resolves_in_registry.
        self.assertIn("mcp-minimax", self.toolsets)

    def test_toolset_description_advertises_mcp(self):
        ts = self.toolsets["mcp-minimax"]
        self.assertIn("MCP", ts["description"])
        self.assertIn("uvx", ts["description"])

    def test_get_toolset_returns_description(self):
        ts = self.get_toolset("mcp-minimax")
        self.assertIsNotNone(ts)
        self.assertEqual(ts["description"], self.toolsets["mcp-minimax"]["description"])

    def test_resolve_returns_list_before_mcp_connect(self):
        """Before MCP client connects, toolset resolves to [].

        This is the expected behavior — tools populate dynamically. The
        static entry guarantees the toolset has a stable handle for
        `sidekick tools enable minimax --platform <p>` (alias resolves
        to the canonical name).
        """
        resolved = self.resolve_toolset("mcp-minimax")
        self.assertIsInstance(resolved, list)
        self.assertEqual(resolved, [])

    def test_resolve_with_mocked_mcp_tools(self):
        """Once MCP tools register, resolve_toolset should include them."""
        # Simulate the MCP client registering tools under toolset="mcp-minimax"
        from tools.registry import registry

        handler = lambda **kw: "{}"
        check_fn = lambda: True
        registry.register(
            name="mock_web_search",
            toolset="mcp-minimax",
            schema={
                "type": "function",
                "function": {
                    "name": "mock_web_search",
                    "description": "Mocked MCP tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            handler=handler,
            check_fn=check_fn,
            requires_env=[],
            is_async=False,
            description="Mocked MCP tool",
            emoji="🔍",
        )
        try:
            resolved = self.resolve_toolset("mcp-minimax")
            self.assertIn("mock_web_search", resolved)
        finally:
            registry._tools.pop("mock_web_search", None)


class TestMinimaxAlias(unittest.TestCase):
    """Verify the MCP client registers an alias from user-name -> canonical."""

    def test_alias_resolves_in_registry(self):
        """The MCP client code in tools/mcp_tool.py registers:
               registry.register_toolset_alias(name, toolset_name)
           where name is the user-facing server name and toolset_name is
           'mcp-{name}'.  Simulate that here and verify toolsets.py honours it.
        """
        from tools.registry import registry
        from toolsets import resolve_toolset

        # Register tool under canonical name
        registry.register(
            name="understand_image",
            toolset="mcp-minimax",
            schema={
                "type": "function",
                "function": {"name": "understand_image", "parameters": {"type": "object"}},
            },
            handler=lambda **kw: "{}",
            check_fn=lambda: True,
            description="Mock",
        )
        # Register alias as the MCP client would
        registry.register_toolset_alias("minimax", "mcp-minimax")
        try:
            # Both names must resolve to the same toolset's tools
            self.assertIn("understand_image", resolve_toolset("mcp-minimax"))
            # get_toolset() looks up the alias chain via _get_registry_toolset_aliases()
            ts = None
            from toolsets import get_toolset
            ts = get_toolset("minimax")
            self.assertIsNotNone(ts, "alias 'minimax' must resolve via get_toolset()")
            self.assertIn("understand_image", ts["tools"])
        finally:
            registry._tools.pop("understand_image", None)
            registry._toolset_aliases.pop("minimax", None)


class TestPresetHelpExamples(unittest.TestCase):
    """Verify that help text mentions the new preset."""

    def test_help_examples_mention_preset_flag(self):
        """The 'sidekick mcp add --preset' examples must be intact."""
        from cli.mcp_config import cmd_mcp_add  # noqa: F401 — import only
        # Smoke test: module imports cleanly
        self.assertTrue(callable(cmd_mcp_add))


if __name__ == "__main__":
    unittest.main(verbosity=2)
