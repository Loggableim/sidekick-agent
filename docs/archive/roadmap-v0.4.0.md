# Sidekick v0.4.0 — Roadmap: Produktqualität

**Basis:** `v0.3.0` (`c2ec61a`)
**Leitbild:** *„Sidekick fühlt sich installierbar, erklärbar und zuverlässig an."*
**Keine Migration, keine breiten Refactors, keine neuen Feature-Massen, keine LastBrowser-Arbeit.**

---

## 1. WebUI Produktqualität

Die WebUI (`web/api/` + `web/static/`) ist funktional vollständig, aber nie
systematisch auf Produktqualität geprüft worden. Der Fokus lag auf Migration
und Bootbarkeit.

### Bestandsaufnahme

| Aspekt | Status | Risiko |
|--------|--------|--------|
| Dashboard (`/dashboard` oder SPA-Start) | ⚠️ Läuft, aber UX nie auditiert | Mittel |
| Session-Liste | ✅ Funktionell, kein Lazy-Loading | Niedrig |
| Session-Resume | ⚠️ Letzte Session via localStorage | Niedrig |
| Workspace-Browsing | ✅ `/api/workspaces` existiert | Niedrig |
| Streaming | ⚠️ SSE-basiert, nie unter Last getestet | Mittel |
| Fehlerzustände (401, 404, 500) | ❌ Keine strukturierte Fehlerbehandlung | Hoch |
| Health/Readiness | ✅ `/health` Endpoint existiert | Niedrig |
| Onboarding (`/api/onboarding`) | ✅ Modul vorhanden | Niedrig |
| SPA-Struktur (index.html + JS) | ⚠️ Monolithische JS, 41 Static-Dateien | Mittel |

### Vorschlag für v0.4.0

**A1. WebUI-Fehlerbehandlung** (Priorität: 🔴 Hoch)
- WebUI-API-Endpoints auf saubere JSON-Fehlerantworten prüfen
- 401/403/404/500 Fälle in `routes.py` durchgehen
- Frontend: Fehler-Toasts statt stumm fehlschlagender Requests
- **DoD:** Jeder API-Endpoint liefert `{"ok": false, "error": "..."}` bei Fehlern

**A2. Streaming-Stabilität** (Priorität: 🟡 Mittel)
- SSE-Streaming unter Last testen (disconnect/reconnect)
- `runtime/streaming.py` Verbindung nach Timeout sauber schließen
- WebSocket-Fallback checken (falls vorhanden)
- **DoD:** Streaming überlebt 5 Minute Idle + Reconnect

**A3. Health/Readiness prüfen** (Priorität: 🟢 Niedrig)
- `/health` auf Vollständigkeit prüfen (dependencies, config, sessions)
- Readiness-Endpoint: „is the agent ready to accept requests?"
- **DoD:** `sidekick dashboard --check` gibt Maschinen-lesbaren Status aus

---

## 2. Runtime-Verlässlichkeit

76 Subcommands, 94 Runtime-Module, 101 Tools — aber keine systematische
Diagnose-Infrastruktur.

### Bestandsaufnahme

| Aspekt | Status | Risiko |
|--------|--------|--------|
| Agent startup latency | ⚠️ Nie gemessen (import-heavy) | Mittel |
| Tool registry validation | ⚠️ Registry lädt alle Module eager | Mittel |
| Provider/model catalog | ✅ Funktionell, nie auf Korruptheit getestet | Niedrig |
| Credentials/config diagnostics | ✅ `sidekick doctor` deckt Basisfälle | Niedrig |
| Graceful failure paths | ❌ Viel `except Exception: pass` | Hoch |
| Config validation | ⚠️ Gateway-Warnings gefixt, aber kein Schema | Mittel |

### Vorschlag für v0.4.0

**B1. Graceful Failure Paths** (Priorität: 🔴 Hoch)
- Systematische Prüfung der wichtigsten Failure-Pfade:
  - Kein Internet / kein API-Key → klare Fehlermeldung
  - Config.yaml korrupt → Fallback + Warnung
  - Provider nicht erreichbar → nächster in der Fallback-Chain
- **DoD:** `sidekick` startet immer, auch ohne API-Key (mit klarer Meldung)

**B2. `sidekick doctor` ausbauen** (Priorität: 🟡 Mittel)
- Provider-Konnektivitätstest (Ping/HTTP-Head)
- Config-Schema-Validierung
- Disk/Speicher-Prüfung (state/log directory)
- **DoD:** `sidekick doctor` liefert Status-Code: 0=healthy, 1=warnings, 2=critical

**B3. Import-Lazyness** (Priorität: 🟢 Niedrig)
- Startup-Latenz messen (`time sidekick --help`)
- Top-Level-Imports in `cli/cli.py` und `run_agent.py` auf Lazy-Load umstellen
- **DoD:** `sidekick --help` startet in < 2s

