# Audit Report: OpenAI Codex (DeepSeek V4 Flash)

## Zusammenfassung
- Gesamt: 84 Fehler (inkl. False Positives in der Detail-Liste markiert)
- Kategorie A (Env-Vars): 38
- Kategorie B (User-Strings): 23
- Kategorie C (Dateinamen): 0
- Kategorie D (Pfade): 8 (als false positives identifiziert - intentional backward compat)
- Kategorie E (Services): 4
- Kategorie F (URLs): 0

## Detail-Liste

### Kategorie A: Env-Vars (KRITISCH) — HERMES_ ohne SIDEKICK_ Fallback

| # | Datei | Zeile | Code | Status |
|---|-------|-------|------|--------|
| A1 | `cli/auth.py` | 1835 | `get_env_value("HERMES_SPOTIFY_CLIENT_ID")` | ❌ FEHLT SIDEKICK_SPOTIFY_CLIENT_ID |
| A2 | `cli/auth.py` | 1858 | `get_env_value("HERMES_SPOTIFY_REDIRECT_URI")` | ❌ FEHLT |
| A3 | `cli/auth.py` | 1874 | `get_env_value("HERMES_SPOTIFY_API_BASE_URL")` | ❌ FEHLT |
| A4 | `cli/auth.py` | 1889 | `get_env_value("HERMES_SPOTIFY_ACCOUNTS_BASE_URL")` | ❌ FEHLT |
| A5 | `cli/auth.py` | 2295 | `save_env_value("HERMES_SPOTIFY_CLIENT_ID", raw)` | ❌ Schreibt nur HERMES_ |
| A6 | `cli/auth.py` | 2299 | `save_env_value("HERMES_SPOTIFY_REDIRECT_URI", ...)` | ❌ Schreibt nur HERMES_ |
| A7 | `cli/auth.py` | 2721 | `os.getenv("HERMES_CA_BUNDLE")` | ❌ Kein SIDEKICK_CA_BUNDLE |
| A8 | `cli/auth.py` | 3297 | `os.getenv("HERMES_PORTAL_BASE_URL")` | ❌ Kein SIDEKICK_PORTAL_BASE_URL |
| A9 | `cli/auth.py` | 3574 | `os.getenv("HERMES_PORTAL_BASE_URL")` | ❌ Kein SIDEKICK_PORTAL_BASE_URL |
| A10 | `cli/auth.py` | 5064 | `os.getenv("HERMES_PORTAL_BASE_URL")` | ❌ Kein SIDEKICK_PORTAL_BASE_URL |
| A11 | `cli/auth.py` | 5185 | `os.getenv("HERMES_CA_BUNDLE")` | ❌ Kein SIDEKICK_CA_BUNDLE |
| A12 | `cli/cli.py` | 2433 | `os.getenv("HERMES_INFERENCE_PROVIDER")` | ❌ Kein SIDEKICK_INFERENCE_PROVIDER |
| A13 | `cli/cli.py` | 2494 | `os.environ.get("HERMES_IGNORE_RULES")` | ❌ Hat SIDEKICK_ in Zeile → dual-read ✅ |
| A14 | `cli/cli.py` | 2498 | `os.getenv("HERMES_EPHEMERAL_SYSTEM_PROMPT")` | Hat SIDEKICK_ in Zeile ✅ |
| A15 | `cli/cli.py` | 2574 | Kommentar: `shared across all ... for this HERMES_HOME` | Docstring/kommentar ✅ |
| A16 | `cli/cli.py` | 11055 | `os.getenv("HERMES_REDACT_SECRETS")` | Hat SIDEKICK_ in Zeile ✅ |
| A17 | `cli/cli.py` | 13069 | `os.getenv("HERMES_SIGTERM_GRACE")` | Hat SIDEKICK_ in Zeile ✅ |
| A18 | `cli/config.py` | 139-143 | `"HERMES_LANGFUSE_ENV"` etc. in `_DEPRECATED_VARS` | ❌ Env-var-namen ohne SIDEKICK_ |
| A19 | `cli/config.py` | 1748 | `"HERMES_QWEN_BASE_URL": {...}` in ENV_VAR_METADATA | ❌ Kein SIDEKICK_QWEN_BASE_URL |
| A20 | `cli/config.py` | 1756 | `"HERMES_GEMINI_CLIENT_ID": {...}` | ❌ Kein SIDEKICK_GEMINI_CLIENT_ID |
| A21 | `cli/config.py` | 1764 | `"HERMES_GEMINI_CLIENT_SECRET": {...}` | ❌ Kein SIDEKICK_GEMINI_CLIENT_SECRET |
| A22 | `cli/config.py` | 1772 | `"HERMES_GEMINI_PROJECT_ID": {...}` | ❌ Kein SIDEKICK_GEMINI_PROJECT_ID |
| A23 | `cli/config.py` | 2135 | `"HERMES_LANGFUSE_PUBLIC_KEY": {...}` | ❌ Kein SIDEKICK_LANGFUSE_PUBLIC_KEY |
| A24 | `cli/config.py` | 2142 | `"HERMES_LANGFUSE_SECRET_KEY": {...}` | ❌ Kein SIDEKICK_LANGFUSE_SECRET_KEY |
| A25 | `cli/config.py` | 2149 | `"HERMES_LANGFUSE_BASE_URL": {...}` | ❌ Kein SIDEKICK_LANGFUSE_BASE_URL |
| A26 | `cli/config.py` | 2530 | `"HERMES_MAX_ITERATIONS": {...}` | ❌ Kein SIDEKICK_MAX_ITERATIONS |
| A27 | `cli/config.py` | 2540 | `"HERMES_TOOL_PROGRESS": {...}` (deprecated) | ❌ Kein SIDEKICK_TOOL_PROGRESS |
| A28 | `cli/config.py` | 2547 | `"HERMES_TOOL_PROGRESS_MODE": {...}` (deprecated) | ❌ Kein SIDEKICK_TOOL_PROGRESS_MODE |
| A29 | `cli/config.py` | 2554 | `"HERMES_PREFILL_MESSAGES_FILE": {...}` | ❌ Kein SIDEKICK_PREFILL_MESSAGES_FILE |
| A30 | `cli/config.py` | 2561 | `"HERMES_EPHEMERAL_SYSTEM_PROMPT": {...}` | ❌ Kein SIDEKICK_EPHEMERAL_SYSTEM_PROMPT |
| A31 | `cli/config.py` | 3263 | `get_env_value("HERMES_TOOL_PROGRESS")` | ❌ Migrationscode liest nur HERMES_ |
| A32 | `cli/config.py` | 3264 | `get_env_value("HERMES_TOOL_PROGRESS_MODE")` | ❌ Migrationscode liest nur HERMES_ |
| A33 | `shared/agent_bridge.py` | 25 | `env.setdefault("HERMES_YOLO_MODE", "1")` | ❌ Kein SIDEKICK_YOLO_MODE setdefault |
| A34 | `shared/agent_bridge.py` | 26 | `env.setdefault("HERMES_ACCEPT_HOOKS", "1")` | ❌ Kein SIDEKICK_ACCEPT_HOOKS setdefault |
| A35 | `cli/doctor.py` | 438 | `os.environ.setdefault("HERMES_INTERACTIVE", "1")` | ❌ Kein SIDEKICK_INTERACTIVE setdefault |
| A36 | `cli/doctor.py` | 1506 | `_HERMES_USER_AGENT` als User-Agent | ❌ User-Agent: `hermes-cli/...` |
| A37 | `cli/model_catalog.py` | 71 | `_HERMES_USER_AGENT = f"hermes-cli/..."` | ❌ User-Agent sollte `sidekick-cli/...` |
| A38 | `cli/models.py` | 23 | `_HERMES_USER_AGENT = f"hermes-cli/..."` | ❌ User-Agent sollte `sidekick-cli/...` |

