# WebUI Source Structure Survey — Phase 4 Migration Planning

> Generated: 2026-06-07
> Source: `C:\HermesPortable\cids-hermes-webui` (legacy)
> Target: `C:\HermesPortable\sidekick/web/` (legacy)

---

## 1. API Module Inventory (48 modules)

| # | Module | Lines | Role | Rewrite Priority |
|---|--------|-------|------|-----------------|
| 1 | `routes.py` | 12,054 | **ALL HTTP route handlers** — monolithic, delegates to other modules | HIGH (must split) |
| 2 | `streaming.py` | 4,721 | SSE streaming engine + agent thread runner | HIGH (FastAPI SSE rewrite) |
| 3 | `config.py` | 4,510 | Shared config, constants, global state — central hub imported by everything | MEDIUM (migrate to Pydantic Settings) |
| 4 | `models.py` | 2,297 | In-memory session store + session model | MEDIUM |
| 5 | `providers.py` | 1,538 | Provider CRUD management endpoints | MEDIUM |
| 6 | `browser_runtime.py` | 1,504 | Browser automation runtime tools | LOW (optional feature) |
| 7 | `kanban_bridge.py` | 1,309 | Kanban board CRUD via `hermes_cli.kanban_db` | LOW (Nova-specific) |
| 8 | `agents.py` | 1,229 | Multi-agent registry (SQLite-backed) | LOW (Nova-specific) |
| 9 | `gmail_tools.py` | 1,232 | Gmail IMAP/SMTP backend | LOW (plugin) |
| 10 | `profiles.py` | 1,056 | Profile state management (profile switching) | MEDIUM |
| 11 | `onboarding.py` | 1,045 | First-run onboarding flow | LOW |
| 12 | `appstore.py` | 815 | App manifest discovery & install | LOW |
| 13 | `oauth.py` | 770 | In-app OAuth flows | LOW |
| 14 | `space_engine.py` | 767 | Space Engine v2 (spaces abstraction) | MEDIUM |
| 15 | `agent_sessions.py` | 726 | Nova session reader helpers (`state.db`) | LOW |
| 16 | `evey_tools.py` | 725 | Evey Tools API | LOW (plugin) |
| 17 | `goals.py` | 640 | Hermes persistent session goals bridge | MEDIUM |
| 18 | `session_recovery.py` | 654 | Session recovery from `.bak` snapshots | MEDIUM |
| 19 | `updates.py` | 577 | Self-update checker (git) | LOW |
| 20 | `agent_workspace.py` | 565 | LLM-based intent recognition for agents | LOW |
| 21 | `discord_bot.py` | 407 | Discord Bot API handlers | LOW |
| 22 | `auth.py` | 382 | Password auth | HIGH (security critical) |
| 23 | `agent_health.py` | 378 | Gateway heartbeat checks | LOW |
| 24 | `dispatcher.py` | 354 | Global task dispatcher (cron-based) | LOW |
| 25 | `rollback.py` | 320 | Filesystem checkpoint/rollback | LOW |
| 26 | `workspace_isolation.py` | 310 | Workspace isolation (Spaces alternate) | LOW |
| 27 | `helpers.py` | 302 | HTTP helpers (JSON, validation, sanitization) | MEDIUM |
| 28 | `terminal.py` | 291 | Embedded workspace terminal (POSIX-only) | LOW (unused on Windows) |
| 29 | `upload.py` | 284 | Multipart file upload parser | MEDIUM |
| 30 | `worktrees.py` | 261 | Git worktree helpers | LOW |
| 31 | `extensions.py` | 254 | Opt-in extension hooks | LOW |
| 32 | `error_logger.py` | 249 | Structured error logger (SQLite) | LOW |
| 33 | `dashboard_probe.py` | 240 | Nova dashboard health probe | LOW |
| 34 | `gateway_watcher.py` | 234 | Gateway session watcher daemon | LOW |
| 35 | `clarify.py` | 206 | Clarify prompt state | LOW |
| 36 | `metering.py` | 194 | TPS streaming metering | LOW |
| 37 | `command_stream.py` | 178 | Per-session terminal output capture | MEDIUM |
| 38 | `system_health.py` | 167 | Host CPU/RAM/disk metrics | LOW |
| 39 | `turn_journal.py` | 164 | Crash-safe turn journal helpers | LOW |
| 40 | `session_ops.py` | 160 | Session retry/undo/truncate helpers | MEDIUM |
| 41 | `request_diagnostics.py` | 160 | Slow request diagnostics | LOW |
| 42 | `commands.py` | 124 | Expose agent COMMAND_REGISTRY | LOW |
| 43 | `state_sync.py` | 118 | Optional state.db sync bridge | LOW |
| 44 | `compression_anchor.py` | 97 | Session compression metadata | LOW |
| 45 | `background.py` | 87 | Background task tracking | LOW |
| 46 | `startup.py` | 136 | Startup helpers (credential perms) | LOW |
| 47 | `__init__.py` | 1 | Package marker | — |
| **TOTAL API** | **~48,148** | | |

