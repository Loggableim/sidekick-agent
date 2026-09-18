# WebUI Performance & Stability — Backlog (50 Items)

**Stand:** 2026-09-18 · **Basis-Commit:** `1ff32cb` (master) · **Quelle:** Messungen am laufenden System (127.0.0.1:9119) in der Analyse-Session vom 2026-09-18.

Dieses Backlog ist die Arbeitsliste für Agenten, die die Sidekick WebUI schrittweise schneller und stabiler machen.
**Ein Item = ein Branch = ein PR.** Reihenfolge: P0 → P1 → P2, jeweils von oben nach unten.

> Zeilen-/Zahlenangaben beziehen sich auf den Basis-Commit. Bei Abweichung gilt der aktuelle Code —
> dann im Item notieren und mit aktuellen Werten arbeiten.

---

## Agent-Workflow (verbindlich)

1. **Item wählen:** erstes offenes `- [ ]` in Dokumentreihenfolge (P0 zuerst). Immer nur **EIN** Item pro PR.
2. **Branch:** `git fetch origin && git checkout -b perf/webui-<nr>-<slug> origin/master`
3. **Implementieren:** kleinster sinnvoller Fix. Keine Beifang-Refactors, keine Verhaltensänderung ohne Evidenz.
4. **Verifizieren (Pflicht):**
   - `node --check web/static/<geänderte-datei>.js` — bei **jeder** JS-Änderung (ein SyntaxError killt die ganze UI, siehe Skill `webui-js-parse-failure-triage`)
   - `python -m pytest tests/ -x -q` — relevante Tests
   - `python tests/smoke_all.py`
   - Bei sichtbaren UI-Änderungen: `python scripts/browser_webui_smoke.py`
   - Vorher/Nachher-Messung wo möglich (curl-Timing, gzip-Größe, DOMContentLoaded)
5. **Commit:** `perf(webui): <titel> (#<nr>)`
6. **Push + PR gegen `master`** mit Evidenz in der Beschreibung (Messung vorher/nachher, Testausgabe).
7. **Abhaken:** Checkbox in diesem Dokument auf `- [x]` + PR-Nummer setzen — im selben PR.
8. **Blockiert?** Im Item notieren (Grund, was fehlt), zum nächsten wechseln. Nicht raten.
9. **Bereits erledigt?** Checkbox mit Notiz abhaken (kurz begründen), weiter.

**Nicht erlaubt:** Sammel-PRs (mehrere Items), Refactors „nebenbei", Merge ohne grüne CI.
**Bei paralleler Agent-Arbeit:** Checkbox-Konflikte in dieser Datei sind erwartbar — beim Rebase auflösen.

## Persistentes Ziel (für `/goal`)

```
Arbeite das 50-Punkte-Backlog in docs/webui-performance-backlog.md ab: ein Item pro PR, in Dokumentreihenfolge (P0 zuerst). Pro Item: Branch perf/webui-<nr>-<slug> von origin/master, kleinster sinnvoller Fix, Verifikation (node --check auf geänderte JS-Dateien, python -m pytest tests/ -x -q, python tests/smoke_all.py), Commit, Push, PR gegen master mit Vorher/Nachher-Evidenz. Checkbox im Backlog-Doc im selben PR abhaken. Keine Sammel-PRs, keine Beifang-Refactors. Blockierte Items im Doc notieren und zum nächsten wechseln.
```

## Status

| Prio | Items | erledigt |
|------|-------|----------|
| P0 — Sofort | 10 | 0 |
| P1 — Nächste Welle | 20 | 0 |
| P2 — Danach | 20 | 0 |

---

# P0 — Sofort (klein, risikoarm, großer Effekt)