### Kategorie B: User-facing Strings (HOCH)

| # | Datei | Zeile | Text | Status |
|---|-------|-------|------|--------|
| B1 | `cli/cli.py` | 2142 | `"⚕ NOUS HERMES - AI Agent Framework"` | ❌ Banner zeigt "NOUS HERMES" |
| B2 | `cli/cli.py` | 2143 | `"⚕ NOUS HERMES"` | ❌ Tiny banner line |
| B3 | `cli/auth.py` | 1844 | `"Set HERMES_SPOTIFY_CLIENT_ID or pass --client-id."` | ❌ Error message |
| B4 | `cli/auth.py` | 2302 | `"Saved HERMES_SPOTIFY_CLIENT_ID to ~/.sidekick/.env"` | ❌ Console output |
| B5 | `cli/auth.py` | 2312 | `# with "HERMES_SPOTIFY_CLIENT_ID is required"` | ❌ Kommentar |
| B6 | `cli/auth.py` | 777 | `"Set HERMES_HOME to a tmp_path..."` | ❌ Error message |
| B7 | `cli/auth.py` | 2887 | `f"... Set HERMES_SHARED_AUTH_DIR to a tmp_path..."` | ❌ Error message |
| B8 | `cli/auth.py` | 4137 | `"Install ... or set HERMES_COPILOT_ACP_COMMAND..."` | ❌ Error message |
| B9 | `cli/cli.py` | 283 | Docstring `HERMES_IGNORE_USER_CONFIG` | ❌ Docstring |
| B10 | `cli/cli.py` | 289 | Kommentar `{HERMES_HOME}/config.yaml` | ❌ Kommentar |
| B11 | `cli/cli.py` | 2101 | Kommentar `HERMES-AGENT logo` | ❌ Kommentar |
| B12 | `cli/cli.py` | 2574 | Kommentar `for this HERMES_HOME` | ❌ Kommentar |
| B13 | `cli/cli.py` | 13044 | Docstring `HERMES_SIGTERM_GRACE` | ❌ Docstring |
| B14 | `cli/config.py` | 185-186 | Docstring `HERMES_MANAGED`, `HERMES_HOME` | ❌ Docstring |
| B15 | `cli/config.py` | 216 | `f"(HERMES_MANAGED={env_hint})"` | ❌ Error message |
| B16 | `cli/config.py` | 225 | `f"(HERMES_MANAGED={env_hint})"` | ❌ Error message |
| B17 | `cli/config.py` | 245 | Docstring `HERMES_HOME/.container-mode` | ❌ Docstring |
| B18 | `cli/config.py` | 316-318 | Docstring `HERMES_HOME_MODE` | ❌ Docstring |
| B19 | `cli/config.py` | 367 | Docstring `HERMES_SKIP_CHMOD` | ❌ Docstring |
| B20 | `cli/config.py` | 379 | Docstring `HERMES_HOME` | ❌ Docstring |
| B21 | `cli/config.py` | 415 | `f"HERMES_HOME {home} does not exist."` | ❌ Error message |
| B22 | `cli/config.py` | 900 | Kommentar `HERMES_TUI_RESUME=<id>` | ❌ Kommentar |
| B23 | `cli/config.py` | 1131 | Kommentar `${HERMES_SKILL_DIR}` und `${HERMES_SESSION_ID}` | ❌ Kommentar |
| B24 | `cli/config.py` | 1286 | Kommentar `HERMES_TUI_NO_CONFIRM=1` | ❌ Kommentar |
| B25 | `cli/config.py` | 1306 | Kommentar `HERMES_ACCEPT_HOOKS=1` | ❌ Kommentar |
| B26 | `cli/config.py` | 1337 | Kommentar `HERMES_CRON_MAX_PARALLEL` | ❌ Kommentar |
| B27 | `cli/config.py` | 1451-1454 | Kommentare `HERMES_HOME` | ❌ Kommentare |
| B28 | `cli/config.py` | 3267 | `"(from HERMES_TOOL_PROGRESS=false)"` | ❌ Console output |
| B29 | `cli/config.py` | 3270 | `"(from HERMES_TOOL_PROGRESS_MODE)"` | ❌ Console output |
| B30 | `cli/config.py` | 3286 | `"(from HERMES_TIMEZONE)"` | ❌ Console output |
| B31 | `cli/config.py` | 3532 | Kommentar `$HERMES_HOME/plugins/` | ❌ Kommentar |
| B32 | `cli/config.py` | 4068 | Docstring `HERMES_HOME` | ❌ Docstring |
| B33 | `cli/banner.py` | 80 | `HERMES_AGENT_LOGO = SIDEKICK_LOGO` | ⚠️ Aliasname, Wert ist korrekt |
| B34 | `cli/banner.py` | 81 | `HERMES_CADUCEUS = "..."` | ⚠️ Aliasname, Wert ist Kunst |
| B35 | `cli/banner.py` | 185-195 | Docstring/Kommentare `HERMES_REVISION` | ❌ Docstring |
| B36 | `cli/banner.py` | 214 | Kommentar `$HERMES_HOME/hermes-agent/` | ❌ Kommentar |
| B37 | `cli/banner.py` | 235 | Kommentar `$HERMES_HOME/hermes-agent/` | ❌ Kommentar |
| B38 | `cli/backup.py` | 8 | Docstring `HERMES_HOME root.` | ❌ Docstring |
| B39 | `cli/backup.py` | 470 | Kommentar `relative to HERMES_HOME` | ❌ Kommentar |
| B40 | `cli/backup.py` | 831-834 | Docstring `HERMES_HOME` | ❌ Docstring |
| B41 | `cli/backup.py` | 903 | Docstring `HERMES_HOME` | ❌ Docstring |
| B42 | `cli/default_soul.py` | 1 | Docstring `HERMES_HOME` | ❌ Docstring |
| B43 | `cli/tips.py` | 441-445 | Tip-Einträge `HERMES_*` | ❌ User-facing tips |
| B44 | `cli/main.py` | 152 | Kommentar `systemd hardcodes HERMES_HOME=/root/.hermes` | ❌ Kommentar |
| B45 | `cli/main.py` | 692 | Docstring `cli_args: ... after 'hermes'` | ❌ Docstring |
| B46 | `cli/main.py` | 8366-8384 | Kommentare `hermes.service` | ❌ Kommentare |
| B47 | `cli/main.py` | 9512 | `"(e.g. hermes.service) left over from older installs."` | ❌ Help-Text |
| B48 | `cli/kanban_db.py` | 30-50 | Docstrings `HERMES_KANBAN_*` env vars | ❌ Docstrings (Code liest SIDEKICK_ + HERMES_) |
| B49 | `cli/kanban_db.py` | 912 | Docstring `HERMES_KANBAN_DB` / `HERMES_KANBAN_BOARD` | ❌ Docstring |
| B50 | `cli/kanban_db.py` | 3890 | Docstring `_resolve_hermes_bin` | ❌ Docstring / Funktionsname |
| B51 | `cli/kanban_db.py` | 3896 | `hermes_bin = shutil.which("hermes")` | ⚠️ Sucht nach binary "hermes" (backward compat) |
| B52 | `cli/kanban_db.py` | 3944 | Kommentar `back to Path.home() / ".hermes"` | ❌ Kommentar |
| B53 | `cli/kanban_db.py` | 3958-3959 | `env["HERMES_KANBAN_TASK"]` / `env["HERMES_KANBAN_WORKSPACE"]` | ❌ Schreibt HERMES_ (backward compat?) |
| B54 | `cli/gateway.py` | 707 | Docstring `falls back to /root/.hermes` | ❌ Docstring |
| B55 | `cli/gateway.py` | 2062 | Docstring `/root/.hermes/hermes-agent` | ❌ Docstring |
| B56 | `cli/gateway.py` | 2087-2093 | Docstring/Kommentare `.hermes` | ❌ Kommentare |
| B57 | `cli/relaunch.py` | 85 | Docstring `shutil.which("hermes")` | ❌ Docstring |
| B58 | `cli/relaunch.py` | 117 | `path_bin = shutil.which("hermes")` | ⚠️ Sucht binary "hermes" |
| B59 | `cli/uninstall.py` | 68 | `'# hermes-agent' in line` | ❌ String matching |
| B60 | `cli/uninstall.py` | 71 | `'hermes' in line.lower()` | ❌ String matching |
| B61 | `cli/uninstall.py` | 77 | `'hermes' in line.lower()` | ❌ String matching |
| B62 | `cli/uninstall.py` | 102 | `Path.home() / ".local" / "bin" / "hermes"` | ❌ Pfad für binary |
| B63 | `cli/uninstall.py` | 272-274 | Kommentare `hermes` | ❌ Kommentare |
| B64 | `cli/stdio.py` | 230-236 | `os.path.join(local_appdata, "hermes", ...)` | ⚠️ Windows Install-Pfade (legacy) |
| B65 | `cli/providers.py` | 221 | `source: str = "" # "hermes", ...` | ❌ Kommentar source = "hermes" |
| B66 | `cli/providers.py` | 453 | `source="hermes"` | ❌ Source-Identifier |
| B67 | `cli/model_switch.py` | 1383 | `"source": "hermes"` | ❌ Source-Identifier |
| B68 | `runtime/gateway/run.py` | 996 | Docstring `shutil.which("hermes")` | ❌ Docstring |
| B69 | `runtime/gateway/run.py` | 1004 | `hermes_bin = shutil.which("hermes")` | ⚠️ Sucht binary "hermes" |
| B70 | `runtime/gateway/run.py` | 12309 | `t("gateway.update.hermes_cmd_not_found")` | ❌ Translation key |
| B71 | `web/api/dispatcher.py` | 224 | `hermes_bin = shutil.which("hermes")` | ⚠️ Sucht binary "hermes" |
| B72 | `web/api/routes.py` | 6977 | `["hermes", "profile", "create", ...]` | ❌ Ruft CLI als "hermes" auf |
| B73 | `toolsets.py` | 31 | `_HERMES_CORE_TOOLS = [` | ⚠️ Variablenname (nicht user-facing) |
| B74 | `toolsets.py` | 234 | Kommentar `HERMES_KANBAN_TASK env` | ❌ Kommentar |
| B75 | `runtime/prompt_builder.py` | 89 | `_HERMES_MD_NAMES = (".hermes.md", "HERMES.md")` | ❌ Feature file names enthalten "hermes" |
| B76 | `runtime/prompt_builder.py` | 144 | `HERMES_AGENT_HELP_GUIDANCE = (...)` | ❌ Variablenname (Wert ist korrekt: Sidekick) |
| B77 | `run_agent.py` | 1973 | `_init_kwargs["agent_workspace"] = "hermes"` | ❌ Identifier "hermes" |
| B78 | `run_agent.py` | 9411 | `"sessionId": self.session_id or "hermes"` | ❌ Default sessionId "hermes" |
| B79 | `cli/kanban.py` | 176 | Kommentar `HERMES_KANBAN_BOARD env var` | ❌ Kommentar |
| B80 | `cli/kanban.py` | 185 | `"HERMES_KANBAN_BOARD env var)"` | ❌ Error message |
| B81 | `cli/kanban.py` | 650 | Kommentar `HERMES_KANBAN_BOARD` | ❌ Kommentar |
| B82 | `cli/kanban.py` | 662 | `os.environ.pop("HERMES_KANBAN_BOARD", None)` | ⚠️ backward compat (Zeile 665: `# backward compat`) |
| B83 | `cli/kanban.py` | 665 | `os.environ["HERMES_KANBAN_BOARD"] = prev_board_env # backward compat` | ✅ backward compat |
| B84 | `cli/kanban.py` | 685 | `os.environ["HERMES_KANBAN_BOARD"] = normed # backward compat` | ✅ backward compat |