---

## 3. CLI/TUI Polishing

76 Subcommands, aber nie auf UX-Konsistenz auditiert.

### Bestandsaufnahme

| Aspekt | Status | Risiko |
|--------|--------|--------|
| `sidekick doctor` | ✅ Läuft, aber Umfang unvollständig | Mittel |
| Fehlermeldungen | ⚠️ Meist Python-Tracebacks | Hoch |
| TUI Start/Exit | ⚠️ prompt_toolkit lädt, nie interaktiv getestet | Mittel |
| CLI help consistency | ✅ v0.2.0 Rebrand abgeschlossen | Niedrig |
| Tab completion | ✅ vorhanden | Niedrig |

### Vorschlag für v0.4.0

**C1. Fehlermeldungen menschlich machen** (Priorität: 🔴 Hoch)
- `try/except` Blöcke in CLI mit `click.style` / Rich-Output
- Tracebacks nur in `--debug` / `SIDEKICK_DEBUG=1`
- Normalfall: „⚠️ Provider not reachable. Run `sidekick doctor` for details."
- **DoD:** `sidekick` wirft keine Python-Tracebacks im Normalbetrieb

**C2. `sidekick doctor` substanziell erweitern** (🟡 Mittel — s. B2)

**C3. TUI Smoke** (Priorität: 🟢 Niedrig)
- `sidekick --tui` starten und per Timeout beenden
- TUI-Komponenten auf Rendering-Fehler prüfen
- **DoD:** TUI importiert und bootet ohne Crash (kein interaktiver Test nötig)

---

## 4. Tests/CI

16 Smoke-Tests, aber viele kritische Pfade ungedeckt.

### Bestandsaufnahme

| Testbereich | Status | Risiko |
|-------------|--------|--------|
| Core bootstrap (install/help/version) | ✅ 4 Tests | Niedrig |
| Import smoke | ✅ 5 Tests | Niedrig |
| Config/Env/Paths | ✅ 2 Tests | Mittel |
| Session layer | ✅ 4 Tests | Niedrig |
| TUI smoke | ✅ 1 Test | Niedrig |
| **WebUI HTTP API** | ❌ 0 Tests | **Hoch** |
| **Provider-less/offline** | ❌ 0 Tests | **Hoch** |
| **Session migration regression** | ❌ 0 Tests | Mittel |
| **Import-boundary audit** | ❌ 0 Tests | Mittel |
| **User-facing branding audit** | ⚠️ v0.2.0 Audit, kein automatisierter Test | Mittel |

### Vorschlag für v0.4.0

**D1. WebUI HTTP API Smoke** (Priorität: 🔴 Hoch)
- Server starten, `/health` abfragen, Server stoppen
- Session create/list/delete über HTTP
- Einfache Auth-Endpoints testen
- **DoD:** `tests/smoke_webui.py` existiert mit 3+ HTTP-Tests

**D2. Provider-less/offline Smoke** (Priorität: 🟡 Mittel)
- Ohne `.env` / ohne API-Keys starten → klare Meldung
- Ohne Internet starten → degraderte Meldung
- **DoD:** `sidekick --help` und `sidekick doctor` funktionieren offline

**D3. Branding-Audit als automatisierter Test** (Priorität: 🟢 Niedrig)
- Grep-basierter Test in `tests/` der verbleibende Sidekick/Nous-Stellen prüft
- **DoD:** CI failt, wenn neue user-facing Sidekick-Referenzen eingefügt werden

---

## 5. Dokumentation

8 Dokumente im `docs/`-Verzeichnis, aber kein systematischer Überblick.

### Bestandsaufnahme

| Dokument | Status | Nutzen |
|----------|--------|--------|
| `README.md` | ✅ Gepflegt | Einsteiger |
| `docs/consolidation.md` | ✅ Veraltet (Migration abgeschlossen) | Entwickler |
| `docs/releases/v0.1.0.md` | ✅ Historisch | — |
| `docs/releases/v0.2.0.md` | ✅ Historisch | — |
| `docs/releases/v0.3.0.md` | ✅ Historisch | — |
| `docs/audits/sidekick-links-v0.1.0.md` | ✅ Historisch | — |
| `docs/audits/sidekick-links-v0.2.0.md` | ✅ Historisch | — |
| `docs/design/session-contract-analysis.md` | ✅ Technisch | Entwickler |
| **Quickstart (neben README)** | ❌ Fehlt | **Hoch** |
| **Architektur-Überblick** | ❌ Fehlt | **Hoch** |
| **Config-Referenz** | ❌ Fehlt | Mittel |
| **Troubleshooting** | ❌ Fehlt | **Hoch** |
| **Migration von Sidekick** | ❌ Fehlt | Mittel |

