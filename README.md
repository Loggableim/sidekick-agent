# Sidekick

**Sidekick** is a unified assistant monorepo combining CLI, TUI, and WebUI in one installable package. This repo replaces the historical split between `cids-hermes-agent` and `cids-hermes-webui`.

## Quick start

```bash
git clone https://github.com/sidekick-ai/sidekick.git
cd sidekick
python -m pip install -e .
sidekick --help
sidekick doctor
```

### WebUI

```bash
sidekick dashboard
# Opens http://127.0.0.1:8787
```

## Repository layout

```
sidekick/
├── cli/         Command-line interface (REPL, TUI, setup wizard, auth)
├── runtime/     Agent runtime (providers, memory, cron, gateway)
├── web/         WebUI server, API routes, static assets
├── shared/      Config, paths, sessions, logging, utils
├── tools/       Tool implementations (registry, files, browser, terminal, ...)
├── docs/        Architecture and migration documentation
├── tests/       Regression tests
└── sidekick_app/  Package entrypoint with legacy-import bootstrap
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
| `sidekick --help` | Full command reference |

## Configuration

Config lives under `~/.sidekick/` (or `$SIDEKICK_HOME`):

- `~/.sidekick/config.yaml` — Settings
- `~/.sidekick/.env` — API keys
- `~/.sidekick/skills/` — Installed skills

Legacy `HERMES_HOME` and `HERMES_*` env vars are still read as fallbacks during migration.

## Migration

This repo absorbs code from:
- `cids-hermes-agent` — agent runtime, CLI, tools, cron, gateway
- `cids-hermes-webui` — WebUI server and frontend assets

See `docs/consolidation.md` for the migration map and naming conventions.

## Install from source

```bash
python -m pip install -e .
# With optional dependencies:
python -m pip install -e ".[web]"   # WebUI extras
python -m pip install -e ".[all]"   # Everything
```