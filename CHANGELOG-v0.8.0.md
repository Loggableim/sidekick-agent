# v0.8.0 — Sidekick: Complete Sidekick Decoupling

**Das große Rebranding-Release.** Sidekick steht jetzt vollständig auf eigenen Beinen — alle `SIDEKICK_*`-Env-Vars, Pfade, URLs und User-Facing-Strings wurden auf `SIDEKICK_*` migriert. Plus ein massives Installer-Overhaul und der vollständige Runtime-Port.

---

## 🚀 Highlights

### Rebranding — Komplette Entkopplung von Sidekick
- **R10:** Dateinamen, Entrypoints, Docstrings auf Sidekick umgestellt
- **R9:** Alle User-facing Strings (CLI-Name, Approval-Patterns, Symlink-Pfade)
- **R8:** 80+ Dateien auf `SIDEKICK_*` Env-Vars mit Fallback zu `SIDEKICK_*` migriert
- **R6:** `auth.py` + `cli.py` Env-Var Migration
- **R4/R5:** Dual-Read/Write Pattern für 15 Kern-Dateien
- **Version harmonisiert:** `v0.8.0` als single source of truth (`sidekick_cli`)

### Runtime & Architecture
- **Full Runtime Port** — context_compressor + alle Shim-Fixes
- **Provider-System** — Vollständiges `providers/` Package mit 29 Provider-Plugins
- **Transport-Layer** — `ChatCompletionsTransport` + `build_kwargs`
- **Gateway** — Wiederherstellung + APIServerAdapter-Stub + `sidekick_cli.config`
- **OpenCode Go** — Routing, Transport, Chat-Kompression gefixt; `OPENCODE_GO_BASE_URL`-Bug beseitigt

### Installer (install.ps1) — Komplett überholt
- **irm|iex kompatibel** — kein `param()`-Block, ASCII-safe, keine Unicode-Box-Zeichen
- **UTF-8 BOM entfernt** — kein `﻿#`-Crash mehr
- **Venv-Pfad** korrekt, WorkingDirectory-Fixes
- **UAC Elevation** für Admin-Operationen
- **Desktop-Shortcuts** + WebUI-Health-Check + Dashboard-Auto-Öffnung
- **Progress-Bars** für Downloads (PortableGit, etc.)
- **CI/CD** — default branch main → master, CDN Cache Busting

### Bugfixes
- `run_agent`-Stub shadowed nicht mehr das echte Modul
- Pool-Credentials: `has_usable_secret()`-Check
- WebUI Dependencies: FastAPI + Uvicorn + atomic_yaml_write
- Gateway-Shims für kanban_db, debug, Launcher
- Installer: Fenster bleibt offen (Read-Host), Write-Host Strings, richtig quoten
- Provider: SPOTIFY, CA_BUNDLE, PORTAL_BASE_URL, INFERENCE_PROVIDER Audit-Fixes

---

**Full Changelog:** https://github.com/Loggableim/sidekick-agent/compare/v0.7.32...v0.8.0