## 1. index.html gzipen · S · Risiko: niedrig
- [x] `_serve_index` (cli/web_server.py:6305) liefert 274 KB ohne gzip; curl bestätigt kein `content-encoding`. Gzip wäre ~47 KB (−83 %). → **PR #62**
- **Fix:** gzip in `_serve_index` analog `serve_spa` (web_server.py:6410) + `Vary: Accept-Encoding`.
- **Akzeptanz:** `curl -H 'Accept-Encoding: gzip' -D - -o /dev/null http://127.0.0.1:9119/` → `content-encoding: gzip`, ~47 KB.
- **Ergebnis:** 274.246 B → 48.228 B (−82,4 %); `content-encoding: gzip` + `vary: Accept-Encoding` gesetzt; dekomprimierter Body byte-identisch (`diff` leer). Browser-Smoke `load_within_budget` 1015 ms → 407 ms. `_serve_index` bekam einen optionalen `request`-Parameter, damit der bestehende Call-Site in `serve_spa` ihn durchreicht.

## 2. index.html mit ETag/304 statt `no-store` · S · Risiko: niedrig
- [ ] `_serve_index` sendet `Cache-Control: no-store, no-cache, must-revalidate` (web_server.py:6332); kein ETag. Jeder Reload lädt 274 KB neu.
- **Fix:** ETag aus gerendertem HTML (Hash) + `If-None-Match` → 304.
- **Akzeptanz:** Zweiter GET mit `If-None-Match` liefert 304 ohne Body.

## 3. index.html + Version-Token in-memory cachen (mtime-keyed) · S · Risiko: niedrig
- [ ] `_index_path.read_text` + replace pro Request (web_server.py:6311); `_webui_version_token()` macht bei `-dirty` ein `rglob` über WEB_DIST pro Request (web_server.py:1003-1020). Aktuell ist der Token `-dirty` → rglob aktiv.
- **Fix:** Gerenderte HTML + Token cachen, invalidieren über mtime von index.html.
- **Akzeptanz:** Kein `rglob`/`read_text` mehr pro Request (Messung/Log).

## 4. Gzip-Ergebnisse cachen statt pro Request komprimieren · S–M · Risiko: niedrig
- [ ] `gzip.compress(file_path.read_bytes(), 5)` pro Request (web_server.py:6414). Gemessen: 87 ms Event-Loop-Blocking für alle Shell-Assets pro Kalt-Load.
- **Fix:** Cache keyed `(path, mtime, size)`; oder Precompress beim Start (siehe Item 10).
- **Akzeptanz:** Zweiter Request desselben Assets komprimiert nicht erneut (Messung).

## 5. `load_settings()` cachen (mtime-check) · S · Risiko: niedrig
- [x] Läuft via `check_auth → is_auth_enabled → get_password_hash → load_settings` bei **jedem** API-Request (web/api/auth.py:417, web/api/config.py:4464). Gemessen: 2,1 ms Disk-Read pro `is_auth_enabled()`. → **PR #67**
- **Fix:** In-Memory-Cache mit mtime-Vergleich auf settings.json; Schreibpfade invalidieren.
- **Akzeptanz:** `is_auth_enabled()` warm < 0,1 ms; Settings-Änderung wirkt sofort.
- **Ergebnis:** Drei Caches: `load_settings()` keyed `(settings.json mtime_ns/size, resolved workspace)` und liefert Kopien; `resolve_default_workspace()` memoised (war mit 2,4 ms der eigentliche Kostentreiber) mit `is_dir()`-Revalidierung; `_state_dir()` memoised (`Path.resolve()` = realpath-Syscall). `save_settings()` refresht den Cache in place. Messung (100 warme Calls): `load_settings()` 2,02 → **0,14 ms**, `is_auth_enabled()` 3,25 → **0,13 ms**, `get_password_hash()` 2,83 → **0,11 ms**. Verifiziert: save_settings sofort sichtbar, externe Datei-Änderung via mtime erkannt, Caller-Mutation vergiftet den Cache nicht. Tests: `tests/test_settings_cache.py` (5).

## 6. PBKDF2-Falle entschärfen (600k-Iterationen pro Request) · S · Risiko: mittel
- [ ] Mit gesetztem `SIDEKICK_PASSWORD`-Env hasht `get_password_hash()` bei jedem `is_auth_enabled()` 600k PBKDF2-Iterationen (web/api/auth.py). Gemessen: 223 ms pro Hash.
- **Fix:** Env-Hash einmal berechnen + cachen (Invalidierung bei Env-/Settings-Änderung).
- **Akzeptanz:** `is_auth_enabled()` warm < 1 ms bei gesetztem Env-Passwort.