### Server entry points (root level):
| File | Lines | Role |
|------|-------|------|
| `server.py` | 609 | Main HTTP server (stdlib `http.server` → ThinHTTPServer) |
| `mcp_server.py` | 567 | MCP protocol server |

---

## 2. Key Internal Imports (from `hermes_cli` / cids-hermes-agent)

The WebUI imports from **10 distinct submodules** of `hermes_cli` (all wrapped in lazy `try/except` for graceful degradation):

| hermes_cli submodule | Used by | What's imported |
|---------------------|---------|-----------------|
| `hermes_cli.commands` | `commands.py` | `COMMAND_REGISTRY` |
| `hermes_cli.plugins` | `commands.py`, `routes.py` | `get_plugin_commands`, `discover_plugins`, `get_plugin_manager` |
| `hermes_cli.goals` | `goals.py` | `GoalManager`, `GoalState`, `judge_goal`, `save_goal`, `CONTINUATION_PROMPT_TEMPLATE`, `DEFAULT_MAX_TURNS` |
| `hermes_cli.profiles` | `profiles.py` | `list_profiles`, `create_profile`, `delete_profile` |
| `hermes_cli.models` | `config.py`, `providers.py`, `routes.py` | `_PROVIDER_ALIASES`, `provider_model_ids`, `list_available_providers` |
| `hermes_cli.auth` | `config.py`, `onboarding.py`, `providers.py` | `get_auth_status` |
| `hermes_cli.config` | `onboarding.py`, `kanban_bridge.py` | `reload`, `load_config` |
| `hermes_cli.tools_config` | `config.py` | `_get_platform_tools` |
| `hermes_cli.runtime_provider` | `config.py`, `routes.py`, `streaming.py` | `resolve_runtime_provider` |
| `hermes_cli.kanban_db` | `kanban_bridge.py` | `kanban_db` module (as `kb`) |

**Pattern**: All imports use lazy `try/except ImportError` blocks with graceful fallbacks — the WebUI is designed to run standalone (without the agent package).

---

## 3. Frontend Architecture

**No build system.** No `package.json`, no npm, no bundler. Pure vanilla JS SPA.

### Static assets (`static/` — 90+ files):

**Core JS modules (~54,000 lines total):**
| File | Lines | Description |
|------|-------|-------------|
| `boot.js` | 2,189 | App bootstrapping, theme/skin init |
| `ui.js` | 9,312 | Core UI framework — dialogs, toasts, panels, settings |
| `panels.js` | 9,483 | Panel system — workspace explorer, kanban, settings panels |
| `messages.js` | 3,858 | Chat message rendering, SSE stream handling |
| `sessions.js` | 3,722 | Session list sidebar, session management |
| `commands.js` | 1,225 | Slash command system (`/help`, `/clear`, `/model`, etc.) |
| `browser.js` | 1,925 | Browser automation UI (iframe sandbox) |
| `workspace.js` | 695 | Workspace file browser UI |
| `spaces.js` | 1,306 | Space management UI |
| `terminal.js` | 973 | Embedded xterm.js terminal |
| `onboarding.js` | 821 | First-run onboarding wizard |
| `gmail.js` | 1,453 | Gmail panel UI |
| `discord.js` | 438 | Discord panel |
| `discord-chat.js` | 955 | Discord chat UI |
| `enhancements.js` | 1,152 | UI enhancements |
| `agents.js` | 1,350 | Agent management UI |
| `i18n.js` | 10,984 | Internationalization (all UI strings) |
| `sw.js` | 163 | Service worker (PWA) |
| `login.js` | 118 | Login screen |
| `power.js` | 138 | Power user tools |

**CSS:**
| File | Lines | Description |
|------|-------|-------------|
| `style.css` | 8,014 | Main stylesheet (light/dark/skins) |
| `agents.css` | 1,403 | Agent dashboard styles |
| `spaces.css` | 483 | Spaces panel styles |
| `gmail-panel.css` | 1,346 | Gmail styles |
| `discord-panel.css` | 202 | Discord styles |
| `discord-chat.css` | 536 | Discord chat styles |
| `agents-dashboard.css` | 350 | Agent dashboard |
| `xterm.css` | 221 | Terminal emulator |
| `icons.js` | 135 | SVG icon injection script |

**Other:** `index.html` (2,242 lines, PWA manifest, favicons, SVGs, PNGs)

### Architecture notes:
- **SSE-based streaming** — no WebSockets, uses `EventSource` for agent responses
- **Theme system** — light/dark + skins (`default`, `ares`, `mono`, `slate`, `poseidon`, `sisyphus`, `charizard`, `sienna`, `matrix`)
- **Inline `<base>` resolution** for subpath mounting support
- **Old-style JS** — global namespace, `S` state object, function-based modules

---

## 4. Dependencies

