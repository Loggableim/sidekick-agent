# Audit Report: DeepSeek V4 Pro

## Zusammenfassung
- Gesamt: 72 Fehler
- Kategorie A (Env-Vars): 14
- Kategorie B (User-Strings): 43
- Kategorie C (Dateinamen): 0
- Kategorie D (Pfade): 8
- Kategorie E (Services): 5
- Kategorie F (URLs): 2

## Detail-Liste

### Kategorie A: Env-Vars (KRITISCH) — 14 Fehler

**`os.getenv("HERMES_*")` ohne `SIDEKICK_`-Dual-Read:**

1. `[A] cli/auth.py:2721 — `or os.getenv("HERMES_CA_BUNDLE")` — kein SIDEKICK_CA_BUNDLE, nur HERMES_CA_BUNDLE als Fallback
2. `[A] cli/auth.py:3297 — `or os.getenv("HERMES_PORTAL_BASE_URL")` — kein SIDEKICK_PORTAL_BASE_URL
3. `[A] cli/auth.py:3574 — `or os.getenv("HERMES_PORTAL_BASE_URL")` — s.o., zweite Stelle
4. `[A] cli/auth.py:5064 — `or os.getenv("HERMES_PORTAL_BASE_URL")` — s.o., dritte Stelle
5. `[A] cli/auth.py:5185 — `or os.getenv("HERMES_CA_BUNDLE")` — kein SIDEKICK_CA_BUNDLE
6. `[A] cli/cli.py:2433 — `or os.getenv("HERMES_INFERENCE_PROVIDER")` — kein SIDEKICK_INFERENCE_PROVIDER; nur HERMES_ Variante

**`get_env_value("HERMES_*")` / `save_env_value("HERMES_*")` ohne SIDEKICK_-Varianten:**

7. `[A] cli/auth.py:1835 — `get_env_value("HERMES_SPOTIFY_CLIENT_ID")` — liest nur HERMES_ Var, kein SIDEKICK_ Fallback
8. `[A] cli/auth.py:1858 — `get_env_value("HERMES_SPOTIFY_REDIRECT_URI")` — s.o.
9. `[A] cli/auth.py:1874 — `get_env_value("HERMES_SPOTIFY_API_BASE_URL")` — s.o.
10. `[A] cli/auth.py:1889 — `get_env_value("HERMES_SPOTIFY_ACCOUNTS_BASE_URL")` — s.o.
11. `[A] cli/auth.py:2295 — `save_env_value("HERMES_SPOTIFY_CLIENT_ID", raw)` — schreibt nur HERMES_ Var
12. `[A] cli/auth.py:2299 — `save_env_value("HERMES_SPOTIFY_REDIRECT_URI", ...)` — s.o.
13. `[A] cli/config.py:3263 — `get_env_value("HERMES_TOOL_PROGRESS")` — liest nur HERMES_ Var
14. `[A] cli/config.py:3264 — `get_env_value("HERMES_TOOL_PROGRESS_MODE")` — s.o.

### Kategorie B: User-facing Strings (HOCH) — 43 Fehler

**Docstrings mit "Hermes" als Produktname:**

