# Sidekick-/Nova-/Sidekick-Audit — v0.1.0-monorepo

Stand: Commit `ddd8208` (Tag `v0.1.0-monorepo`)
Datum: 2026-06-07

## Kategorien

| Label | Bedeutung |
|-------|-----------|
| ✅ allowed legacy compat | Bewusst erhaltener Legacy-Pfad für Übergangszeit |
| ✅ provenance/legal | Attribution/Obligation gegenüber ursprünglichem Projekt |
| ⚠️ should rename | Kosmetisch, aber für Konsistenz umbenennenswert |
| ❌ blocking functional | Echte funktionale Abhängigkeit — muss vor Ablösung |

---

## 1. User-facing Texte

| Fundort | Referenz | Einordnung |
|---------|----------|------------|
| `sidekick --help` | `SIDEKICK_INFERENCE_MODEL`, `SIDEKICK_INFERENCE_PROVIDER`, `SIDEKICK_ACCEPT_HOOKS` | ✅ allowed legacy compat — CLI hilft Nutzern mit Legacy-Env-Vars |
| `sidekick --help` | Subcommand-Beschreibung "Back up Sidekick home directory" | ⚠️ should rename — Kosmetik, Backup-Beschreibung |
| `sidekick --help` | Subcommand "profile" Beschreibung "multiple isolated Sidekick instances" | ⚠️ should rename |
| `sidekick --help` | Subcommand "logs" Beschreibung "View and filter Sidekick log files" | ⚠️ should rename |
| `sidekick --help` | Subcommand "mcp" Beschreibung "run Sidekick as an MCP server" | ⚠️ should rename |
| `web/static/index.html` | JS-Variablen `sidekick-theme`, `sidekick-skin`, `sidekick-font-size`, `sidekick-webui-*` | ✅ allowed legacy compat — localStorage-Keys, Rückwärtskompatibel |
| `web/static/index.html` | CSS-Klasse `sidekick-action-grid`, `sidekickSessionMeta` | ✅ allowed legacy compat — CSS/IDs, Seiteneffektfrei |
| `README.md` | `cids-sidekick-agent` / `cids-sidekick-webui` Erwähnung | ✅ provenance/legal — Migration Source Attribution |
| `README.md` | `SIDEKICK_HOME` Legacy-Erwähnung | ✅ allowed legacy compat — Dokumentation der Kompatibilität |

**Urteil:** Alle user-facing Texte sind entweder dokumentierte Legacy-Kompatibilität oder kosmetisch. **Kein Blocker.**

---

## 2. Runtime-/Import-Verknüpfungen

| Fundort | Referenz | Einordnung |
|---------|----------|------------|
| `sidekick_cli/__init__.py` | Forwarder zu `cli.*` (74 Submodule) | ✅ allowed legacy compat — Transition Layer |
| `runtime/account_usage.py` | `from cli.auth` / `from cli.runtime_provider` | ✅ gefixt in diesem Release (war sidekick_cli) |
| `runtime/auxiliary_client.py` | `import sidekick_cli as ...` | ✅ allowed legacy compat — Version aus Forwarder |
| `runtime/credential_pool.py` | `import cli.auth` / `from cli.config` | ✅ kanonisch — zeigt auf cli.* |
| `shared/runtime.py` | `cids-sidekick-agent` discovery candidate | ✅ gefixt in diesem Release |
| `web/api/config.py` | `_discover_agent_dir()` bevorzugt Monorepo | ✅ gefixt in diesem Release |
| `web/api/appstore.py` | Hardcodierte Pfade zu cids-sidekick-* | ✅ gefixt in diesem Release |
| `web/api/agents.py` | `HTTP-Referer` Header | ✅ gefixt in diesem Release |
| `runtime/_compat/*` | 8 Shim-Module | ✅ allowed legacy compat — Transition Layer |
| `tools/*.py` | >100 Importe auf `runtime.*`, `cli.*`, `shared.*` | ✅ kanonisch — 0 `from agent.` verbleibend |

**Urteil:** 4 echte Blocker identifiziert und in diesem Release gefixt. Keine weiteren funktionalen Abhängigkeiten zum alten Sidekick-Repo.

---

## 3. Env-/Config-Kompatibilität

