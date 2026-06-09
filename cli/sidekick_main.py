"""Redirect entry point to sidekick_cli.main."""
import sys
from cli.main import main  # noqa: F401

if __name__ == "__main__":
    sys.exit(main())