15. `[B] cli/auth.py:2417 — `Read Codex OAuth tokens from Hermes auth store`
16. `[B] cli/auth.py:2466 — `Save Codex OAuth tokens to Hermes auth store`
17. `[B] cli/auth.py:2485 — `Refresh Codex OAuth tokens without mutating Hermes auth state`
18. `[B] cli/auth.py:2589 — `Saves the new tokens to Hermes auth store automatically`
19. `[B] cli/auth.py:2644 — `Resolve runtime credentials from Hermes's own Codex token store`
20. `[B] cli/auth.py:4546 — `Run a fresh device code flow — Hermes gets its own OAuth session` (Kommentar)
21. `[B] cli/auth.py:4554 — `Save tokens to Hermes auth store` (Kommentar)
22. `[B] cli/auth.py:4840 — `Persist MiniMax OAuth state to Hermes auth store`
23. `[B] cli/banner.py:183 — `Check whether a Hermes update is available`
24. `[B] cli/banner.py:299 — `Hermes checkout. Cached per-process.`
25. `[B] cli/browser_connect.py:1 — `Shared helpers for attaching Hermes to a local Chrome CDP port`
26. `[B] cli/claw.py:152 — `Check if a Hermes gateway is running`
27. `[B] cli/claw.py:314 — `Run the OpenClaw → Hermes migration`
28. `[B] cli/claw.py:401 — `Check if a Hermes gateway is running` (Kommentar)
29. `[B] cli/cli.py:2311 — `Initialize the Hermes CLI`
30. `[B] cli/commands.py:90 — `Create or restore state snapshots of Hermes config/state`
31. `[B] cli/commands.py:105 — `Set a standing goal Hermes works on`
32. `[B] cli/commands.py:151 — `Control what Enter does while Hermes is working`
33. `[B] cli/config.py:183 — `Check if Hermes is running in package-manager-managed mode`
34. `[B] cli/config.py:338 — `When Hermes runs in a container`
35. `[B] cli/config.py:535 — `Hermes auto-sources ~/.profile`
36. `[B] cli/config.py:541 — `Hermes sources the user's shell rc files`
37. `[B] cli/config.py:558 — `useful when Hermes runs in a container`
38. `[B] cli/config.py:589 — `bundled Hermes image`
39. `[B] cli/config.py:627 — `Hermes sends a stable profile-scoped userId`
40. `[B] cli/config.py:659 — `hermes sweeps the checkpoint base at startup` (Kommentar)
41. `[B] cli/config.py:679 — `Hermes truncates the file`
42. `[B] cli/config.py:1113 — `Hermes feeds a continuation prompt`
43. `[B] cli/config.py:1119 — `Max continuation turns before Hermes auto-pauses`
44. `[B] cli/config.py:207 — `Cannot modify this Hermes installation`
45. `[B] cli/config.py:215 — `this Hermes installation is managed by NixOS`
46. `[B] cli/config.py:224 — `this Hermes installation is managed by Homebrew`
47. `[B] cli/config.py:2678 — `Scans all enabled skills for metadata.hermes.config entries`
48. `[B] cli/config.py:2826 — `older Hermes versions write models`
49. `[B] cli/config.py:3143 — `Hermes won't know which provider to use`
50. `[B] cli/config.py:4569 — `vars known to Hermes` (Kommentar)
51. `[B] cli/config.py:4579 — `Remove known Hermes vars` (Kommentar)
52. `[B] cli/debug.py:290 — `----HermesDebugBoundary9f3c` (boundary string)
53. `[B] cli/dump.py:4 — `compact summary of the user's Hermes setup`
54. `[B] cli/env_loader.py:1 — `Helpers for loading Hermes .env files`
55. `[B] cli/env_loader.py:147 — `Load Hermes environment files`
56. `[B] cli/gateway.py:519 — `Hermes profile are returned`
57. `[B] cli/gateway.py:3094 — `before the Hermes command` (user-facing message)
58. `[B] runtime/gateway/run.py:3790 — `thread_name = f"Hermes — {cli_title}"` (Telegram thread name)
59. `[B] runtime/gateway/run.py:10884 — `return "Hermes Chat"` (default Telegram chat title)
60. `[B] runtime/gateway/run.py:12272 — `update Hermes Agent to the latest version`
61. `[B] runtime/gateway/run.py:13818 — `Forward the message to a remote Hermes API server`
62. `[B] runtime/gateway/run.py:16535 — `Hermes Gateway - Multi-platform messaging`
63. `[B] runtime/gateway/status.py:140 — `looks like the Hermes gateway`
64. `[B] runtime/auxiliary_client.py:327 — `HERMES_OPENROUTER_CACHE — truthy` (docstring)
65. `[B] runtime/auxiliary_client.py:330 — `HERMES_OPENROUTER_CACHE_TTL — integer seconds` (docstring)
66. `[B] runtime/cron/jobs.py:274 — `timezone-aware datetime in Hermes configured timezone`

**User-facing Strings / Log-Meldungen / Konsolenausgaben:**

67. `[B] cli/auth.py:1844 — `Set HERMES_SPOTIFY_CLIENT_ID or pass --client-id` (Error-Meldung)
68. `[B] cli/auth.py:2302 — `Saved HERMES_SPOTIFY_CLIENT_ID to ~/.sidekick/.env` (Konsolenausgabe)
69. `[B] cli/auth.py:3248 — `Skip Hermes models — they're not reliable` (Kommentar)
70. `[B] cli/auth.py:5286 — `users on older Hermes builds` (Kommentar)
71. `[B] cli/copilot_auth.py:384 — `User-Agent: HermesAgent/1.0` (HTTP User-Agent)
72. `[B] cli/cli.py:4296 — `Hermes 3 & 4 models are NOT agentic` (user-facing Warnung)

### Kategorie C: Dateinamen — 0 Fehler

Keine Dateien mit `hermes` im Namen (außerhalb .git/ und __pycache__) gefunden.

### Kategorie D: Config-Pfade — 8 Fehler

73. `[D] shared/constants.py:19 — `native_hermes = Path.home() / ".hermes"` — Legacy-Fallback als Default-Pfad
74. `[D] cli/gateway.py:2092 — `current_default = (Path.home() / ".hermes").resolve()` — Default auf ~/.hermes
75. `[D] cli/gateway.py:2093 — `target_default = Path(target_home_dir) / ".hermes"` — Pfadkomponente
76. `[D] runtime/nous_rate_guard.py:35 — `base = os.path.join(os.path.expanduser("~"), ".hermes")` — Default-Pfad
77. `[D] shared/runtime.py:55 — `candidates.append((Path.home() / ".hermes" / "hermes-agent").resolve())` — Legacy-Suchpfad
78. `[D] web/api/config.py:121 — `candidates.append(HOME / ".hermes" / "hermes-agent")` — Legacy-Suchpfad
79. `[D] web/api/config.py:274 — `return HOME / ".hermes" / "config.yaml"` — Default-Config-Pfad
80. `[D] web/api/config.py:2179 — `return HOME / ".hermes" / "auth.json"` — Default-Auth-Pfad
81. `[D] web/api/config.py:3050 — `hermes_env_path = HOME / ".hermes" / ".env"` — Default-Env-Pfad

