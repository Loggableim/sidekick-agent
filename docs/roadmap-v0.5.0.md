# Sidekick v0.5.0 — Roadmap: Stabilität & Diagnose

**Basis:** `v0.4.0` (`9a47ef3`)
**Leitbild:** *„Sidekick fühlt sich bei echter Nutzung stabil an."*
**Keine Features · Kein LastBrowser · Kein Frontend-Redesign · Keine neuen Provider/Tools**

---

## 1. WebUI Streaming-Stabilität

### Architektur (Stand v0.4.0)

```
Browser → POST /api/session/chat → routes.py → streaming._run_agent_streaming()
  → AIAgent.run_conversation() → SSE events (text delta, tool_call, stream_end)
  → Session.save() (nach jedem meaningful progress + am Ende)
```

SSE wird über stdlib `wfile.write()` mit manuellem Framing ausgeliefert.
Ein 5s-Heartbeat (`_SSE_HEARTBEAT_INTERVAL_SECONDS = 5`) hält die Verbindung
bei langen LLM-Think-Phasen offen.

### Streaming-Fehlerfälle (Stand v0.4.0)

| Fehlerfall | Verhalten | Risiko |
|------------|-----------|--------|
| Agent-Fehler (Provider-Timeout) | `_classify_provider_error()` → Fehler-SSE-Event | Mittel |
| Client Disconnect | `wfile.write()` wirft `BrokenPipeError` → `except` im Handler | 🟡 Mittel — wird gefangen, aber ohne Garantie dass Session.save() läuft |
| API-Key fehlt | Error wird an Client zurückgegeben | 🟢 Niedrig (sauber) |
| Stream nach Session-Close | `CANCEL_FLAGS`-Dict prüft Session-ID | 🟡 Mittel — manuelle Cancel-Flag- Verwaltung |
| Timeout (kein Provider-Response) | `_clarify_timeout_seconds()` → Timeout-Event | 🟢 Niedrig |
| Server-Neustart während Stream | `_clear_stale_stream_state()` bei nächstem Session-Load | 🟡 Mittel — Session-Status repariert sich |
| SSE Heartbeat-Disconnect | Kein Heartbeat → nginx/cloudflare schließt nach ~60s | 🟡 Mittel (dokumentiert) |

### Kritischste Lücke

**Session.save() bei Client-Disconnect:** Wenn der Client die Verbindung
trennt, bricht `wfile.write()` mit `BrokenPipeError` / `ConnectionResetError`
ab. Der `except`-Block im Handler fängt das, aber der `finally`-Block, der
`Session.save()` aufruft, läuft möglicherweise nicht sauber durch
(Threading-Race).

### Vorschlag für v0.5.0

| Maßnahme | Aufwand | Impact | Risiko |
|----------|---------|--------|--------|
| **S1.** `_run_agent_streaming()` mit `try/finally` für `Session.save()` absichern | Klein | 🔴 Hoch | Niedrig |
| **S2.** `CANCEL_FLAGS` auf `threading.Event` umstellen (statt Dict) | Mittel | 🟡 Mittel | Mittel |
| **S3.** Streaming-Fehlerfall-Test in `tests/smoke_webui.py` ergänzen (POST → disconnect → check session) | Klein | 🟡 Mittel | Niedrig |
| **S4.** SSE-Keepalive auf Konfiguration prüfen (heartbeat-Intervall) | Sehr klein | 🟢 Niedrig | Sehr niedrig |

---

## 2. `sidekick doctor` Provider-Konnektivität

### Aktuelle Checks (Stand v0.4.0)

| Check | Typ | Offline-fähig |
|-------|-----|---------------|
| Python-Version | Lokal | ✅ Ja |
| Venv-Aktiv | Lokal | ✅ Ja |
| Pip-Pakete | Lokal | ✅ Ja |
| `.env` vorhanden | Lokal | ✅ Ja |
| API-Key konfiguriert | Lokal | ✅ Ja |
| `config.yaml` vorhanden | Lokal | ✅ Ja |
| Config-Version aktuell | Lokal | ✅ Ja |
| Auth-Provider (login status) | Lokal | ✅ Ja |
| Provider-Modell (model/provider-Check) | Lokal | ✅ Ja |
| Provider-Konnektivität (HTTP-Ping) | **Online** | ❌ Nein |