### Kategorie C: Dateinamen mit 'hermes'
Keine Dateien mit 'hermes' im Namen gefunden. ✅

### Kategorie D: Config-Pfade (.hermes als Default)
Alle `Path.home() / ".hermes"` Referenzen sind backward-compat (Fallback für Legacy-Nutzer). Der kanonische Default ist `~/.sidekick`.
Keine neuen/nicht-backward-compat Pfade gefunden. ✅

Hinweis: Über 50 Stellen verwenden `.hermes` als Fallback-Pfad (in `shared/constants.py`, `shared/runtime.py`, `tools/environments/*.py`, `web/api/*.py`, `cli/*.py`). Dies ist intentional als Legacy-Kompatibilität.

### Kategorie E: Service-Namen

| # | Datei | Zeile | Text | Status |
|---|-------|-------|------|--------|
| E1 | `cli/gateway.py` | 2063 | `/opt/hermes` in Docstring | ❌ Pfad /opt/hermes |
| E2 | `cli/gateway.py` | 3094 | `/opt/hermes/docker/entrypoint.sh` in Error message | ❌ Pfad in user-facing message |
| E3 | `cli/main.py` | 8366-8384 | `hermes.service` in Kommentaren | ❌ Service name |
| E4 | `cli/main.py` | 9512 | `hermes.service` in help text | ❌ Service name |
| E5 | `cli/kanban_db.py` | 41 | Docstring `/opt/hermes` | ❌ Pfad |
| E6 | `tools/browser_tool.py` | 3498 | Docstring `/opt/hermes/.playwright` | ❌ Pfad |