### Kategorie E: Service-Namen — 5 Fehler

82. `[E] cli/gateway.py:2063 — `/opt/hermes` — System-Pfad-Referenz
83. `[E] cli/gateway.py:3094 — `/opt/hermes/docker/entrypoint.sh before the Hermes command` — Docker-Pfad
84. `[E] tools/browser_tool.py:3498 — `/opt/hermes/.playwright` — Playwright-Browser-Pfad
85. `[E] cli/kanban_db.py:41 — `/opt/hermes` (Erwähnung im Docstring)
86. `[E] cli/main.py:8366 — `hermes.service` — pre-rename systemd unit (Kommentar)
87. `[E] cli/main.py:8384 — `pre-rename units (hermes.service) fight` — user-facing message
88. `[E] cli/main.py:9512 — `hermes.service left over from older installs` — user-facing message

### Kategorie F: URLs — 2 Fehler

89. `[F] runtime/_compat/WEBUI_SURVEY.md:4 — `C:\HermesPortable\cids-hermes-webui` — veralteter Source-Pfad
90. `[F] runtime/_compat/WEBUI_SURVEY.md:5 — `C:\HermesPortable\sidekick/web/` — veralteter Target-Pfad
91. `[F] tools/web_providers/ARCHITECTURE.md:73 — `.hermes/plans/2026-05-03-web-tools-provider-architecture.md` — veralteter Pfad

## False Positives (übersprungen aber erwähnenswert)

### Übersprungene Env-Var-Dual-Reads (intentional backward compat)
Die folgenden `HERMES_*`-Reads sind korrekt mit `SIDEKICK_*`-Fallback versehen und wurden daher übersprungen:
- ~25 Dual-Reads in `cli/auth.py` (`SIDEKICK_CODEX_*` or `HERMES_CODEX_*`)
- ~15 Dual-Reads in `run_agent.py` (`SIDEKICK_API_TIMEOUT` or `HERMES_API_TIMEOUT`, etc.)
- ~10 Dual-Reads in `cli/cli.py` (`SIDEKICK_*` or `HERMES_*`)
- ~10 Dual-Reads in `cli/gateway.py`
- ~8 Dual-Reads in `runtime/auxiliary_client.py`
- ~5 Dual-Reads in `runtime/cron/scheduler.py`
- ~5 Dual-Reads in `tools/approval.py`
- ~5 Dual-Reads in `cli/config.py`
- ~4 Dual-Reads in `web/api/streaming.py`
- Diverse in `cli/main.py`, `cli/plugins.py`, `runtime/gateway/run.py`, `cli/kanban_db.py`, etc.