### Vorschlag für v0.4.0

**E1. Troubleshooting/FAQ** (Priorität: 🔴 Hoch)
- Top-5 Probleme: kein API-Key, Config fehlt, Provider nicht erreichbar,
  Session verschwunden, WebUI startet nicht
- **DoD:** `docs/troubleshooting.md` existiert

**E2. Config-Referenz** (Priorität: 🟡 Mittel)
- Alle Config-Keys aus `shared/config.py` + `cli/config.py` dokumentieren
- **DoD:** `docs/config-reference.md` listet alle Config-Keys mit Defaults

**E3. Architektur-Überblick** (Priorität: 🟢 Niedrig)
- Diagramm: CLI → shared.* → runtime.*, WebUI → shared.* → runtime.*
- **DoD:** `docs/architecture.md` mit einem Mermaid-Diagramm

---

## Empfehlung

### Was sollte v0.4.0 werden?

v0.4.0 sollte ein **Quality & Documentation Release** sein:

> *„Sidekick fühlt sich installierbar, erklärbar und zuverlässig an."*

Kern: **WebUI-Fehlerbehandlung** + **Graceful Failures** + **Dokumentation**.
Die WebUI und die Runtime-Produktpfade sind der wertvollste Teil — sie
bekommen die meiste Aufmerksamkeit.

### Empfohlene 5 Slices (in Reihenfolge)

| Rang | Bereich | Priority | Aufwand | Begründung |
|------|---------|----------|---------|------------|
| 1 | WebUI-Fehlerbehandlung (A1) | 🔴 | Klein | Hat direkten User-Impact |
| 2 | Graceful Failures (B1) | 🔴 | Mittel | Kein Traceback = Professionalität |
| 3 | Troubleshooting-Doku (E1) | 🔴 | Klein | Erfüllt „erklärbar" |
| 4 | WebUI HTTP API Tests (D1) | 🔴 | Mittel | Erfüllt „zuverlässig" (messbar) |
| 5 | `sidekick doctor` ausbauen (B2/C2) | 🟡 | Mittel | Erfüllt „installierbar" |

### Was sollte explizit nicht in v0.4.0?

- ❌ Kein Frontend-Redesign (kein React/Vue, kein neues Dashboard)
- ❌ Keine Gateway-Plattform-Erweiterung
- ❌ Keine neuen Tools oder Provider
- ❌ Kein Session-Modell-Merge (shared vs web bleibt getrennt)
- ❌ Kein LastBrowser
- ❌ Keine Windows/MacOS CI-Matrix
- ❌ Kein neues Build-System

### DoD-Kriterien für v0.4.0

1. **WebUI-Fehlerbehandlung:** Alle API-Endpoints in `routes.py` liefern
   strukturierte `{"ok": false, "error": "..."}` bei Fehlern (Stichprobe 10
   Endpoints)
2. **Graceful Failures:** `sidekick` startet ohne API-Key mit klarer Meldung
   (kein Traceback)
3. **Troubleshooting:** `docs/troubleshooting.md` deckt die Top-5-Probleme ab
4. **WebUI HTTP Smoke:** `tests/smoke_webui.py` testet `/health` + Session
   CRUD über HTTP
5. **`sidekick doctor`:** Liefert Status-Code 0/1/2 und prüft Provider-
   Konnektivität (HTTP-Head)

### Risiken

| Risiko | Eintrittsw'keit | Impact | Mitigation |
|--------|-----------------|--------|------------|
| WebUI-Fehleranalyse deckt tiefere Mängel auf | Mittel | Hoch | Pro Endpoint fixen, nicht auf Vollständigkeit warten |
| Graceful-Failure-Änderungen berühren viele Codepfade | Niedrig | Mittel | Fokus auf CLI-Einstieg + doctor |
| Doku veraltet nach Release | Hoch | Niedrig | README + troubleshooting.md sind „living docs" |
| v0.4.0 wird zu ambitioniert | Mittel | Mittel | Auf 5 Slices begrenzen, Rest auf v0.5.0 |

---

## Zeitplan (grobe Schätzung)

| Phase | Aufwand | Beschreibung |
|-------|---------|-------------|
| A1 WebUI-Fehlerbehandlung | 1–2h | Endpoints durchgehen, Fehler-Template einführen |
| B1 Graceful Failures | 2–3h | CLI-Einstieg, config-load, provider-ping |
| E1 Troubleshooting | 0.5–1h | docs/troubleshooting.md |
| D1 WebUI HTTP Smoke | 1–2h | tests/smoke_webui.py |
| B2/C2 doctor ausbauen | 1–2h | Status-Codes, connectivity check |
| **Gesamt** | **~6–10h** | 5–7 kleine Commits |
