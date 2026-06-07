# Sidekick Repo — Consolidation & Current State

**Stand:** v0.5.0 (Tag `v0.5.0`, Commit `129322f`)
**Datum:** 2026-06-07

---

## Was ist Sidekick?

Sidekick ist ein **installierbares Assistant-Monorepo** mit drei First-Class-
Surfaces: **CLI**, **TUI** und **WebUI**. Dieses Repo ersetzt die historische
Trennung zwischen `cids-hermes-agent` (Agent-Runtime + CLI + Tools) und
`cids-hermes-webui` (WebUI-Server + Frontend).

## Aktuelle Struktur

```
sidekick/
├── cli/         CLI + TUI (77 Module — REPL, auth, config, setup)
├── runtime/     Agent-Runtime (94 Module — providers, cron, gateway)
├── tools/       101 Tool-Implementierungen (registry, file, browser, terminal)
├── web/         WebUI (48 API-Module + 113 Static-Assets)
├── shared/      Config, Paths, Sessions, Logging, Utils (10 Module)
├── sidekick_app/  Entrypoint + Legacy-Import-Bootstrap
├── sidekick_cli/  Legacy-Package-Forwarder (Transition Layer)
├── docs/        Releases, Roadmaps, Audits, Troubleshooting
└── tests/       Smoke-Tests (18 CLI + 7 WebUI HTTP)
```

## Wichtige Änderungen seit der Migration

| Alt (getrennt) | Neu (monorepo) |
|----------------|----------------|
| `cids-hermes-agent/agent/*.py` | `runtime/*.py` |
| `cids-hermes-agent/cron/` | `runtime/cron/` |
| `cids-hermes-agent/gateway/` | `runtime/gateway/` |
| `cids-hermes-agent/cli.py + sidekick_cli/` | `cli/` (77 Module) |
| `cids-hermes-webui/api/` | `web/api/` (48 Module) |
| `cids-hermes-webui/static/` | `web/static/` (113 Dateien) |

## Naming

| Begriff | Bedeutung |
|---------|-----------|
| **Sidekick** | Produktname (kanonisch) |
| **Nova** | Assistant-Name (Chat-Identität) |
| **hermes** | Legacy-CLI-Alias (`hermes` → `sidekick`) |
| **HERMES_\*** | Legacy-Env-Vars (read as fallback) |
| **~/.hermes** | Legacy-Home-Verzeichnis (Fallback) |
| **~/.sidekick** | Kanonisches Home-Verzeichnis |

## Legacy-Kompatibilität

- `HERMES_HOME` → wird als Fallback gelesen (nach `SIDEKICK_HOME`)
- `HERMES_*` Env-Vars → werden als Aliase akzeptiert
- `hermes` CLI → Alias auf `sidekick` Binary
- `sidekick_cli.*` Python-Imports → Forwarder auf `cli.*`
- `~/.hermes/` → Legacy-Sessions werden beim ersten Zugriff automatisch
  nach `~/.sidekick/state/` migriert

## Releases

| Version | Fokus | Tag |
|---------|-------|-----|
| v0.1.0-monorepo | Erste Baseline, alle Komponenten migriert | `v0.1.0-monorepo` |
| v0.2.0 | Rebrand: Hermes aus user-facing Texten entfernt | `v0.2.0` |
| v0.3.0 | Session Contract, Gateway-Warnings, CI/Smoke | `v0.3.0` |
| v0.4.0 | Error Handling, Doctor Exit-Codes, Troubleshooting | `v0.4.0` |
| v0.5.0 | Doctor --check-providers, macOS CI, Streaming | `v0.5.0` |

## Ausblick

Das Repo befindet sich im **Post-Migration-Zustand**. Keine weitere Migration
von Alt-Repos nötig. Nächste Schritte sind Dokumentation, Tests und
Produktqualität (siehe `docs/roadmap-v0.6.0.md`).
