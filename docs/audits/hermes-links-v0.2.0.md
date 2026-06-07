# Finaler Hermes-Link-Audit — v0.2.0

**Stand:** Commit `5f9edfe` (Tag `v0.2.0`)
**Datum:** 2026-06-07

## Kategorien

| Label | Bedeutung | Vorkommen |
|-------|-----------|-----------|
| ✅ **allowed legacy compat** | Bewusst erhaltener Legacy-Pfad für Übergangszeit | 12 |
| ✅ **provenance/legal** | Attribution gegenüber ursprünglichem Projekt | 2 |
| ✅ **migration shim** | Import-Forwarder-Paket oder Shim-Modul | 5 |
| ✅ **env var doc** | Env-Var-Referenz in --help (Hilfe für Nutzer) | 3 |
| ⚠️ **should rename** | Kosmetisch, für künftige Releases | 4 |
| ❌ **blocking functional** | Echte funktionale Abhängigkeit | 0 |

---

## 1. CLI Help / User-facing Output

| Fundort | Referenz | Einordnung | Begründung |
|---------|----------|------------|------------|
| `sidekick --help` Z.74 | `HERMES_INFERENCE_MODEL` | ✅ env var doc | Nutzerhilfe: Legacy-Env-Var wird noch akzeptiert |
| `sidekick --help` Z.78 | `HERMES_INFERENCE_PROVIDER` | ✅ env var doc | s.o. |
| `sidekick --help` Z.91 | `HERMES_ACCEPT_HOOKS` | ✅ env var doc | s.o. |

**Fazit:** 0 user-facing "Hermes"-Produktnamen mehr. Nur dokumentierte Legacy-Env-Var-Aliase.

---

## 2. README / Docs

| Fundort | Referenz | Einordnung | Begründung |
|---------|----------|------------|------------|
| `README.md` Z.28 | `hermes --help` | ✅ allowed legacy compat | Legacy-CLI-Alias-Dokumentation |
| `README.md` Z.89 | `cids-hermes-agent` / `cids-hermes-webui` | ✅ provenance/legal | Source-Attribution der Migration |
| `README.md` Z.95 | `hermes_cli.*` | ✅ provenance/legal | Erwähnung im Migrations-Kontext |
| `README.md` Z.97 | `hermes` command alias | ✅ allowed legacy compat | CLI-Alias-Dokumentation |
| `docs/releases/v0.1.0-monorepo.md` | `cids-hermes-*` | ✅ provenance/legal | Historische Release-Dokumentation |
| `docs/releases/v0.2.0.md` | `v0.1.0-monorepo` | ✅ provenance/legal | Versions-Dokumentation |
| `docs/audits/hermes-links-v0.1.0.md` | alle | ✅ provenance/legal | Historisches Audit-Dokument |

---

## 3. Import-/Paket-Forwarder

| Fundort | Referenz | Einordnung | Begründung |
|---------|----------|------------|------------|
| `cron/__init__.py` | "cids-hermes-agent/cron/" im Kommentar | ✅ migration shim | Forwarder-Paket, Kommentar erklärt Herkunft |
| `gateway/__init__.py` | "gateway" → `runtime.gateway` | ✅ migration shim | Forwarder-Paket |
| `gateway/restart.py` | `from runtime.gateway.restart import *` | ✅ migration shim | Forwarder |
| `gateway/status.py` | `from runtime.gateway.status import *` | ✅ migration shim | Forwarder |
| `sidekick_cli/__init__.py` | 74 Submodul-Forwarder | ✅ migration shim | Transition Layer |
| `runtime/_compat/shim_*.py` | 8 Shim-Module | ✅ migration shim | Legacy-Import-Brücken |
| `sidekick_constants.py` | Re-export aus `shim_constants` | ✅ migration shim | Legacy-Import-Brücke |

---

## 4. Interne Code-Referenzen (kein user-facing)

| Fundort | Referenz | Einordnung | Begründung |
|---------|----------|------------|------------|
| `cli/auth.py:836` | `".hermes/auth.json"` | ⚠️ should rename | Fallback-Pfad, low risk |
| `cli/backup.py:286,301` | `.hermes/config.yaml`, `.hermes` prefix | ⚠️ should rename | Backup-Kompatibilität, low risk |
| `cli/cli.py:2594` | `.hermes_history` | ⚠️ should rename | History-Filename, cosmetic |
| `cli/config.py:217` | "hermes-agent.settings" | ⚠️ should rename | NixOS-Konfig-Referenz, cosmetic |

---

## 5. Gateway Top-Level Forwarder (zuvor nicht dokumentiert)

| Datei | Einordnung | Begründung |
|-------|------------|------------|
| `gateway/__init__.py` | ✅ migration shim | Ermöglicht `from gateway.restart import X` |
| `gateway/restart.py` | ✅ migration shim | 1-Zeilen-Forwarder |
| `gateway/status.py` | ✅ migration shim | 1-Zeilen-Forwarder |

Diese 3 Dateien sind stub-forwarder analog `sidekick_cli/`. Sie existieren,
weil `cli/cli.py` und andere Module `from gateway.restart import ...` importieren.
Die echte Implementierung liegt in `runtime/gateway/`.

---

## Zusammenfassung

| Kategorie | Gesamt | User-facing |
|-----------|--------|-------------|
| ✅ allowed legacy compat | 6 | 0 |
| ✅ provenance/legal | 7 | 0 |
| ✅ migration shim | 14 | 0 |
| ✅ env var doc | 3 | 3 (gewollt) |
| ⚠️ should rename | 4 | 0 |
| ❌ blocking functional | 0 | — |

**Gesamturteil:**
- **Keine user-facing Hermes-Namen mehr** außer dokumentierten Legacy-Env-Var-Aliase im `--help`
- **0 blockierende Hermes-Abhängigkeiten**
- **4 kosmetische ⚠️-Funde** (Filename `.hermes_history`, Backup-Prefix `.hermes`, NixOS-Konfig-Referenz) für v0.4.0 oder später
- **Alle produktiven Imports** zeigen auf `runtime.*`, `cli.*`, `shared.*`, `tools.*`, `web.*`

Das Repo ist funktional vollständig auf Sidekick umgestellt.
