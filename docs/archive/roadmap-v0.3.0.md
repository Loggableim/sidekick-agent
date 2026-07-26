# Sidekick v0.3.0 — Roadmap-Vorschlag

**Basis:** `v0.2.0` (`5f9edfe`)
**Keine neuen Features — nur Stabilisierung und Härtung**

---

## Geplante Arbeiten (in dieser Reihenfolge)

### A. Session Contract härten

`shared/sessions.py` ist das kanonische Modell. `web/api/session_ops.py`
delegiert teilweise, hat aber noch WebUI-spezifische Lock- und Stream-Logik.

**Schritte:**
1. `shared/sessions.Session` um optionale Felder für agent state erweitern
   (optional — WebUI-spezifische Felder bleiben in der WebUI-Klasse)
2. Alle WebUI-Session-Lese/Schreibzugriffe auf `shared.sessions.*` uniformieren
3. `web/api/session_ops.py` auf reine WebUI-spezifische Wrapper reduzieren
4. Test: Session via CLI (`shared.sessions`) anlegen und via WebUI lesen

### B. Gateway-Warnings beseitigen

2 non-blocking Warnings beim Import von `runtime.gateway.run`:

```
Warning: config validation failed: cannot import name 'print_config_warnings'
Warning: deprecation check failed: cannot import name 'warn_deprecated_cwd_env_vars'
```

**Schritte:**
1. `runtime/config.py` um `print_config_warnings()` ergänzen (stub oder Delegation)
2. `runtime/config.py` um `warn_deprecated_cwd_env_vars()` ergänzen (stub)
3. Smoke-Import testen → 0 Warnings

### C. CI/Smoke erweitern

Aktuell: 10 Tests in `tests/smoke_all.py`

**Neue Tests:**
1. `SIDEKICK_HOME` → Config-Ladetest
2. `SIDEKICK_HOME` → Legacy-Compat-Test
3. WebUI-Server: `/health`-Endpoint via HTTP (start/stop server)
4. WebUI: Session create/list über API
5. `--version` Output parsen

### D. Config/Home/Env-Alias-Tests

Systematisch prüfen, dass alle Legacy-Fallback-Chains funktionieren:

| Test | Beschreibung |
|------|-------------|
| `SIDEKICK_HOME` set → Config wird von dort geladen | ✅ |
| `SIDEKICK_HOME` set → Config wird von dort geladen (Fallback) | ⏳ |
| `~/.sidekick/` existiert → wird genutzt | ⏳ |
| `~/.sidekick/` existiert → wird genutzt (nur wenn ~/.sidekick fehlt) | ⏳ |
| `SIDEKICK_STATE_DIR` → überschreibt state-Pfad | ⏳ |
| `SIDEKICK_STATE_DIR` → überschreibt state-Pfad (Fallback) | ⏳ |

### E. Gateway-Hardening (niedrige Priorität)

- `runtime/gateway/run.py` await-Bug-Fix testen
- Gateway-Import ohne Warnings verifizieren
- Gateway-Dokumentation: Was läuft, was nicht

---

## Explizit nicht in v0.3.0

- ❌ Keine neuen Tools oder Provider
- ❌ Keine LastBrowser-Arbeit
- ❌ Keine neuen CLI-Commands
- ❌ Kein Frontend-Redesign
- ❌ Kein Session-Format-Break (Rückwärtskompatibilität erhalten)

---

## Geschätzter Aufwand

| Bereich | Dateien | Aufwand |
|---------|---------|---------|
| A. Session Contract | 2-3 | klein |
| B. Gateway-Warnings | 1 | sehr klein |
| C. CI/Smoke | 2-3 | klein |
| D. Config/Env-Tests | 2-3 | klein |
| E. Gateway-Hardening | 1 | sehr klein |

**Gesamt:** ~6-10 kleine Commits, ~1-2 Stunden Arbeit.

---

## Tag-Strategie

`v0.3.0` nach Abschluss aller Punkte A–E.
