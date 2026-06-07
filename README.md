# Sidekick

**Sidekick** is a unified assistant monorepo combining CLI, TUI, and WebUI in one installable package. This repo replaces the historical split between separate agent and webui repositories.

![CI](https://github.com/Loggableim/sidekick-agent/actions/workflows/ci.yml/badge.svg)

## Quick start

```bash
git clone https://github.com/Loggableim/sidekick-agent.git
cd sidekick-agent
python -m pip install -e .
sidekick --help
sidekick doctor
```

### Start a chat

```bash
sidekick                     # Interactive REPL
sidekick --tui               # Terminal UI mode
sidekick dashboard           # WebUI at http://127.0.0.1:8787
```

### Legacy alias

```bash
hermes --help                # Same binary as sidekick
```

## Repository layout

```
sidekick/
├── cli/         Command-line interface (REPL, TUI, auth, config, setup, gateway mgmt)
├── runtime/     Agent runtime (providers, memory, cron, gateway, compat shims)
├── web/         WebUI server (48 API modules + 113 static assets)
├── shared/      Config, paths, sessions, logging, utility functions
├── tools/       ~100 tool implementations (registry, file ops, browser, terminal, ...)
├── docs/        Architecture, migration, audits, release notes
├── tests/       Smoke tests and regression tests
├── sidekick_app/  Package entrypoint with legacy-import bootstrap
└── sidekick_cli/  Legacy package forwarder (transition layer)
```

## Commands

| Command | Description |
|---------|-------------|
| `sidekick` | Interactive chat with the agent |
| `sidekick doctor` | System health check |
| `sidekick dashboard` | Start the WebUI |
| `sidekick setup` | Interactive setup wizard |
| `sidekick --tui` | Terminal UI (TUI) mode |
| `sidekick status` | Show component status |
| `sidekick model` | Select default model/provider |
| `sidekick login` | Authenticate with an inference provider |
| `sidekick cron` | Cron job management |
| `sidekick gateway` | Messaging gateway management |
| `sidekick --help` | Full command reference (38+ subcommands) |

## Configuration

Config lives under `~/.sidekick/` (or `$SIDEKICK_HOME` / `$HERMES_HOME`):

- `~/.sidekick/config.yaml` — Settings
- `~/.sidekick/.env` — API keys
- `~/.sidekick/skills/` — Installed skills

Home directory resolution:
1. `$SIDEKICK_HOME` → `~/.sidekick/` (canonical)
2. `$HERMES_HOME` → `~/.hermes/` (legacy fallback)
3. Default → `~/.sidekick/`

## Status

| Surface | Status |
|---------|--------|
| **CLI** | ✅ `sidekick --help`, `sidekick`, `sidekick doctor` |
| **TUI** | ✅ `sidekick --tui` (prompt_toolkit + curses) |
| **WebUI** | ✅ `sidekick dashboard`, `/health`, session CRUD |
| **Runtime** | ✅ AIAgent, 76 registered tools, provider integrations |
| **Cron** | ✅ Scheduler + job management |
| **Gateway** | ✅ Messaging platform runner |
| **Smoke** | ✅ 10/10 tests pass |

## Migration

This repo absorbs code from `cids-hermes-agent` and `cids-hermes-webui`.
See `docs/consolidation.md` and `docs/releases/v0.1.0-monorepo.md` for details.

### Key changes from the old split

- All code now lives in one installable package
- Imports use `runtime.*`, `cli.*`, `shared.*`, `tools.*`, `web.*` — no more `agent.*` or `hermes_cli.*`
- Legacy `HERMES_*` env vars are read as fallbacks; `SIDEKICK_*` is canonical
- The `hermes` command is an alias for `sidekick` (same binary)

### Legacy env vars preserved

`HERMES_HOME`, `HERMES_STATE_DIR`, `HERMES_WEBUI_HOST`, `HERMES_WEBUI_PORT`,
`HERMES_OPTIONAL_SKILLS`, `HERMES_LANGUAGE`, `HERMES_ACCEPT_HOOKS`,
`HERMES_YOLO_MODE`, `HERMES_QUIET`

## Install from source

```bash
# Minimal (CLI only)
python -m pip install -e .

# With WebUI extras
python -m pip install -e ".[web]"

# Everything (recommended for development)
python -m pip install -e ".[all]"
```

## Known issues

- Gateway prints 2 non-blocking warnings about missing config validators (harmless)
- Session-Layer: `shared/sessions.py` und `web/api/session_ops.py` noch nicht vollständig vereinheitlicht
- See `docs/releases/v0.2.0.md` for full details
