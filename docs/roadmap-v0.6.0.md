# Sidekick v0.6.0 — Roadmap: Vertrauen & Vorzeigbarkeit

**Basis:** `v0.5.0` (`129322f`)
**Leitbild:** *„Sidekick ist nicht nur installierbar und stabil — es ist vorzeigbar."*

---

## Wo stehen wir?

| Release | Fokus | Status |
|---------|-------|--------|
| v0.1.0 | Migration: alles in einem Repo | ✅ |
| v0.2.0 | Rebrand: kein user-facing Hermes mehr | ✅ |
| v0.3.0 | Session Contract, Gateway, CI | ✅ |
| v0.4.0 | Error Handling, Doctor, Troubleshooting | ✅ |
| v0.5.0 | Streaming, Diagnostics, macOS CI | ✅ |
| **v0.6.0** | **Vertrauen, Vorzeigbarkeit, Konsistenz** | **⏳** |

---

## Analyse: was fehlt noch?

### Dokumentation (🔴 Hoch)

| Lücke | Impact |
|-------|--------|
| Keine Architecture-Overview (wie hängen CLI/TUI/WebUI/Runtime zusammen) | Neuentwickler verstehen Struktur nicht |
| Consolidation.md veraltet (spricht von Migration) | Verwirrt Leser |
| Keine Config-Referenz mit allen Keys | Nutzer kennen nicht alle Einstellungen |

### Tests/CI (🟡 Mittel)

| Lücke | Impact |
|-------|--------|
| Windows CI inaktiv | Nutzer melden Windows-Probleme ungefiltert |
| Kein automatisierter Hermes-Branding-Regressionstest | Könnte bei Änderungen again auftauchen |
| Kein WebUI-Frontend-Test (JS-Logik) | Nur Python-Backend getestet |

### Produktqualität (🟢 Niedrig)

| Lücke | Impact |
|-------|--------|
| Kein Dashboard-Redesign | Funktional, aber nicht „sleek" |
| TUI nie interaktiv getestet | Nur Import-Smoke |
| Session-Layer-Divergenz dokumentiert | Verständlich, aber nicht ideal |

---

## Vorschlag für v0.6.0

### Nicht-Ziele

- ❌ Kein Dashboard-Redesign
- ❌ Kein WebSocket-Refactor
- ❌ Keine neuen Tools/Provider
- ❌ Kein LastBrowser
- ❌ Keine Runtime-Architekturänderung
- ❌ Kein Session-Modell-Merge (bleibt für später)

### A. Architecture-Overview (`docs/architecture.md`)

**Ziel:** Ein neuer Entwickler versteht in 5 Minuten die Repo-Struktur.

Inhalt:
- System-Architektur (CLI → shared.* → runtime.*, WebUI → web.api → runtime.*)
- Datenfluss: User Input → AIAgent.run_conversation() → tool execution → response
- Session-Lebenszyklus
- Config/State/Logs-Pfade
- Tools-Registrierung und Registry

**Aufwand:** 1h
**Datei:** `docs/architecture.md`

### B. Config-Referenz (`docs/config-reference.md`)

**Ziel:** Alle Config-Keys mit Defaults und Beschreibung.

Inhalt:
- Alle Keys aus `runtime/config.py` + `shared/config.py` + `cli/config.py`
- Env-Var-Override-Chain pro Key
- Legacy-Aliase

**Aufwand:** 1h
**Datei:** `docs/config-reference.md`

### C. Consolidation.md aktualisieren (oder ersetzen)

**Ziel:** Aktuelle Repo-Beschreibung statt alter Migrations-Doku.

Inhalt:
- Kurze Historie (was war cids-hermes-agent/webui)
- Aktuelle Struktur (Sidekick monorepo)
- Wohin die Reise geht (keine weitere Migration nötig)

**Aufwand:** 0.5h
**Datei:** `docs/consolidation.md`

### D. Windows CI vorbereiten

**Ziel:** Windows-CI in GitHub Actions aktivieren (subset).

Schritte:
- `windows-latest` zur Matrix hinzufügen
- Nur CLI smoke (`--help`, `--version`, `doctor`) + Import smoke
- Session-Tests (Pfad-Kompatibilität prüfen)
- Gateway-Import-Test auslassen (Linux-spezifisch)

**Aufwand:** 0.5h
**Risiko:** 🟡 Mittel — Shell-Kompatibilität, unbekannte Windows-Fehler

### E. Hermes-Branding-Regressionstest

**Ziel:** Automatisiertes Failen, wenn neuer user-facing Hermes-Text eingefügt wird.

Schritte:
- Grep-basierter Test in `tests/`: Durchsucht `cli/main.py`, `web/static/`, `README.md`
  nach bestimmten Hermes-Mustern
- Failt nur bei user-facing Texten (nicht bei Legacy-Env-Var-Referenzen)
- `tests/smoke_all.py` integrieren

**Aufwand:** 0.25h
**Datei:** `tests/smoke_all.py` (bestehenden Test erweitern)

---

## Empfehlung

### v0.6.0 — Titel

**Documentation & Polish Release**

### Empfohlene Slices (in Reihenfolge)

| Rang | ID | Aufwand | Priority |
|------|----|---------|----------|
| 1 | **A** Architecture-Overview | 1h | 🔴 |
| 2 | **B** Config-Reference | 1h | 🔴 |
| 3 | **C** Consolidation.md aktualisieren | 0.5h | 🟡 |
| 4 | **D** Windows CI | 0.5h | 🟡 |
| 5 | **E** Branding-Regressionstest | 0.25h | 🟢 |
| | **Gesamt** | **~3.25h** | |

### DoD-Kriterien

1. `docs/architecture.md` existiert und beschreibt System-Architektur
2. `docs/config-reference.md` listet alle Config-Keys mit Defaults
3. `docs/consolidation.md` ist auf aktuellen Stand (keine veraltete Migration)
4. Windows CI läuft (mindestens CLI-Smoke-subset)
5. Branding-Regressionstest failt bei neuen Hermes-Referenzen
6. Smoke 19/19 (1 neuer Branding-Test)
7. Kein LastBrowser, keine user-facing Hermes, keine neuen Features