### Kategorie F: URLs
Keine `hermes-agent.nousresearch.com` oder `hermes-agent.sh` URLs gefunden. ✅

## False Positives (übersprungen aber erwähnenswert)

### Env-Vars mit dual-read (SIDEKICK_ + HERMES_)
Diese Stellen lesen/setzen beide Varianten und sind korrekt migriert:
- `cli/auth.py:743` — `os.getenv("SIDEKICK_OAUTH_TRACE") or os.getenv("HERMES_OAUTH_TRACE")` ✅
- `cli/auth.py:1703` — `SIDEKICK_QWEN_BASE_URL or HERMES_QWEN_BASE_URL` ✅
- `cli/auth.py:2648` — `SIDEKICK_CODEX_REFRESH_TIMEOUT_SECONDS or HERMES_...` ✅
- `cli/auth.py:2669` — `SIDEKICK_CODEX_BASE_URL or HERMES_CODEX_BASE_URL` ✅
- `cli/auth.py:2860` — `SIDEKICK_SHARED_AUTH_DIR or HERMES_SHARED_AUTH_DIR` ✅
- `cli/auth.py:4013,4017,4127,4131` — `SIDEKICK_COPILOT_ACP_* or HERMES_COPILOT_ACP_*` ✅
- `cli/auth.py:4538,4693` — `SIDEKICK_CODEX_BASE_URL or HERMES_CODEX_BASE_URL` ✅
- `cli/cli.py:295` — `SIDEKICK_IGNORE_USER_CONFIG or HERMES_IGNORE_USER_CONFIG` ✅
- `cli/cli.py:2460-2462` — `SIDEKICK_MAX_ITERATIONS or HERMES_MAX_ITERATIONS` ✅
- `cli/cli.py:2494` — `SIDEKICK_IGNORE_RULES or HERMES_IGNORE_RULES` ✅
- `cli/cli.py:2498` — `SIDEKICK_EPHEMERAL_SYSTEM_PROMPT or HERMES_EPHEMERAL_SYSTEM_PROMPT` ✅
- `cli/cli.py:8385` — `SIDEKICK_YOLO_MODE or HERMES_YOLO_MODE` ✅
- `cli/cli.py:11055` — `SIDEKICK_REDACT_SECRETS or HERMES_REDACT_SECRETS` ✅
- `cli/cli.py:13069` — `SIDEKICK_SIGTERM_GRACE or HERMES_SIGTERM_GRACE` ✅
- `cli/config.py:169,210` — `SIDEKICK_MANAGED or HERMES_MANAGED` ✅
- `cli/config.py:255` — `SIDEKICK_DEV or HERMES_DEV` ✅
- `cli/config.py:325` — `SIDEKICK_HOME_MODE or HERMES_HOME_MODE` ✅
- `cli/config.py:344` — `SIDEKICK_CONTAINER or HERMES_CONTAINER / SIDEKICK_SKIP_CHMOD or HERMES_SKIP_CHMOD` ✅
- `cli/config.py:3283` — `SIDEKICK_TIMEZONE or HERMES_TIMEZONE` ✅
- `shared/constants.py:21` — `SIDEKICK_HOME or HERMES_HOME` ✅
- `shared/constants.py:57` — `SIDEKICK_OPTIONAL_SKILLS or HERMES_OPTIONAL_SKILLS` ✅
- `shared/constants.py:75` — `SIDEKICK_HOME or HERMES_HOME` ✅
- `shared/paths.py:7,9` — `LEGACY_HOME_ENV / LEGACY_STATE_DIR_ENV` ✅ (legacy constants)
- `shared/runtime.py:10-14` — Tuple-Paare `(SIDEKICK_*, HERMES_*)` ✅
- `run_agent.py:3348` — `SIDEKICK_API_TIMEOUT or HERMES_API_TIMEOUT` ✅
- `run_agent.py:3368` — `SIDEKICK_API_CALL_STALE_TIMEOUT or HERMES_...` ✅
- `run_agent.py:6920-6921` — dual-read ✅
- `run_agent.py:7674` — dual-read ✅
- `run_agent.py:7681` — dual-read ✅
- `run_agent.py:8032` — dual-read ✅
- `run_agent.py:8279` — dual-read ✅
- `run_agent.py:15074` — dual-read ✅
- `cli/kanban_db.py:161,204,295,317` — dual-read ✅
- `cli/kanban.py:655,1526,1528` — dual-read ✅