| Variable | Kanonisch | Legacy-Alias | Status |
|----------|-----------|--------------|--------|
| `SIDEKICK_HOME` | ✅ `~/.sidekick` | `SIDEKICK_HOME` | ✅ |
| `SIDEKICK_STATE_DIR` | ✅ `~/.sidekick/state` | `SIDEKICK_STATE_DIR` | ✅ |
| `SIDEKICK_WEBUI_AGENT_DIR` | ✅ (neu) | `SIDEKICK_WEBUI_AGENT_DIR` | ✅ |
| `SIDEKICK_WEBUI_HOST/PORT` | ✅ | `SIDEKICK_WEBUI_HOST/PORT` | ✅ |
| `SIDEKICK_OPTIONAL_SKILLS` | ✅ | `SIDEKICK_OPTIONAL_SKILLS` | ✅ |
| `SIDEKICK_PREFER_IPV4` | ✅ | — | ✅ |
| `SIDEKICK_LANGUAGE` | ✅ | `SIDEKICK_LANGUAGE` | ✅ |
| `SIDEKICK_HOME` | — | `~/.sidekick`-Fallback | ✅ allowlist |
| `SIDEKICK_YOLO_MODE` | — | CLI-Toggle | ✅ allowlist |
| `SIDEKICK_ACCEPT_HOOKS` | — | CLI-Flag | ✅ allowlist |
| `SIDEKICK_QUIET` | — | Startup-Suppression | ✅ allowlist |

Home-Verzeichnis Fallback-Chain:
1. `SIDEKICK_HOME` env ✓
2. `SIDEKICK_HOME` env ✓
3. `~/.sidekick/` ✓
4. `~/.sidekick/` ✓ (Legacy-Fallback)

**Urteil:** Vollständig dokumentierte Dual-Support-Phase. `~/.sidekick` ist kanonisch, `~/.sidekick` nur Kompat-Fallback.

---

## 4. Command-/Package-Kompatibilität

| Aspekt | Status |
|--------|--------|
| `sidekick` CLI | ✅ Kanonischer Name |
| `sidekick` CLI | ✅ Alias in pyproject.toml `sidekick = sidekick_app.__main__:main` |
| `sidekick_cli` Python-Paket | ✅ Transition Layer → leitet an `cli.*` weiter |
| `sidekick_constants` | ✅ Re-export von `runtime._compat.shim_constants` |
| `sidekick_constants` | ✅ Re-export von `runtime._compat.shim_constants` |
| `sidekick_state` | ✅ Re-export von `runtime._compat.shim_state` |
| `sidekick_state` | ✅ Re-export von `runtime._compat.shim_state` |
| Produktive Logik unter Sidekick-Namen | ❌ Nicht mehr vorhanden |
| Produktive Logik exklusiv unter Sidekick-Namen | ✅ Ja |

**Urteil:** `sidekick`-Paketnamen sind reine Re-Export-Transition-Layer. Keine produktive Logik mehr exklusiv unter Sidekick.

---

## 5. WebUI/API

| Aspekt | Status |
|--------|--------|
| Split-Repo-Annahmen (hartkodiert) | ✅ 4 Funde gefixt |
| Split-Repo-Annahmen (Doku/Kommentare) | ⚠️ Kommentare erwähnen "sidekick-agent" (Doku-Kosmetik) |
| Session/State über Sidekick-Layer | ✅ shared.sessions ist kanonisch |
| Legacy-WebUI-Session-JSONs lesbar | ✅ gleicher Storage-Pfad |
| Keine externen Repo-Referenzen in API-Calls | ✅ |

**Urteil:** WebUI läuft komplett monorepo-intern.

---

## Gesamturteil

**Alles funktional über Sidekick/Nova.** Keine echten Sidekick-Blocker mehr.

Verbleibende ⚠️-Punkte (kosmetisch, für v0.2.0):

- CLI-Help-Texte erwähnen "Sidekick" in Subcommand-Beschreibungen
- WebUI-HTML verwendet `sidekick-*` localStorage-Keys und CSS-Klassen
- Einige API-Config-Kommentare referenzieren "sidekick-agent"
- `cli_backup/main.py` ist eine 1:1-Kopie des alten CLI (für Referenz)

---

## Empfehlungen für v0.2.0

1. CLI-Help-Texte: "Sidekick" durch "Sidekick" ersetzen (ca. 15 Stellen in `cli/main.py`)
2. WebUI: localStorage-Keys von `sidekick-*` nach `sidekick-*` migrieren (mit Read-Fallback)
3. `cli_backup/` entfernen (nach erfolgreichem QA)
4. `runtime/_compat/copy_*.py` Skripte entfernen (nicht mehr nötig)