## 15. `<link rel="preconnect">` für cdn.jsdelivr.net · S · Risiko: niedrig
- [ ] 0 preconnect/dns-prefetch in index.html; 9 CDN-Refs (Prism, xterm, KaTeX).
- **Fix:** `<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>` + ggf. `dns-prefetch`.
- **Akzeptanz:** Hint im DOM; CDN-Assets starten früher (Performance-Panel).

## 21. Approval-Poll: Visibility-Gate + Intervall · S · Risiko: niedrig
- [ ] `_pollGlobalApprovals` läuft alle 3 s ohne `document.hidden`-Check (web/static/messages.js:3261, 3274).
- **Fix:** `if(document.hidden) return;` + Intervall 3 s → 5–10 s; sofortiger Poll bei `visibilitychange`.
- **Akzeptanz:** Im Hintergrund-Tab keine `/api/approval/pending-all`-Requests (Netzwerk-Panel).

## 22. Doppelten `_startGlobalApprovalPoll` entfernen · S · Risiko: niedrig
- [ ] Zwei Definitionen: messages.js:2875 (tot) und 3261 (aktiv); doppeltes `_stop` (2912/3267). Die zweite Definition gewinnt.
- **Fix:** Toten Block entfernen; sicherstellen, dass nur ein Timer existiert.
- **Akzeptanz:** `grep -c "function _startGlobalApprovalPoll" web/static/messages.js` = 1; Boot startet genau einen Poll.

## 39. CI-Gate: `node --check` über alle web/static/*.js · S · Risiko: niedrig
- [ ] Kein `node --check` in tests/, scripts/ oder CI; ein SyntaxError killt die ganze UI (Skill `webui-js-parse-failure-triage`).
- **Fix:** Kleines Script (z. B. `scripts/check_webui_js.py`) + Einbindung in `.github/workflows/ci.yml`.
- **Akzeptanz:** CI schlägt bei absichtlich eingebautem SyntaxError fehl; lokal Exit 0.

---

# P1 — Nächste Welle (mittlerer Aufwand, hoher Nutzen)

## 7. `_prune_expired_sessions()` nicht bei jedem verify · S · Risiko: niedrig
- [ ] `verify_session()` ruft prune bei jedem Verify (web/api/auth.py:378-382); prune schreibt die Session-Datei bei abgelaufenen Einträgen.
- **Fix:** Zeitgesteuert (z. B. max. 1×/60 s) statt pro Request.
- **Akzeptanz:** Verify-Pfad ohne Datei-Write im Normalfall.

## 8. fastapi_bridge: Thread pro Request → bounded ThreadPoolExecutor · M · Risiko: mittel
- [ ] `threading.Thread(target=self._run, daemon=True)` pro API-Call (web/api/fastapi_bridge.py:169-170). Unbegrenzt viele Threads bei Last.
- **Fix:** Bounded Executor + Backpressure (429/503 bei Überlast) oder Thread-Reuse.
- **Akzeptanz:** Lasttest mit 100 parallelen Requests → Thread-Zahl gedeckelt, keine Fehler.

## 9. SSE-Chunk-Reads batchen · M · Risiko: mittel
- [ ] `await asyncio.to_thread(self._chunks.get)` pro Chunk (web/api/fastapi_bridge.py:114); anyio-Default-Limiter = 40 Threads.
- **Fix:** Mehrere Chunks pro Thread-Aufruf drainen (z. B. `get_nowait`-Schleife nach erstem `get`).
- **Akzeptanz:** SSE-Streams unter Last blockieren den Limiter nicht mehr (Messung).

