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

## Screenshots

### `sidekick doctor` — System health check

```
┌─────────────────────────────────────────────────────────┐
│                 🩺 Sidekick Doctor                      │
└─────────────────────────────────────────────────────────┘

◆ Python Environment
  ✓ Python 3.12.10
  ✓ Virtual environment active

◆ Required Packages
  ✓ OpenAI SDK  ✓ Rich (terminal UI)  ✓ PyYAML  ✓ HTTPX

◆ Configuration Files
  ✓ ~/.sidekick/.env file exists
  ✓ API key or custom endpoint configured
  ✓ ~/.sidekick/config.yaml exists

◆ Auth Providers
  ✓ OpenAI Codex (logged in)    ⚠ Nous Portal (not logged in)
  ✓ 26 provider connectivity checks (--check-providers)

◆ Memory Provider
  ✓ Built-in memory active

◆ Session State
  ✓ 10 sessions, 3 workspaces, 0 stale streams
```

### WebUI Dashboard

The WebUI runs at `http://127.0.0.1:8787` after `sidekick dashboard`:

- **Session panel** — create, select, rename, delete sessions
- **Chat interface** — real-time SSE streaming with message history
- **Workspace browser** — switch between active workspaces
- **Model/provider selector** — configure model per session
- **Settings panel** — theme, skin, TTS, language preferences

**Known working:** `/health` (200), session CRUD, workspace browsing,
streaming chat, static asset serving, SSE heartbeats (5s interval).

## Repository layout

```
sidekick/
├── cli/         Command-line interface (REPL, TUI, auth, config, setup)
├── runtime/     Agent runtime (providers, memory, cron, gateway, compat)
├── web/         WebUI server (48 API modules + 113 static assets)
├── shared/      Config, paths, sessions, logging, utility functions
├── tools/       ~100 tool implementations (registry, file ops, browser...)
├── docs/        Releases, roadmaps, audits, troubleshooting
├── tests/       Smoke tests (18 tests) and HTTP smoke (7 tests)
├── sidekick_app/  Package entrypoint with legacy-import bootstrap
└── sidekick_cli/  Legacy package forwarder (transition layer)
```

## Commands

| Command | Description |
|---------|-------------|
| `sidekick` | Interactive chat with the agent |
| `sidekick doctor` | System health check |
| `sidekick doctor -p` | Doctor + provider connectivity check |
| `sidekick dashboard` | Start the WebUI (http://127.0.0.1:8787) |
| `sidekick setup` | Interactive setup wizard |
| `sidekick --tui` | Terminal UI (TUI) mode |
| `sidekick status` | Show component status |
| `sidekick model` | Select default model/provider |
| `sidekick login` | Authenticate with an inference provider |
| `sidekick cron` | Cron job management |
| `sidekick gateway` | Messaging gateway management |
| `sidekick --help` | Full command reference (38+ subcommands) |

## Status

| Surface | Status |
|---------|--------|
| **CLI** | ✅ `sidekick --help`, `sidekick`, `sidekick doctor` (exit codes 0/1/2) |
| **TUI** | ✅ `sidekick --tui` (prompt_toolkit + curses, import verified) |
| **WebUI** | ✅ `sidekick dashboard`, `/health`, session CRUD, SSE streaming |
| **Runtime** | ✅ AIAgent (15K LOC), 76 registered tools, provider integrations |
| **Cron** | ✅ Scheduler + job management |
| **Gateway** | ✅ Messaging platform runner (0 import warnings) |
| **Smoke** | ✅ 18 CLI tests + 7 WebUI HTTP tests, all green |
| **CI** | ✅ Linux (full) + macOS (subset), Python 3.11 + 3.12 |

## Configuration

Config lives under `~/.sidekick/` (or `$SIDEKICK_HOME` / `$HERMES_HOME`):

- `~/.sidekick/config.yaml` — Settings
- `~/.sidekick/.env` — API keys
- `~/.sidekick/skills/` — Installed skills
- `~/.sidekick/state/webui/sessions/` — Session files (JSON)
- `~/.sidekick/logs/` — Log files (agent.log, errors.log, gateway.log)

Home directory resolution:
1. `$SIDEKICK_HOME` → `~/.sidekick/` (canonical)
2. `$HERMES_HOME` → `~/.hermes/` (legacy fallback)
3. Default → `~/.sidekick/`

## Graceful degradation without API key

All entry points work without any API key configured:

| Command | Without API key | With API key |
|---------|----------------|--------------|
| `sidekick --help` | ✅ Full help | ✅ Full help |
| `sidekick --version` | ✅ Version info | ✅ Version info |
| `sidekick doctor` | ✅ Shows what's missing | ✅ Full diagnostics |
| `sidekick doctor -p` | ⚠ Skips provider checks | ✅ Connectivity test |
| `sidekick dashboard` | ✅ Server starts, UI loads | ✅ + chat works |
| `sidekick` | ⚠ Shows setup instructions | ✅ Interactive chat |

## Legacy env vars preserved

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

See `docs/known-issues.md` for the full list.

Key items:
- Gateway warnings (2 non-blocking, `print_config_warnings`/`warn_deprecated_cwd_env_vars`)
- Session layer: `shared.sessions` and `web.api.models.Session` use different data models
- CLI help text still references `HERMES_*` env vars (legacy compat — intentional)
- No Windows CI (currently Linux + macOS only)

## Release history

| Version | Tag | Focus |
|---------|-----|-------|
| v0.1.0-monorepo | `v0.1.0-monorepo` | First monorepo baseline, all code merged |
| v0.2.0 | `v0.2.0` | Rebrand: CLI help, localStorage, audit |
| v0.3.0 | `v0.3.0` | Session contract, gateway warnings, CI smoke |
| v0.4.0 | `v0.4.0` | Error handling, doctor exit codes, troubleshooting |
| v0.5.0 | `v0.5.0` | Doctor --check-providers, macOS CI, streaming stability |

## Troubleshooting

See `docs/troubleshooting.md` for:
- Installation / Fresh Clone
- Missing API keys
- Provider/Credentials
- WebUI doesn't start
- Sessions/State paths
- Legacy `HERMES_*` aliases
- Migration from `~/.hermes` to `~/.sidekick`
- Logs and diagnostics
- Smoke tests