### backward compat Marker
- `cli/cli.py:51` — `os.environ["HERMES_QUIET"] = "1"  # backward compat` ✅
- `cli/cli.py:617` — `os.environ["HERMES_REDACT_SECRETS"] = ...  # backward compat` ✅
- `cli/cli.py:13310` — `os.environ["HERMES_INTERACTIVE"] = "1"  # backward compat` ✅
- `run_agent.py:1833` — `os.environ["HERMES_SESSION_ID"] = ...  # backward compat` ✅
- `run_agent.py:10152` — `os.environ["HERMES_SESSION_ID"] = ...  # backward compat` ✅
- `cli/kanban.py:665,685` — `# backward compat` ✅

### session_context.py docstrings
Keine gefunden.

### shim_constants
- `runtime/_compat/shim_constants_v1.py` und `v2.py` — shim layer, intentional backward compat ✅

### Methodennamen / Model-Namen detection
- `is_nous_hermes_non_agentic` — Funktionsname, übersprungen ✅
- `"hermes" in name.lower()` in `cli/auth.py:3249` — Model detection ✅
- `cli/model_switch.py:61-63` — Kommentar über `"hermes" in name.lower()` ✅

### URLs zum upstream repo
- `github.com/nousresearch/hermes-agent` — nicht gefunden ✅
- `hermes-agent.nousresearch.com` — nicht gefunden ✅

