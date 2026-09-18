"""Tests for parallel boot requests (backlog item 45).

The game-mode status sync ran sequentially after the settings work, so its
round-trip was added to boot time. It is independent of the settings payload
and now starts in parallel.
"""
from __future__ import annotations

from pathlib import Path

BOOT_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "boot.js"


def _boot_block() -> str:
    source = BOOT_JS.read_text(encoding="utf-8")
    start = source.index("const _bootSettingsReady = (async()=>{")
    end = source.index("})();", start)
    return source[start:end]


def test_game_mode_sync_starts_before_the_settings_await() -> None:
    block = _boot_block()

    sync_at = block.index("const _gameModeSync = _syncGameModeStateFromServer();")
    settings_at = block.index("await _bootTimeout(api('/api/settings')")
    assert sync_at < settings_at, (
        "the game-mode sync must start before the settings request is awaited"
    )


def test_game_mode_sync_is_still_awaited_before_boot_finishes() -> None:
    block = _boot_block()
    assert "await _gameModeSync;" in block, (
        "the parallel sync must still be awaited so the button state is correct"
    )


def test_no_duplicate_game_mode_sync_call() -> None:
    """The old sequential call must be gone, not just moved."""
    block = _boot_block()
    assert block.count("_syncGameModeStateFromServer()") == 1, (
        "the sync is called more than once in the boot block"
    )
