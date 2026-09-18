"""Regression test: space tests must not write into the live Space registry.

Bug (observed 2026-09-14, live ``agent.log``): ``test_nova_kanban_projection_cycle42``
called ``space_engine.Space(slug, slug).save_config({...project_dir...})`` BEFORE
monkeypatching ``SPACES_ROOT``/``SIDEKICK_HOME``. ``Space.root`` resolves
``_spaces_root()`` at property-access time, so ``save_config`` wrote into the LIVE
``C:\\sidekick\\home\\spaces\\<slug>\\space.yaml`` for ``nova``,
``finanzjunkie``, and ``aquarium-zentrum`` — with a ``project_dir`` pointing into
the test's temporary directory, which conftest deletes at teardown.

Result on the live install: all three Space configs carried a dead ``project_dir``,
``get_project_dir()`` logged ``project_dir ... does not exist`` on every call
(1265 warnings in the last 3 MB of ``agent.log``) and returned ``None``, breaking
project_dir-dependent features for those Spaces. Introduced 2026-08-03 in 7272ee6.

The sibling contract tests (``test_nova_three_space_contract_cycle27``,
``test_space_memory_path._reset_spaces``) already patch the root BEFORE writing;
this guard keeps every future space-writing test on that pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every test that calls Space.save_config must redirect the spaces root (or use a
# custom_root) BEFORE the first save_config call, so the write lands in the test's
# temporary root instead of the live Sidekick home.
_GUARDED_FILES = (
    "tests/test_nova_kanban_projection_cycle42.py",
    "tests/test_nova_space_inventory.py",
    "tests/test_nova_three_space_contract_cycle27.py",
    "tests/test_space_memory_path.py",
)

_PATCH_PATTERN = re.compile(
    r"setattr\(space_engine,\s*[\"']SPACES_ROOT[\"']"
    r"|setenv\(\s*[\"']SIDEKICK_HOME[\"']"
    r"|custom_root\s*="
)

_SAVE_PATTERN = re.compile(r"\.save_config\(|\bsave_config\(")


def _code_only(line: str) -> str:
    """Strip trailing comments so comment text cannot satisfy the patterns."""
    return line.split("#", 1)[0]

# Helpers that redirect the root on the caller's behalf (e.g. _reset_spaces).
_HELPER_DEF_PATTERN = re.compile(r"def (_[a-z0-9_]+)\(monkeypatch")


def _test_functions(source: str) -> list[tuple[str, list[str]]]:
    """Return (name, body_lines) for every top-level test function."""
    functions: list[tuple[str, list[str]]] = []
    lines = source.splitlines()
    current_name = None
    current_body: list[str] = []
    for line in lines:
        match = re.match(r"(?:async )?def (test_[a-zA-Z0-9_]+)\(", line)
        if match:
            if current_name is not None:
                functions.append((current_name, current_body))
            current_name = match.group(1)
            current_body = []
        elif current_name is not None:
            if re.match(r"^(?:async )?def \w+\(", line) or (line and not line[0].isspace() and line.strip()):
                functions.append((current_name, current_body))
                current_name = None
                current_body = []
            else:
                current_body.append(line)
    if current_name is not None:
        functions.append((current_name, current_body))
    return functions


def _helper_names_with_root_patch(source: str) -> set[str]:
    """Names of module-level helpers that patch SPACES_ROOT/SIDEKICK_HOME."""
    helpers: set[str] = set()
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = _HELPER_DEF_PATTERN.match(line.strip())
        if not match:
            continue
        # Look ahead within the helper body (until the next top-level def).
        for follow in lines[index + 1 :]:
            if re.match(r"^(?:async )?def \w+\(", follow):
                break
            if _PATCH_PATTERN.search(follow):
                helpers.add(match.group(1))
                break
    return helpers


@pytest.mark.parametrize("relative", _GUARDED_FILES)
def test_space_saving_tests_patch_root_before_first_save_config(relative: str) -> None:
    """save_config must never run before the spaces root is redirected to tmp."""
    path = REPO_ROOT / relative
    source = path.read_text(encoding="utf-8-sig")
    helper_names = _helper_names_with_root_patch(source)

    for name, body in _test_functions(source):
        code_lines = [_code_only(line) for line in body]
        if not any(_SAVE_PATTERN.search(line) for line in code_lines):
            continue
        save_offset = None
        patch_offset = None
        for offset, line in enumerate(code_lines):
            if save_offset is None and _SAVE_PATTERN.search(line):
                save_offset = offset
            if patch_offset is None and (
                _PATCH_PATTERN.search(line)
                or any(f"{helper}(" in line for helper in helper_names)
            ):
                patch_offset = offset
        assert patch_offset is not None and patch_offset < save_offset, (
            f"{relative}::{name} calls save_config before patching SPACES_ROOT/"
            "SIDEKICK_HOME/custom_root — Space.root resolves the LIVE Sidekick home "
            "at property-access time, so the write lands in the live space.yaml and "
            "survives the tmp cleanup as a dead project_dir"
        )