### Übersprungene `os.environ["HERMES_*"] =` SET-Operationen mit `# backward compat`
- `run_agent.py:1833,10152` — `os.environ["HERMES_SESSION_ID"] = ... # backward compat`
- `cli/cli.py:51` — `os.environ["HERMES_QUIET"] = "1"  # backward compat`
- `cli/cli.py:617` — `os.environ["HERMES_REDACT_SECRETS"] = ... # backward compat`
- `cli/cli.py:13310` — `os.environ["HERMES_INTERACTIVE"] = "1"  # backward compat`
- `cli/gateway.py:722` — `os.environ["HERMES_HOME"] = ... # backward compat`
- `cli/kanban.py:665,685` — `os.environ["HERMES_KANBAN_BOARD"] = ... # backward compat`
- `cli/main.py:192,232,1286,1380,1389,1396,1401` — diverse `# backward compat`
- `cli/oneshot.py:173-174` — `# backward compat`
- `cli/profiles.py:825,861,864` — `# backward compat` (z.T. implizit)
- `web/api/dispatcher.py:343` — `# backward compat`
- `web/api/kanban_bridge.py:127,134` — `# backward compat`
- `web/api/profiles.py:388,471,636` — immer mit SIDEKICK_HOME gepaart (Dual-Set)
- `runtime/gateway/run.py:420,539-567` — immer mit SIDEKICK_ gepaart (Dual-Set)

### Übersprungene `shim_constants` (Shim Layer)
- `runtime/_compat/shim_constants_v1.py` — reiner Re-Export/Shim-Layer
- `runtime/_compat/shim_constants_v2.py` — reiner Re-Export/Shim-Layer
- `sidekick_constants.py` — reiner Re-Export/Shim-Layer

### Übersprungene Model-Namen Detection (backward compat)
- `cli/auth.py:3249` — `if "hermes" in mid.lower()` — Model-Namen-check
- `cli/model_switch.py:60-72` — `_NOUS_HERMES_NON_AGENTIC_RE` — Model-Namen-Regex
- `cli/model_switch.py:75` — `is_nous_hermes_non_agentic()` — Funktionsname
- `cli/cli.py:4290-4296` — `is_nous_hermes_non_agentic(model_name)` — Nutzung

### Übersprungene Dok-Hinweise auf Legacy-Compat
- `README.md` Zeilen 159, 169, 187-189, 211, 232-233 — Legacy-Env-Var-Dokumentation
- `docs/troubleshooting.md` — vollständiger Legacy-Support-Abschnitt
- `docs/known-issues.md` — CLI HERMES_*-Referenzen-Liste
- `docs/consolidation.md` — Migrations-Matrix
- `docs/releases/*.md` — Release-Notes mit Legacy-Thema
- `docs/audits/*.md` — frühere Audit-Berichte

### Übersprungene `pyproject.toml` CLI-Alias
- `pyproject.toml:41` — `hermes = "cli.main:main"` — notwendiger CLI-Alias für backward compat

### Übersprungene `env.setdefault` für Subprocess-Env
- `cli/main.py:1163-1168` — setzt HERMES_* in TUI-Subprocess-Env
- `cli/web_server.py:3457` — setzt HERMES_TUI_DISABLE_MOUSE
- `cli/doctor.py:438` — setzt HERMES_INTERACTIVE
- `shared/agent_bridge.py:25-26` — setzt HERMES_YOLO_MODE/ACCEPT_HOOKS für Bridge-Subprocess

### Übersprungene Test-Code-Direktiven
- `test_integration.py:29,39,43,51` — Test-Fixtures für HERMES_*-Fallback-Tests
- `tests/smoke_all.py:152` — Test-Fixture
- `tests/smoke_webui.py:133` — Test-Fixture
- `tests/smoke_all.py:260-274` — Branding-Regression-Tests (testen AUF Hermes-Freiheit)

