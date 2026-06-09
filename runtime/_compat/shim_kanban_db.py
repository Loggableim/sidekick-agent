"""Shim for sidekick_cli.kanban_db — re-exports all from cli.kanban_db."""
from __future__ import annotations

from cli.kanban_db import *  # noqa: F401, F403
from cli.kanban_db import (  # noqa: F401
    connect,
    list_boards,
    read_board_metadata,
    DEFAULT_FAILURE_LIMIT,
)