## 10. Top-Assets beim Start prekomprimieren · S · Risiko: niedrig
- [ ] ui.js/i18n.js/panels.js/style.css/index.html ≈ 350 KB gzip; Kompression kostet pro Request CPU (Item 4).
- **Fix:** Beim Serverstart einmal komprimieren, im RAM halten (keyed mtime).
- **Akzeptanz:** Erster Request nach Start liefert gzip ohne Kompressions-Spike.

## 11. i18n splitten · M · Risiko: mittel
- [ ] i18n.js = 663 KB, 9 Locales (en, it, ja, ru, es, de, zh, pt, ko) in einer Datei; pro Session wird 1 Locale gebraucht.
- **Fix:** Locale-Bundles in separate Dateien (z. B. `i18n/en.js`, `i18n/de.js`), Loader lädt aktive Sprache + en als Fallback (~75 KB statt 663 KB).
- **Akzeptanz:** Initialer JS-Payload sinkt um ~590 KB; Sprachwechsel lädt Bundle nach; Fallback en funktioniert.

## 12. panels.js lazy laden · M · Risiko: mittel
- [ ] panels.js = 517 KB, lädt immer, obwohl Panels selten geöffnet werden. Skill `lazy-load-panels` existiert; Revert d1f6060 wegen Parse-Kollision.
- **Fix:** Dynamischer Import beim ersten Panel-Wechsel; vorher `node --check`-Gate (Item 39) sicherstellen; IIFE-Wrap beachten.
- **Akzeptanz:** panels.js wird erst beim ersten Panel-Öffnen geladen (Netzwerk-Panel); alle Panels funktionieren.

## 13. Nicht-kritische Scripts on-demand laden · M · Risiko: mittel
- [ ] browser.js (246 KB), gmail.js (62 KB), discord.js (17 KB), discord-chat.js (45 KB), agents.js (58 KB), swarm.js (28 KB), onboarding.js (45 KB), enhancements.js (52 KB) laden upfront ≈ 550 KB.
- **Fix:** Pro Feature-Gruppe dynamischer Import beim Panel-/Feature-Öffnen.
- **Akzeptanz:** Initialer Payload < 1,5 MB; Features laden bei Bedarf fehlerfrei.

## 14. xterm/Prism/KaTeX self-hosten · M · Risiko: mittel
- [ ] 9 CDN-Refs (cdn.jsdelivr.net) in index.html; CDN-Ausfall = Terminal/Highlighting/KaTeX tot; zusätzliche DNS/TLS-Latenz.
- **Fix:** Assets nach web/static/vendor/ kopieren, lokale Pfade + SRI-Hashes aktualisieren.
- **Akzeptanz:** Kein externer CDN-Request mehr; alle Features funktionieren lokal.

## 19. SW: stale-while-revalidate für Shell-Assets · M · Risiko: mittel
- [ ] sw.js:162-179 network-first für Shell-Assets → jeder Load wartet auf Netzwerk.
- **Fix:** Cache-first + Hintergrund-Refresh (stale-while-revalidate) für versionierte Assets; Update via Version-Bump.
- **Akzeptanz:** Wiederholter Load liefert Shell aus Cache (<100 ms), Refresh im Hintergrund.

## 20. SW-Precache verkleinern + Update-Prompt · M · Risiko: niedrig
- [ ] sw.js:24-58 precached ~3,5 MB per `addAll` beim Install; kein Update-Prompt für neue Versionen.
- **Fix:** Precache auf kritisches Minimum; Rest on-demand cachen; `updatefound`-Prompt einbauen.
- **Akzeptanz:** Install-Precache < 1 MB; neue Version zeigt Update-Hinweis.

## 26. `api()`: Default-Timeout (AbortSignal.timeout) · S–M · Risiko: niedrig
- [ ] `api()` (web/static/workspace.js:1) hat keinen Default-Timeout; nur `_workspaceApiWithTimeout` für Workspace-Pfade. Hängende Requests stapeln sich.
- **Fix:** Default-Timeout (z. B. 30 s) via `AbortSignal.timeout`, überschreibbar per Option; Timeout-Fehler klar melden.
- **Akzeptanz:** Hängender Request bricht nach 30 s ab; UI bleibt bedienbar.

