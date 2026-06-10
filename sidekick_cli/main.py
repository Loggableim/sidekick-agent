"""Legacy module entry point for ``python -m sidekick_cli.main``."""
from __future__ import annotations

from cli.main import main


if __name__ == "__main__":
    raise SystemExit(main())