### Runtime (`requirements.txt`):
```
pyyaml>=6.0
```
**That's it.** Everything else is Python stdlib. The WebUI is designed to have zero external dependencies beyond `pyyaml`.

### Implied runtime needs (not in requirements.txt):
- `hermes_cli` (cids-hermes-agent) — optional, lazy-imported
- `mcp` package — for `mcp_server.py` only
- `fcntl`, `termios`, `select` — POSIX-only imports (terminal.py, browser_runtime.py)

### Docker:
- Base image: `python:3.12-slim`
- `uv` for package management
- No node.js or frontend toolchain

---

## 5. Rewrite Complexity Assessment

### What needs the MOST rewrites:

| Component | Est. Effort | Reason |
|-----------|-------------|--------|
| **`routes.py`** (12,054 lines) | **Very High** | Monolithic — every endpoint defined as method on `Handler` class. Must split into FastAPI routers. |
| **`streaming.py`** (4,721 lines) | **Very High** | SSE streaming via manual `wfile.write`. Must become FastAPI SSE or Starlette `StreamingResponse`. |
| **`config.py`** (4,510 lines) | **High** | Global mutable state via module-level dicts/locks. Must become Pydantic `Settings` + dependency injection. |
| **`server.py`** (609 lines) | **High** | stdlib `ThreadingHTTPServer` → FastAPI/Uvicorn. Network isolation hack must be adapted. |
| **`auth.py`** (382 lines) | **High** | Cookie-based manual auth → FastAPI middleware/dependencies. |
| **`helpers.py`** (302 lines) | **Medium** | JSON response helpers → FastAPI `Response` models. |
| **`upload.py`** (284 lines) | **Medium** | Manual multipart parser → FastAPI `UploadFile`. |
| **`models.py`** (2,297 lines) | **Medium** | Thread-safe session store → async session management. |
| **`terminal.py`** (291 lines) | **Low** | PTY spawner — mostly POSIX, could become FastAPI WebSocket. |

### What can be mostly preserved (low rewrite):

| Component | Reason |
|-----------|--------|
| `commands.py` (124 lines) | Thin wrapper, trivial to port |
| `extensions.py` (254 lines) | Simple static injection |
| `compression_anchor.py` (97 lines) | Pure utility functions |
| `turn_journal.py` (164 lines) | JSONL file append helpers |
| `gw_helpers.py` | Simple bus/queue integration |
| `background.py` (87 lines) | Tiny dict-based tracker |
| `state_sync.py` (118 lines) | Optional bridge, simple |
| `dashboard_probe.py` (240 lines) | HTTP probe, simple |
| `error_logger.py` (249 lines) | SQLite logger, portable 1:1 |
| `system_health.py` (167 lines) | `/proc` readers, Linux-only |
| `worktrees.py` (261 lines) | Git subprocess wrapper |
| `startup.py` (136 lines) | chmod helpers, fine as-is |

### Frontend migration notes:

The JS/CSS is ~54,000 lines of vanilla JS with no build system. **Complete rewrite vs. port decision needed:**
- **Option A**: Serve existing static files from FastAPI. Zero frontend work.
- **Option B**: Incrementally modernize (add bundler, TypeScript).
- **Option C**: Full rewrite with React/Vue/Svelte.

**Recommendation**: Phase 4 should **Option A** the frontend (serve the existing static files from the new FastAPI server) and focus rewrite effort on the Python backend. The frontend can be modernized in a later phase.

---

## 6. Summary Statistics

| Metric | Value |
|--------|-------|
| API Python modules | **48** (in `api/`) |
| Total API code | **~48,000 lines** |
| Server entry points | 2 (`server.py`, `mcp_server.py`) |
| Static assets | **90+ files** (~54,000 lines JS + ~8,000 lines CSS + 2,242 lines HTML) |
| External PyPI deps | **1** (`pyyaml>=6.0`) |
| hermes_cli submodules used | **10** (all lazy-imported) |
| Frontend build system | **None** (vanilla JS SPA) |
| Docker base | `python:3.12-slim` |
| Python version target | 3.12+ |
| File with highest complexity | `routes.py` (12,054 lines, monolithic) |

---

## 7. Migration Strategy Recommendations

1. **Preserve the static/ directory** — serve it as-is from FastAPI. No frontend changes in Phase 4.
2. **Replace `server.py` + stdlib HTTP** → FastAPI with Uvicorn.
3. **Replace `auth.py` cookie-jar** → FastAPI middleware.
4. **Split `routes.py`** → API routers by domain (sessions, providers, agents, workspace, kanban, etc.).
5. **Replace `streaming.py` manual SSE** → FastAPI `StreamingResponse` or Starlette SSE.
6. **Replace `config.py` module globals** → Pydantic `BaseSettings` with dependency injection.
7. **Keep `hermes_cli` lazy imports** — strong decoupling already exists.
8. **Keep all "tool" modules** (`gmail_tools.py`, `evey_tools.py`, `kanban_bridge.py`, etc.) as-is — they're already clean domain modules.
