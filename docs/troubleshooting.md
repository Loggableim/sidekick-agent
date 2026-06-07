# 🔧 Sidekick Troubleshooting Guide

Common issues, their root causes, and how to fix them.

---

## Installation

### Windows PowerShell installer

**One-liner:**
```powershell
irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/main/install.ps1 | iex
```

**What it does:**
- Installs Sidekick under `%LOCALAPPDATA%\sidekick\`
- Installs Python 3.11+ via `uv` (if not found) — no admin needed
- Installs Portable Git (if not found) — no admin needed
- Creates a Python virtual environment and installs Sidekick + all dependencies
- Creates a **desktop shortcut** for the WebUI dashboard
- **Opens the WebUI dashboard** in your default browser
- Sets `SIDEKICK_HOME` and `SIDEKICK_GIT_BASH_PATH` user environment variables

**Requirements:**
- Windows 10+ / Windows Server 2019+
- PowerShell 5.1+ (comes with Windows 10+)
- Internet connection

**No admin rights required.** The installer installs everything under your user profile.

**Common failures:**
- **PowerShell execution policy:** If you see a security error, run:
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```
- **Antivirus blocking `iex`:** Download and run locally:
  ```powershell
  Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Loggableim/sidekick-agent/main/install.ps1' -OutFile install.ps1
  .\install.ps1
  ```