### pyproject.toml
- `zeile 41: hermes = "cli.main:main"` — backward compat console_scripts entry ✅

## Fazit

### Ist der Rebrand vollständig?
**Nein.** Es gibt mehrere Kategorien von unvollständig migrierten Stellen:

### Was fehlt für Vollständigkeit:

1. **KRITISCH — Spotify Env-Vars**: `cli/auth.py` verwendet durchgängig `HERMES_SPOTIFY_*` ohne `SIDEKICK_SPOTIFY_*` Fallback. Dies betrifft 6 Stellen (A1-A6).

2. **KRITISCH — Config-Metadaten**: `cli/config.py` definiert ~12 Env-Var-Namen in `ENV_VAR_METADATA` nur als `HERMES_*` ohne `SIDEKICK_*` Äquivalente (A19-A30). Hiervon sind `HERMES_LANGFUSE_*`, `HERMES_QWEN_BASE_URL`, `HERMES_GEMINI_*`, `HERMES_MAX_ITERATIONS`, `HERMES_PREFILL_MESSAGES_FILE`, `HERMES_EPHEMERAL_SYSTEM_PROMPT` betroffen.

3. **KRITISCH — Portal/CA Bundle**: `cli/auth.py` liest `HERMES_PORTAL_BASE_URL` und `HERMES_CA_BUNDLE` ohne SIDEKICK_-Fallback (A7-A11).

