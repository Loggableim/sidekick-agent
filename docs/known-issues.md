# Sidekick — Known Issues (v0.5.0)

**Stand:** Tag `v0.5.0` (Commit `129322f`)

---

## Gateway import warnings (non-blocking)

```
Warning: config validation failed: cannot import name 'print_config_warnings'
Warning: deprecation check failed: cannot import name 'warn_deprecated_cwd_env_vars'
```

**Status:** ✅ Kosmetisch, keine funktionalen Auswirkungen
**Ursache:** `sidekick_cli.config` routet via Shim zu `runtime.config`, dort
die Startup-Funktionen als No-Ops implementiert.
**Details:** `runtime/config.py` enthält Stub-Implementierungen. Die echte
Config-Validierung läuft über `sidekick doctor`.
**Resolved in:** v0.3.0 (Stubs)

---

## Session-Layer: zwei Datenmodelle

| Modell | Felder | Persistenz |
|--------|--------|------------|
| `shared.sessions.Session` | 6 (Basisfelder) | `~/.sidekick/state/webui/sessions/` |
| `web.api.models.Session` | 30+ (agent state, streaming, ...) | `~/.sidekick/state/webui/sessions/` |

**Status:** ⚠️ Gleicher Storage-Pfad, aber unterschiedliche Objektmodelle.
**Konsequenz:** Sessions aus der CLI sind im WebUI sichtbar, aber WebUI-
spezifische Felder (agent state, streaming, compression anchor) sind nur
über die WebUI-API zugänglich.
**Workaround:** Keiner nötig — beide lesen/schreiben dasselbe JSON-Verzeichnis.
**Geplant für:** v0.6.0 oder später (kein akuter Blocker)

---

## CLI-Help-Text: `HERMES_*` Env-Var-Referenzen

In `sidekick --help` erscheinen noch `HERMES_INFERENCE_MODEL`,
`HERMES_INFERENCE_PROVIDER` und `HERMES_ACCEPT_HOOKS` als Argument-
Dokumentation.

**Status:** ✅ Bewusst erhalten (Legacy-Kompatibilität für Bestandssetups)
**Details:** Die Env-Vars funktionieren weiterhin. Neue Nutzer sehen
`SIDEKICK_*` als kanonische Namen.

---

## WebUI localStorage: `hermes-*` Legacy-Keys

Einige CSS-Klassen und localStorage-Keys verwenden noch `hermes-`-Präfix.

**Status:** ✅ Kosmetisch, Seiteneffektfrei. Migrations-Shim in `boot.js`
kopiert alte Keys beim ersten Laden.
**Details:** In v0.2.0 wurden alle produktiven Storage-Keys von `hermes-*`
auf `sidekick-*` migriert. Die alten Keys bleiben für Browser-Kompatibilität
erhalten.
**Resolved in:** v0.2.0

---

## Windows CI fehlt

**Status:** ⚠️ Noch nicht aktiviert
**Details:** CI läuft auf Ubuntu (full) und macOS (subset). Windows ist
vorbereitet aber noch nicht getestet. Hauptrisiken: Shell-Kompatibilität,
Pfad-Separator, Python-Binary-Name.
**Geplant für:** v0.6.0 oder später

---

## `hermes` CLI-Alias

```bash
hermes --help   # → sidekick
```

**Status:** ✅ Dokumentierter Legacy-Alias in `pyproject.toml`.
Beide Binarys zeigen auf denselben Entrypoint.

---

## Gateway await-Bug (gefixt)

**Status:** ✅ Kein bekanntes Problem
**Details:** Ein pre-existing `await` in einer sync-Funktion wurde in v0.1.0
behoben (`asyncio.run_coroutine_threadsafe`). Gateway importiert ohne
Warnings.

---

## Zusammenfassung

| Issue | Status | Seit | Blocking |
|-------|--------|------|----------|
| Gateway Warnings | ✅ Non-blocking | v0.3.0 | ❌ |
| Session-Layer Divergenz | ⚠️ Workaround | — | ❌ |
| CLI HERMES_* Referenzen | ✅ Legacy compat | — | ❌ |
| WebUI hermes-* Keys | ✅ Residual | v0.2.0 | ❌ |
| Windows CI | ⚠️ Nicht aktiv | — | ❌ |
| hermes-Alias | ✅ Dokumentiert | v0.1.0 | ❌ |