## 27. In-Flight-Dedupe ausweiten · S–M · Risiko: niedrig
- [ ] ctx-Poll (5 s, ui.js:5051) und Streaming-Poll (5 s, sessions.js:2663) können sich mit Nutzeraktionen überlappen; `_sessionListInFlight`-Guard existiert nur für die Session-Liste.
- **Fix:** Generischer In-Flight-Guard pro Endpoint (Promise-Map).
- **Akzeptanz:** Keine doppelten parallelen Requests desselben Endpoints (Netzwerk-Panel).

## 28. /api/sessions-Cache TTL erhöhen + ETag · S · Risiko: niedrig
- [ ] `_SESSION_LIST_CACHE_TTL = 2.0` (web/api/models.py:1562) bei 5-s-Poll.
- **Fix:** TTL 3–5 s; ETag/304 für unveränderte Listen.
- **Akzeptanz:** Poll-Kosten sinken; keine sichtbare Verzögerung bei Session-Änderungen.

## 30. Agent-Health-Poll gaten · S · Risiko: niedrig
- [ ] `pollAgentHealth` prüft nur `visibilityState`, nicht Panel-Sichtbarkeit (ui.js:6063); System-Health prüft beides (ui.js:5969).
- **Fix:** Panel-/Alert-Sichtbarkeit als Gate (oder Intervall adaptiv).
- **Akzeptanz:** Kein `/api/health/agent`-Poll, wenn weder Panel sichtbar noch Alert aktiv.

## 40. Doppelte Funktionsdefinitionen entfernen · M · Risiko: mittel
- [ ] commands.js: `cmdStatus` (1276/3524) u. a. 11 Namen doppelt; spaces.js `renderSpacesPanel` (799/1207); panels.js `closeKanbanTaskDetail` (1938/2565); terminal.js `openSplitTerminal`/`closeTerminalPane`/`toggleSplitTerminal` (882/1028 ff.).
- **Fix:** Jeweils ältere Kopie entfernen (Diff prüfen, welche aktiv ist — die letzte Definition gewinnt), Verhalten verifizieren.
- **Akzeptanz:** Keine doppelten Top-Level-Definitionen; Smoke-Tests grün.

## 45. Boot parallelisieren · S · Risiko: niedrig
- [ ] `await _syncGameModeStateFromServer()` läuft sequenziell nach Settings (boot.js:1933).
- **Fix:** Parallel starten (Promise.all mit Settings/Profile).
- **Akzeptanz:** Boot-Zeit sinkt messbar; keine Race-Fehler.

## 47. Ladezeit-Budget als Smoke-Test · S–M · Risiko: niedrig
- [ ] browser_webui_smoke.py existiert, prüft aber kein Ladezeit-Budget.
- **Fix:** DOMContentLoaded/TTI-Messung + Budget (z. B. < 2 s lokal) als Check.
- **Akzeptanz:** Check schlägt bei Budget-Verletzung fehl.

## 48. Browser-Smoke in CI verdrahten · S–M · Risiko: mittel
- [ ] browser_webui_smoke.py hat bereits Console-Error-Check (Zeile 1983), wird aber in CI nicht ausgeführt (nur Text-Assertions in pytest).
- **Fix:** CI-Job (z. B. ubuntu + Playwright) für den Browser-Smoke; oder als optionaler Job.
- **Akzeptanz:** CI führt Browser-Smoke aus; Fehler blockieren Merge.

## 49. Perf-Marks im Frontend · S–M · Risiko: niedrig
- [ ] 0 `performance.mark/measure` im Frontend.
- **Fix:** Marks für Boot, Session-Load, Render, SSE-Reconnect; Debug-Panel/Console-Ausgabe.
- **Akzeptanz:** `performance.getEntriesByType('measure')` liefert Werte; Baseline dokumentiert.

## 50. Baseline committen · S · Risiko: niedrig
- [ ] Keine Performance-Baseline im Repo.
- **Fix:** Lighthouse-/Performance-Timeline-Snapshot + curl-Timings in `docs/` ablegen (Vorher-Werte für alle Items).
- **Akzeptanz:** Baseline-Datei im Repo; Items referenzieren sie.