4. **KRITISCH — User-Agent**: `_HERMES_USER_AGENT = f"hermes-cli/{version}"` in `cli/models.py:23` und `cli/model_catalog.py:71`. Wird als HTTP User-Agent verwendet → API-Anbieter sehen "hermes-cli" statt "sidekick-cli".

5. **KRITISCH — Banner**: `cli/cli.py:2142-2143` zeigt "⚕ NOUS HERMES - AI Agent Framework" und "⚕ NOUS HERMES" im Startup-Banner.

6. **HOCH — `shutil.which("hermes")`**: In `cli/kanban_db.py:3896`, `runtime/gateway/run.py:1004`, `web/api/dispatcher.py:224`, `cli/relaunch.py:117`. Sucht nach binary "hermes" — falls kein `hermes`-Symlink existiert, schlägt die Suche fehl.

7. **HOCH — `"hermes"` als identifier/source**: `run_agent.py:1973` (`agent_workspace = "hermes"`), `run_agent.py:9411` (`sessionId: "hermes"`), `cli/providers.py:453` (`source="hermes"`), `cli/model_switch.py:1383` (`source: "hermes"`).

8. **HOCH — CLI invocation**: `web/api/routes.py:6977` ruft `["hermes", "profile", "create", ...]` auf — sollte `sidekick` sein.