### Vorschlag für v0.5.0

| Neuer Check | Typ | Aufwand | Risiko |
|-------------|-----|---------|--------|
| **D1.** Provider-Konnektivität (optional online): `HEAD /v1/models` für konfigurierten Provider | Optional online | Klein | Niedrig — Timeout nach 5s, Fehler = Degraded |
| **D2.** Config-Schema-Validierung: prüft unbekannte Keys in `config.yaml` | Lokal | Klein | Niedrig |
| **D3.** Disk-Usage für State/Logs: Warnt bei >500MB | Lokal | Sehr klein | Niedrig |
| **D4.** Session-Verzeichnis-Integrität: zählt JSON-Dateien, prüft auf Korruptheit | Lokal | Klein | Niedrig |

**Wichtig:** Alle neuen Online-Checks müssen **optional** und mit kurzem
Timeout sein. Doctor darf nie durch Netzwerkabhängigkeit blockieren.

### Exit-Code-Schema (unverändert zu v0.4.0)

| Code | Bedeutung |
|------|-----------|
| 0 | Alles gesund |
| 1 | Warnings (optionale Pakete fehlen, nicht-kritisch) |
| 2 | Critical (kein API-Key, Config kaputt) |
| (3) | (Reserviert für „Offline — einige Checks nicht ausführbar") |

---

## 3. CI Cross-Platform

### Status Quo (v0.4.0)

- **Nur Linux** (ubuntu-latest, Python 3.11 + 3.12)
- 9 CI-Schritte (install, CLI smoke, import, session, gateway, env, cleanup)
- Läuft in ~2-3 Minuten

### Risikoanalyse

| OS | Risiko | Besonderheiten |
|----|--------|----------------|
| **macOS** | 🟢 Niedrig | POSIX-kompatibel, Pfadverhalten ähnlich Linux |
| **Windows** | 🟡 Mittel | `Path.home()` → `C:\Users\...`, `os.sep = \\`, andere Python-Binary-Namen, Shell-Unterschiede |

### Vorschlag für v0.5.0

| Maßnahme | Aufwand | Risiko |
|----------|---------|--------|
| **C1.** macOS zur Matrix hinzufügen (nur CLI + Import + Session) | Sehr klein | 🟢 Niedrig — gleiche Shell, gleiche Pfade |
| **C2.** Windows als `windows-latest` mit `pwsh` (nur `--help`, `--version`, `python -c` imports) | Klein | 🟡 Mittel — Shell-Kompatibilität |
| **C3.** Pfad-Tests auf Windows/macOS (sidekick_home, state_dir) | Klein | 🟢 Niedrig |
| **C4.** CI-Gesamtzeit im Blick behalten (Matrix × 2 Python-Versionen × 3 OS = 6 Jobs) | — | 🟡 Mittel — Kosten/Zeit |

**Empfehlung:** macOS in v0.5.0 aktivieren, Windows vorbereiten aber erst
in v0.6.0 aktivieren, nachdem `tests/smoke_all.py` auf Windows getestet wurde.

---

## 4. Risiken / Scope Control

### Arbeiten für v0.5.0 (klein und sicher)

| ID | Beschreibung | Dateien | Geschätzter Aufwand |
|----|-------------|---------|---------------------|
| **S1** | `_run_agent_streaming()` try/finally für Session.save() absichern | `web/api/streaming.py` | 0.5h |
| **S3** | Streaming-Fehlerfall-Test (disconnect → session check) | `tests/smoke_webui.py` | 0.5h |
| **D1** | Provider-Konnektivität (optional online, 5s timeout) | `cli/doctor.py` | 1h |
| **D3** | Disk-Usage-Warnung in doctor | `cli/doctor.py` | 0.5h |
| **D4** | Session-Verzeichnis-Integrität | `cli/doctor.py`, `shared/sessions.py` | 0.5h |
| **C1** | macOS CI-Matrix | `.github/workflows/ci.yml` | 0.25h |
| **C3** | Pfad-Tests macOS ergänzen | `tests/smoke_all.py` | 0.25h |
| **Gesamt** | | | **~3.5h** |

### Auf v0.6+ verschieben

| Thema | Begründung |
|-------|------------|
| S2 – CANCEL_FLAGS auf threading.Event | Riskanter Refactor, kein akutes Problem |
| C2 – Windows CI aktivieren | Braucht manuellen Smoke-Test vor Aktivierung |
| C4 – CI-Matrix-Optimierung | Erst beobachten, dann optimieren |
| D2 – Config-Schema-Validierung | Low-Priority, kaum User-Impact |
| WebSocket statt SSE | Fundamentale Architekturänderung — nicht in v0.5 |

### Regression-Prävention

Folgende v0.1–v0.4-Errungenschaften müssen Tests/Fails verhindern:

1. **Session-Contract:** `shared/sessions` vs `web.api.models` bleiben kompatibel
   → Smoke-Test: shared session → web.api.session_ops delegation ✅
2. **Gateway-Warnings:** Keine Import-Warnings
   → Smoke-Test: `gateway.run import (0 warnings)` ✅
3. **Env-Alias:** `SIDEKICK_*` > `HERMES_*`
   → Smoke-Test: `shared.paths: env var priority` ✅
4. **WebUI-Fehler:** Strukturierte Responses statt Tracebacks
   → Smoke-Test: `/health` returns 200 + JSON ✅
5. **Doctor Exit-Codes:** 0/1/2 statt immer 0
   → Smoke-Test: `sidekick doctor` valid exit codes ✅

---

## Empfehlung

### v0.5.0 — Titel

**Stability & Diagnostics Release**

### Empfohlene 5 Slices (in Reihenfolge)

| Rang | ID | Beschreibung | Priority | Aufwand |
|------|----|-------------|----------|---------|
| 1 | **S1** | Streaming-Session.save() try/finally absichern | 🔴 | 0.5h |
| 2 | **D1** | Provider-Konnektivität (optional online, 5s, degraded) | 🔴 | 1h |
| 3 | **C1** | macOS CI-Matrix aktivieren | 🟡 | 0.25h |
| 4 | **S3/D3/D4** | Streaming-Fehler-Test + Disk-Usage + Session-Integrität | 🟡 | 1.5h |
| 5 | **C3** | macOS-Pfad-Tests | 🟢 | 0.25h |

### Explizite Nicht-Ziele

- ❌ Kein WebSocket-Refactor
- ❌ Kein CANCEL_FLAGS-Refactor
- ❌ Kein Windows CI (nur vorbereiten)
- ❌ Keine Config-Schema-Validierung
- ❌ Kein LastBrowser
- ❌ Kein Frontend-Redesign
- ❌ Keine neuen Tools/Provider
- ❌ Keine Runtime-Architekturänderung

### DoD-Kriterien für v0.5.0

1. **Streaming:** `_run_agent_streaming()` persistiert Session bei Fehlern im
   `finally`-Block (kein Datenverlust bei Client-Disconnect)
2. **Doctor:** Provider-Konnektivität als optionaler Online-Check (5s Timeout,
   degraded statt fail)
3. **CI:** macOS in der Test-Matrix (CLI + Imports + Session Layer)
4. **Doctor:** Disk-Usage-Warnung + Session-Integritäts-Check
5. **Smoke:** 19/19 oder besser (2 neue Tests: Streaming-Disconnect + macOS-Pfade)
6. **Keine neuen Features · kein LastBrowser · keine user-facing Hermes**

### Risiken

| Risiko | Eintrittsw'keit | Impact | Mitigation |
|--------|-----------------|--------|------------|
| Streaming-Save-Änderung bricht SSE | Niedrig | Hoch | Vorher/nachher mit `tests/smoke_webui.py` testen |
| Doctor-Online-Check macht doctor langsam | Mittel | Mittel | Timeout 5s, Flag `--offline` ergänzen |
| macOS CI schlägt wegen Homebrew-Unterschieden fehl | Mittel | Mittel | Nur CLI + Imports + Session — kein Browser-Test |
| v0.5.0 wird zu ambitioniert | Mittel | Mittel | Auf 5 Slices begrenzt, Rest auf v0.6.0 |