---

# P2 — Danach (größere Umbauten / Feinschliff)

## 16. Minify-Step · M · Risiko: mittel
- [ ] 3,1 MB JS unkomprimiert, kein Build/Minify (kein package.json).
- **Fix:** esbuild/terser-Step (npm oder Python-Wrapper), Output nach web/static/build/; Quell-Dateien bleiben.
- **Akzeptanz:** Minifizierter Payload ~1,2 MB; Quellcode unverändert nutzbar.

## 17. Script-Tags bündeln · M · Risiko: mittel
- [ ] 21 Script-Tags in index.html (HTTP/1.1: 21 Requests).
- **Fix:** 3–5 Bundles (core, panels, optional) — abhängig von Item 16.
- **Akzeptanz:** < 8 Requests beim Kalt-Load; Reihenfolge-Abhängigkeiten gewahrt.

## 18. style.css splitten · M–L · Risiko: mittel
- [ ] style.css = 475 KB, render-blocking.
- **Fix:** Kritisches CSS inline, Panel-CSS lazy laden.
- **Akzeptanz:** Render-blockierendes CSS < 100 KB.

## 23. Polling zentralisieren · M · Risiko: mittel
- [ ] ~30 `setInterval` in 11 Dateien.
- **Fix:** Ein Scheduler-Modul (Visibility-Gating, Backoff, Prioritäten).
- **Akzeptanz:** Alle Polls laufen über den Scheduler; Hintergrund-Tab ~0 Polls.

## 24. SSE `retry:`-Hint + Jitter · S · Risiko: niedrig
- [ ] Kein `retry:`-Feld in SSE-Antworten; EventSource-Default 3 s fix.
- **Fix:** `retry: <ms>` beim Stream-Start senden; Client-Jitter optional.
- **Akzeptanz:** Reconnect-Verhalten kontrolliert (Netzwerk-Panel).

## 25. SSE-Kanäle multiplexen · L · Risiko: hoch
- [ ] Bis zu 4 EventSource pro Session (chat/approval/subagent/clarify).
- **Fix:** Ein Multiplex-Kanal mit Event-Typen.
- **Akzeptanz:** 1 SSE-Verbindung pro Session; alle Events kommen an.

## 29. Gateway-Poll-Fallback verifizieren · S · Risiko: niedrig
- [ ] Fallback-Poll existiert (sessions.js:2681-2723); Stopp bei SSE-Recovery unklar.
- **Fix:** Verifizieren + ggf. Stopp-Logik fixen.
- **Akzeptanz:** Nach SSE-Recovery läuft kein Fallback-Poll mehr.

## 31. renderMessages inkrementell patchen · L · Risiko: hoch
- [ ] Full-Rebuild via `inner.innerHTML=''` (ui.js:7795) bei jedem Update (Tool-Completes etc.).
- **Fix:** Nur geänderte Rows patchen (Key-basiert).
- **Akzeptanz:** Tool-Complete-Update < 50 ms bei 200 Messages.

## 32. Session-HTML-Cache: LRU größer + Prefetch · M · Risiko: niedrig
- [ ] Cache max. 8 Einträge (ui.js:8387).
- **Fix:** LRU mit größerem Limit (z. B. 20) + Prefetch bei Hover in Session-Liste.
- **Akzeptanz:** Session-Wechsel zurück < 100 ms.

## 33. Resize-Listener konsolidieren · S–M · Risiko: niedrig
- [ ] 6+ `resize`-Listener in ui.js (66, 1935, 3229, 3295, 10386, 11015).
- **Fix:** Ein debounced Handler.
- **Akzeptanz:** 1 Listener; Verhalten identisch.

## 34. Scroll-Listener passive auditieren · S · Risiko: niedrig
- [ ] sessions.js:2389 ohne `passive` (capture=true).
- **Fix:** `{passive:true}` wo kein preventDefault nötig.
- **Akzeptanz:** Keine Scroll-Jank (Performance-Panel).

