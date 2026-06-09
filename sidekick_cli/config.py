"""
Shim — forwards ``sidekick_cli.config`` imports to the correct modules.

This file exists because ``from sidekick_cli.config import load_config``
cannot be handled by ``sidekick_cli/__init.py__``'s ``__getattr__`` —
Python resolves the dotted import path *before* calling ``__getattr__`` on
the parent package, and ``sidekick_cli/config.py`` does not exist on disk.
"""
from cli.config import *  # noqa: F401, F403
from cli.config import (  # noqa: F401
    load_config,
    print_config_warnings,
    get_config_path,
    read_raw_config,
    _expand_env_vars,
    warn_deprecated_cwd_env_vars,
)
# HOST/PORT are defined in web.api.config, not cli.config
from web.api.config import HOST, PORT  # noqa: F401