9. **HOCH — Docstrings/Kommentare**: Über 30 Stellen, die noch "HERMES_HOME", "HERMES_*", "hermes" in Docstrings und Kommentaren erwähnen. Niedrige Priorität, da sie die Funktionalität nicht beeinträchtigen.

10. **HOCH — Agent Bridge**: `shared/agent_bridge.py:25-26` setzt `HERMES_YOLO_MODE` und `HERMES_ACCEPT_HOOKS` ohne SIDEKICK_-Gegenstück.

### Empfohlene Priorisierung:
1. **Sofort**: Spotify-Env-Vars (A1-A6) — neuer Code, kein dual-read
2. **Sofort**: Config-Metadaten (A19-A30) — Setup-Wizard zeigt nur HERMES_-Namen
3. **Sofort**: Portal/CA-Bundle (A7-A11) — könnte in neuen Deployments brechen
4. **Hoch**: User-Agent (A36-A38) — API-Provider sehen "hermes-cli"
5. **Hoch**: Banner (B1-B2) — neuer User sieht "NOUS HERMES"
6. **Hoch**: binary lookup (B51, B58, B69, B71) — `shutil.which("hermes")`
7. **Medium**: Docstrings/Kommentare — kosmetisch

### Bereits korrekt migriert:
- Die meisten Env-Vars haben korrekten dual-read (`SIDEKICK_*` > `HERMES_*`)
- `shared/paths.py` definiert `SIDEKICK_HOME_ENV` und `LEGACY_HOME_ENV = "HERMES_HOME"`
- `shared/runtime.py` hat Tuple-Paare `(SIDEKICK_*, HERMES_*)` für WebUI-Vars
- Kanban env vars haben alle dual-read
- Alle `os.environ["HERMES_*"] = ...` writes haben `# backward compat` Marker
- `pyproject.toml` hat beide console_scripts `sidekick` und `hermes`
- Default home ist `~/.sidekick` (nicht mehr `~/.hermes`)