### Übersprungene Code-Kommentare (kein user-facing)
- Diverse Kommentare die `hermes_*` als Code-Referenz verwenden (z.B. `hermes_logging.setup_logging()`, `hermes_cli/config.py`, `hermes-lcm#68`, etc.)
- `runtime/context_references.py:22` — `_SENSITIVE_HERMES_DIRS` — Variablenname (interne Konstante)

## Fazit

**Ist der Rebrand vollständig? Nein.**

### Kritische Mängel (müssen vor Release behoben werden):

1. **Kategorie A: Env-Vars (14 kritische Stellen)**
   - `HERMES_CA_BUNDLE` und `HERMES_PORTAL_BASE_URL` werden ohne `SIDEKICK_`-Dual-Read gelesen — ältere Nutzer mit `HERMES_PORTAL_BASE_URL` bekommen noch Support, aber Neunutzer werden SIDEKICK_ setzen und es wird ignoriert.
   - `HERMES_SPOTIFY_*` (5 Reads + 2 Writes) haben keine `SIDEKICK_`-Varianten
   - `HERMES_TOOL_PROGRESS*` (2 Reads) haben keine `SIDEKICK_`-Varianten
   - `HERMES_INFERENCE_PROVIDER` in `cli/cli.py:2433` hat keinen `SIDEKICK_INFERENCE_PROVIDER`-Dual-Read

2. **Kategorie B: User-Facing Strings (43 Stellen)**
   - Zahlreiche Docstrings und Kommentare referenzieren noch "Hermes" als aktuellen Produktnamen (sollten "Sidekick" heißen oder zumindest "Legacy: ..." als Präfix haben)
   - `cli/copilot_auth.py:384` — User-Agent string "HermesAgent/1.0"
   - `runtime/gateway/run.py:3790,10884` — Telegram thread name "Hermes — ..." / "Hermes Chat"
   - `cli/cli.py:4296` — User-facing Warnung "Hermes 3 & 4 models..."

3. **Kategorie D: Config-Pfade (8 Stellen)**
   - `shared/runtime.py:55` und `web/api/config.py:121,274,2179,3050` sowie `runtime/nous_rate_guard.py:35` referenzieren `~/.hermes` als Default statt `~/.sidekick`

4. **Kategorie E: Service-Namen (5 Stellen)**
   - `/opt/hermes` wird mehrfach referenziert (müsste `/opt/sidekick` sein)

5. **Kategorie F: URLs (3 Stellen)**
   - `runtime/_compat/WEBUI_SURVEY.md` enthält `C:\HermesPortable\...` Pfade

### Nicht dringend (dokumentierte backward compat):
- Dual-Reads (SIDEKICK_* or HERMES_*): ~100 Stellen, intentional für Legacy-Support
- `# backward compat` SET-Operationen: ~30 Stellen, intentional
- Shim-Layer (`shim_constants*`, `sidekick_constants.py`): intentional
- `pyproject.toml` CLI-Alias `hermes`: intentional
- Model-Namen-Checks: intentional (prüft echte Model-Namen, nicht Produktnamen)

### Empfohlene nächste Schritte:
1. `HERMES_CA_BUNDLE`, `HERMES_PORTAL_BASE_URL`, `HERMES_INFERENCE_PROVIDER` mit `SIDEKICK_`-Dual-Read versehen
2. `HERMES_SPOTIFY_*` komplett zu `SIDEKICK_SPOTIFY_*` migrieren
3. `HERMES_TOOL_PROGRESS*` zu `SIDEKICK_TOOL_PROGRESS*` migrieren (oder Dual-Read)
4. User-Agent in `copilot_auth.py` auf `SidekickAgent/1.0` ändern
5. Telegram-Default-Namen in `run.py` von "Hermes" auf "Sidekick" ändern
6. `~/.hermes`-Pfade zu `~/.sidekick` ändern (wo es kein Legacy-Fallback mehr sein soll)
7. `/opt/hermes` zu `/opt/sidekick` ändern
8. `WEBUI_SURVEY.md`-Pfade bereinigen