## 35. Message-Liste virtualisieren (>200) · L · Risiko: hoch
- [ ] Render-Window=50 existiert (ui.js:413); Session-Liste ist bereits virtualisiert (sessions.js:3245).
- **Fix:** Virtualisierung für lange Conversations.
- **Akzeptanz:** 500-Message-Session rendert < 200 ms.

## 36. highlightCode nur am Stream-Ende · M · Risiko: mittel (prüfen)
- [ ] `highlightCode()` läuft nach jedem Render (ui.js:7756); smd-Parser ist inkrementell.
- **Fix:** Prism nur am Stream-Ende/bei Bedarf.
- **Akzeptanz:** Streaming-Frame-Zeit sinkt (Messung).

## 37. rAF-Batching der Streaming-Writes auditieren · M · Risiko: mittel (prüfen)
- [ ] 19 rAF-Nutzungen in ui.js; Streaming-Pfad prüfen.
- **Fix:** DOM-Writes pro Frame bündeln.
- **Akzeptanz:** Keine Layout-Thrashing-Spikes (Performance-Panel).

## 38. Attachments: `loading="lazy"` + `decoding="async"` · S · Risiko: niedrig (teilweise vorhanden)
- [ ] `loading="lazy"` existiert in mehreren Pfaden (ui.js:898, 4412, 4491, 4670); `decoding="async"` fehlt; restliche Pfade prüfen.
- **Fix:** Alle `<img>`-Pfade ergänzen.
- **Akzeptanz:** Alle Message-Bilder lazy + async.

## 41. Frontend-Fehlertelemetrie verifizieren & erweitern · S–M · Risiko: niedrig (teilweise vorhanden)
- [ ] `window.onerror`/`unhandledrejection`/`console.error` → `/api/errors/log` existiert (workspace.js:666-740, routes.py:8410).
- **Fix:** Verifizieren, dass Fehler ankommen; um SSE-Reconnects und Render-Zeiten erweitern.
- **Akzeptanz:** Testfehler erscheint im Server-Log; Events im Log sichtbar.

## 42. localStorage-Wrapper zentralisieren · M · Risiko: mittel
- [ ] Mehrere Quota-Crash-Fixes (Commits b755433, c3e5229, 12dd016, f02e671); verstreute try/catch.
- **Fix:** Ein Utility (`safeGet/safeSet`) mit Quota-Handling; alle Aufrufer migrieren.
- **Akzeptanz:** Kein direkter localStorage-Zugriff außerhalb des Wrappers.

## 43. SSE-Verbindungszähler + Limit + Logging · M · Risiko: mittel
- [ ] Kein Limit/Zähler für SSE-Verbindungen gefunden.
- **Fix:** Zähler pro Endpoint, Limit mit 503, Logging bei Überschreitung.
- **Akzeptanz:** Limit greift im Lasttest; Zähler in /health sichtbar.

## 44. /health erweitern (Version/Uptime/SSE-Counts) · S · Risiko: niedrig
- [ ] `/health` existiert (routes.py:4567, web_server.py:1167).
- **Fix:** Version, Uptime, SSE-Counts, Cache-Statistiken ergänzen.
- **Akzeptanz:** `/health` liefert erweiterte Felder.

## 46. 349 Inline-`onclick` → Event-Delegation · L · Risiko: mittel
- [ ] 349 `onclick=` in index.html.
- **Fix:** Delegation über data-Attribute; perspektivisch CSP ohne `unsafe-inline`.
- **Akzeptanz:** Keine Inline-Handler; alle Buttons funktionieren.

---

## Referenzen

- **Skills:** `webui-js-parse-failure-triage` (JS-Parse-Fehler), `lazy-load-panels` (Item 12), `sidekick-webui-feature-audit` (Full-Stack-Audit)
- **Scripts:** `scripts/browser_webui_smoke.py`, `tests/smoke_all.py`, `tests/smoke_webui.py`
- **CI:** `.github/workflows/ci.yml`