- **Git clone fails:** Check your internet connection or proxy settings.
- **uv install fails:** The installer falls back to system Python. Ensure Python 3.11+ is installed from [python.org](https://python.org).

**To update an existing install:**
```powershell
.\install.ps1 -UpdateOnly
```

**To skip post-install health check:**
```powershell
.\install.ps1 -NoDoctor
```

### `pip install -e .` (macOS / Linux / manual)

```bash
# Install the project in editable mode
pip install -e .
```

**Common failures:**
- **Missing build tools**: Install `python3-dev` / `python3-venv` / build-essential on Linux, or Xcode CLI tools on macOS.
- **Conflicting packages**: Use a fresh virtual environment:
  ```bash
  python3 -m venv sidekick-venv
  source sidekick-venv/bin/activate
  pip install -e .
  ```
- **Permission errors**: Use `--user` flag or a virtual environment — never `sudo pip`.

After install, verify:

```bash
sidekick --help        # Should show usage
sidekick --version     # Should show version string
```

---

## Missing API Keys

### `sidekick doctor` shows "No API keys configured"

Sidekick needs at least one provider to be configured.

```bash
sidekick doctor           # Run diagnostics to see what's missing
sidekick doctor --fix     # Auto-fix what's possible
sidekick setup            # Interactive configuration wizard
```

**Ways to configure a provider:**

1. **Interactive setup (recommended):**
   ```bash
   sidekick setup
   ```

2. **Environment variables:**
   ```bash
   export OPENAI_API_KEY="sk-..."
   export ANTHROPIC_API_KEY="sk-..."
   export OPENROUTER_API_KEY="..."
   ```
   Or create a `~/.sidekick/.env` file:
   ```
   OPENAI_API_KEY=sk-...
   ```

3. **Via config file (`~/.sidekick/config.yaml`):**
   ```yaml
   provider: openai
   model: gpt-4o
   api_key: sk-...
   ```

> [!TIP]
> Run `sidekick doctor --fix` to automatically detect and suggest fixes for common config issues.

---

## Provider / Credentials

### `sidekick login` or authentication issues

```bash
sidekick login            # Set up API key-based auth
sidekick auth             # Manage authentication tokens
sidekick setup            # Full setup wizard
```

**"No provider configured" error:**

```bash
sidekick doctor           # Check provider status
sidekick model list      # List available models
```

If `sidekick doctor` shows providers but `sidekick` still can't use them:

1. Check that `~/.sidekick/config.yaml` has the correct provider name.
2. Verify the API key is set (in env vars or config).
3. Check for stale environment variables that override config.

```bash
# Debug: show current effective config
sidekick dump
```

---

## WebUI Won't Start

### `sidekick web` or `sidekick serve` fails

**Port conflicts:**

```bash
# Check what's using the default port (9119)
# On Linux/macOS:
lsof -i :9119
# On Windows (from PowerShell):
netstat -ano | findstr :9119

# Start on a different port:
sidekick web --port 8080
```

**Missing dependencies:**

```bash
# The WebUI needs fastapi and uvicorn
pip install 'fastapi' 'uvicorn[standard]'
```

**"Web UI requires fastapi and uvicorn":**

Install the web extras:
```bash
pip install -e ".[web]"
```

**WebUI starts but pages don't load:**

```bash
# Check the server log:
cat ~/.sidekick/logs/webui.log

# Verify the dist directory exists:
ls -la sidekick_cli/web_dist/index.html
# If missing, build the frontend:
cd web && npm install && npm run build
```

**"Port already in use" despite no visible process:**

On Windows, ports can linger in TIME_WAIT. Wait 30 seconds or use:
```bash
# Windows: find and kill the process
netstat -ano | findstr :9119
taskkill /PID <PID> /F
```

---

## Sessions / State Paths

Sidekick stores state and sessions under `~/.sidekick/state/webui/`:

```
~/.sidekick/
├── config.yaml          # Main configuration
├── .env                 # Environment variables / API keys
├── state/
│   └── webui/
│       └── sessions/    # Session JSON files
├── logs/
│   ├── webui.log        # WebUI server log
│   └── sidekick.log     # CLI log
├── profiles/            # Profile directories (if using profiles)
└── plugins/             # Installed plugins
```

**Session files** are JSON files stored in `~/.sidekick/state/webui/sessions/`. Each session has a UUID-based filename.

**To inspect sessions manually:**

```bash
ls ~/.sidekick/state/webui/sessions/
cat ~/.sidekick/state/webui/sessions/<session-id>.json
```

---

## Legacy `HERMES_*` Aliases

Sidekick was previously named **Hermes**. The legacy `HERMES_*` environment variables are still supported but deprecated:

| Legacy Var | Current Var |
|---|---|
| `HERMES_HOME` | `SIDEKICK_HOME` |
| `HERMES_WEBUI_HOST` | — (still `HERMES_WEBUI_HOST`) |
| `HERMES_WEBUI_PORT` | — (still `HERMES_WEBUI_PORT`) |
| `HERMES_WEBUI_PASSWORD` | — (still `HERMES_WEBUI_PASSWORD`) |
| `HERMES_WEBUI_LOG_FILE` | — (still `HERMES_WEBUI_LOG_FILE`) |

**Resolution order:**
1. `SIDEKICK_HOME` (highest priority)
2. `HERMES_HOME`

> If both are set, `SIDEKICK_HOME` wins. The config file and state directory will use `SIDEKICK_HOME` if set.

The CLI command `sidekick doctor` checks for usage of legacy env vars and suggests migration.

---

## Migration from `~/.hermes` to `~/.sidekick`

On first run, Sidekick automatically migrates data from `~/.hermes` to `~/.sidekick`:

- **Config**: Migrated on first `list_sessions()` or config read.
- **Sessions**: Migrated from `~/.hermes/state/webui/sessions/` to `~/.sidekick/state/webui/sessions/`.
- **Environment**: Old `~/.hermes/.env` is copied to `~/.sidekick/.env` if the new file doesn't exist.

The migration is **safe** — old files are never deleted, only copied. To force re-migration:

```bash
# Delete the migration marker and re-run a session command
rm ~/.sidekick/.migrated_from_hermes
sidekick doctor           # triggers migration check
```

To verify:
```bash
ls ~/.sidekick/config.yaml
ls ~/.sidekick/state/webui/sessions/
```

---

## Finding Logs

Sidekick writes logs to `~/.sidekick/logs/`:

| Log File | Content |
|---|---|
| `~/.sidekick/logs/webui.log` | WebUI server requests, errors, and startup info |
| `~/.sidekick/logs/sidekick.log` | CLI operations and diagnostics |
| `~/.sidekick/logs/gateway.log` | Gateway service (if running) |

**Tailing logs:**

```bash
# WebUI log
tail -f ~/.sidekick/logs/webui.log

# CLI log
tail -f ~/.sidekick/logs/sidekick.log
```

**Debug logging:**

```bash
# Enable verbose output
sidekick --verbose doctor
sidekick --debug web

# Or set environment variable
export SIDEKICK_LOG_LEVEL=DEBUG
```

---

## Running Smoke Tests

Smoke tests validate basic Sidekick functionality:

```bash
# Run all smoke tests
python tests/smoke_all.py

# Run only the WebUI HTTP smoke test
python tests/smoke_webui.py

# Run with verbose output
python -X tracemalloc tests/smoke_all.py
```

**Test files:**

| Test | What it checks |
|---|---|
| `tests/smoke_all.py` | Full smoke suite: imports, CLI, sessions, WebUI |
| `tests/smoke_webui.py` | WebUI HTTP: /health, static assets, session CRUD |
| `tests/test_paths.py` | Path resolution and config loading |

**Exit codes for `sidekick doctor`:**

| Exit Code | Meaning |
|---|---|
| `0` | All checks passed |
| `1` | Warnings only (non-critical issues) |
| `2` | Critical issues (missing API keys, broken config) |

---

## FAQ

### `sidekick --help` works but `sidekick` says "no provider"

This means the CLI loaded successfully but no provider is configured.

```bash
# 1. Check what providers are available
sidekick doctor

# 2. Set up a provider
sidekick setup

# 3. Or set environment variables
export OPENAI_API_KEY="sk-..."
export OPENROUTER_API_KEY="..."

# 4. Verify the configuration
sidekick doctor
```

**Root causes:**
- No `provider` or `api_key` in `~/.sidekick/config.yaml`
- Environment variables not set in the current shell
- `.env` file not loaded (check `~/.sidekick/.env`)
- Stale `~/.hermes/` config not migrated (run `sidekick doctor --fix`)

### `sidekick doctor` says "config issue" but I configured everything

Try:

```bash
sidekick doctor --fix     # Auto-fix
sidekick dump              # Show effective config for debugging
```

### Where is `~/.sidekick` on my system?

| OS | Default Path |
|---|---|
| Linux/macOS | `~/.sidekick/` |
| Windows | `C:\Users\<user>\.sidekick\` |

You can override with `SIDEKICK_HOME` environment variable.

### I deleted `~/.sidekick` — how do I recover?

Re-run setup:

```bash
sidekick setup
```

Sessions and configuration are local — there is no cloud backup. If you had an old `~/.hermes/`, the auto-migration will recreate state from there.

### Gateway service won't start

```bash
sidekick doctor                              # Check gateway status
systemctl --user status sidekick-gateway     # Linux systemd
# Or check the gateway log:
cat ~/.sidekick/logs/gateway.log
```
