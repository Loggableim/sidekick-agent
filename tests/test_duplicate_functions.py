"""Tests for the duplicate-function cleanup (backlog item 40).

Four files carried duplicate top-level function definitions. Only the LAST
definition runs, so the earlier copies were dead code — except where the later
copy was the weaker one, which silently removed features.

Removed:
  commands.js  - 36 identical pairs plus the weaker cmdMcp/cmdSubagents copies
  spaces.js    - the legacy renderSpacesPanel (the richer one wins)
  panels.js    - the older closeKanbanTaskDetail
  terminal.js  - 3 identical pairs
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
FILES = ("commands.js", "spaces.js", "panels.js", "terminal.js")

_PATTERN = re.compile(r"^(?:async )?function ([a-zA-Z_$][a-zA-Z0-9_$]*)\(", re.M)


def _definitions(name: str) -> dict[str, list[int]]:
    source = (STATIC / name).read_text(encoding="utf-8")
    found: dict[str, list[int]] = {}
    for match in _PATTERN.finditer(source):
        line = source[: match.start()].count("\n") + 1
        found.setdefault(match.group(1), []).append(line)
    return found


def test_no_duplicate_top_level_functions() -> None:
    for name in FILES:
        duplicates = {k: v for k, v in _definitions(name).items() if len(v) > 1}
        assert not duplicates, f"{name} still has duplicates: {duplicates}"


def test_the_richer_cmdMcp_copy_survived() -> None:
    """The later copy lacked toggle/delete, so /mcp toggle was broken."""
    source = (STATIC / "commands.js").read_text(encoding="utf-8")
    start = source.index("async function cmdMcp(args)")
    end = source.find("\nasync function ", start + 1)
    body = source[start : end if end != -1 else None]

    assert "raw.startsWith('toggle ')" in body, "the toggle handler is missing"
    assert "raw.startsWith('delete ')" in body, "the delete handler is missing"


def test_the_richer_cmdSubagents_copy_survived() -> None:
    source = (STATIC / "commands.js").read_text(encoding="utf-8")
    start = source.index("async function cmdSubagents(args)")
    end = source.find("\nasync function ", start + 1)
    body = source[start : end if end != -1 else None]

    assert "openSubagentsPanel" in body, "the panel-opening path is missing"
    assert "loadSubagentsPanel" in body, "the panel refresh path is missing"


def test_the_richer_renderSpacesPanel_survived() -> None:
    source = (STATIC / "spaces.js").read_text(encoding="utf-8")
    start = source.index("function renderSpacesPanel(")
    end = source.find("\nfunction ", start + 1)
    body = source[start : end if end != -1 else None]

    assert "loadSpaces(options)" in body, "the options-forwarding renderer is missing"
    assert "_spacesPanelRenderRev" in body, "the render-revision guard is missing"


def test_no_function_was_lost() -> None:
    """Every name that existed before the cleanup must still be defined."""
    expected = {
        "commands.js": {"cmdStatus", "cmdMcp", "cmdSubagents", "cmdGoal", "cmdNew"},
        "spaces.js": {"renderSpacesPanel", "renderSpaceDetail", "renderSpaceSelector"},
        "panels.js": {"closeKanbanTaskDetail", "switchPanel"},
        "terminal.js": {"openSplitTerminal", "closeTerminalPane", "toggleSplitTerminal"},
    }
    for name, names in expected.items():
        defined = set(_definitions(name))
        missing = names - defined
        assert not missing, f"{name} lost functions: {missing}"
