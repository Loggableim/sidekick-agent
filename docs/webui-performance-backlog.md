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
- [x] `_serve_index` sendet `Cache-Control: no-store, no-cache, must-revalidate` (web_server.py:6332); kein ETag. Jeder Reload lädt 274 KB neu. → **PR #63**
- **Fix:** ETag aus gerendertem HTML (Hash) + `If-None-Match` → 304.
- **Akzeptanz:** Zweiter GET mit `If-None-Match` liefert 304 ohne Body.
- **Ergebnis:** Schwacher ETag (`W/"<sha256[:32]>"`) über das gerenderte HTML + RFC-7232-Vergleich (`If-None-Match`, auch Listen/`*`/Strong-Form). `Cache-Control` von `no-store` → `private, no-cache, must-revalidate` (sonst würde der Browser nie revalidieren). curl: 48.229 B/283 ms → 304/0 B/25 ms; echter Chromium: Reload überträgt 201 B statt 48.507 B. Test: `tests/test_paths.py::test_web_server_root_revalidates_with_etag`.

## 3. index.html + Version-Token in-memory cachen (mtime-keyed) · S · Risiko: niedrig
- [x] `_index_path.read_text` + replace pro Request (web_server.py:6311); `_webui_version_token()` macht bei `-dirty` ein `rglob` über WEB_DIST pro Request (web_server.py:1003-1020). Aktuell ist der Token `-dirty` → rglob aktiv. → **PR #65**
- **Fix:** Gerenderte HTML + Token cachen, invalidieren über mtime von index.html.
- **Akzeptanz:** Kein `rglob`/`read_text` mehr pro Request (Messung/Log).
- **Ergebnis:** Zwei Caches: Version-Token (keyed auf `WEBUI_VERSION` + newest-mtime) und gerenderte HTML (keyed auf `(path, mtime_ns, size, prefix, token)`, LRU-Cap 8). Zusätzlich `rglob`+`stat` (~17 ms) durch `os.scandir`-Walk (~0,8 ms) ersetzt. Messung über 30 warme Requests bei dirty tree: `read_text(index.html)` 30 → **0**, `rglob` 30 → **0**, warm GET / 28,10 ms → **8,95 ms** (−68 %). Tests: `test_web_server_root_does_not_re_read_index_per_request`, `test_web_server_root_render_cache_invalidates_on_index_change`.

## 4. Gzip-Ergebnisse cachen statt pro Request komprimieren · S–M · Risiko: niedrig
- [x] `gzip.compress(file_path.read_bytes(), 5)` pro Request (web_server.py:6414). Gemessen: 87 ms Event-Loop-Blocking für alle Shell-Assets pro Kalt-Load. → **PR #66**
- **Fix:** Cache keyed `(path, mtime, size)`; oder Precompress beim Start (siehe Item 10).
- **Akzeptanz:** Zweiter Request desselben Assets komprimiert nicht erneut (Messung).
- **Ergebnis:** `_gzip_cached(path, stat)` mit Cache keyed `(path, mtime_ns, size)`, Cap 128 Einträge / 32 MB. Messung (20 Shell-Assets): reine `gzip.compress`-Kosten 80,8 ms pro Durchlauf; warm-Pass 190,4 ms → **105,1 ms**; Cache hält 20 Einträge / 891 KB. Editierte Datei wird rekomprimiert (mtime-Invalidierung verifiziert). Test: `test_static_asset_gzip_is_cached_and_invalidated`.

## 5. `load_settings()` cachen (mtime-check) · S · Risiko: niedrig
- [x] Läuft via `check_auth → is_auth_enabled → get_password_hash → load_settings` bei **jedem** API-Request (web/api/auth.py:417, web/api/config.py:4464). Gemessen: 2,1 ms Disk-Read pro `is_auth_enabled()`. → **PR #67**
- **Fix:** In-Memory-Cache mit mtime-Vergleich auf settings.json; Schreibpfade invalidieren.
- **Akzeptanz:** `is_auth_enabled()` warm < 0,1 ms; Settings-Änderung wirkt sofort.
- **Ergebnis:** Drei Caches: `load_settings()` keyed `(settings.json mtime_ns/size, resolved workspace)` und liefert Kopien; `resolve_default_workspace()` memoised (war mit 2,4 ms der eigentliche Kostentreiber) mit `is_dir()`-Revalidierung; `_state_dir()` memoised (`Path.resolve()` = realpath-Syscall). `save_settings()` refresht den Cache in place. Messung (100 warme Calls): `load_settings()` 2,02 → **0,14 ms**, `is_auth_enabled()` 3,25 → **0,13 ms**, `get_password_hash()` 2,83 → **0,11 ms**. Verifiziert: save_settings sofort sichtbar, externe Datei-Änderung via mtime erkannt, Caller-Mutation vergiftet den Cache nicht. Tests: `tests/test_settings_cache.py` (5).

## 6. PBKDF2-Falle entschärfen (600k-Iterationen pro Request) · S · Risiko: mittel
- [x] Mit gesetztem `SIDEKICK_PASSWORD`-Env hasht `get_password_hash()` bei jedem `is_auth_enabled()` 600k PBKDF2-Iterationen (web/api/auth.py). Gemessen: 223 ms pro Hash. → **PR #68**
- **Fix:** Env-Hash einmal berechnen + cachen (Invalidierung bei Env-/Settings-Änderung).
- **Akzeptanz:** `is_auth_enabled()` warm < 1 ms bei gesetztem Env-Passwort.
- **Ergebnis:** Env-Hash-Cache keyed `(env-Wert, signing key als Salt)`; zusätzlich `_signing_key()` memoised (las die Key-Datei pro Aufruf). Messung mit `SIDEKICK_WEBUI_PASSWORD`: `get_password_hash()` 245,3 → **0,61 ms**, `is_auth_enabled()` 255,5 → **0,98 ms** (−99,6 %). Verifiziert: richtiges Passwort verifiziert, falsches abgelehnt; Env-Wechsel invalidiert (altes Passwort danach abgelehnt); Cache-Hash == frisch berechneter Hash; ohne Env Fallback auf settings.json. Tests: `tests/test_auth_password_cache.py` (4).

## 15. `<link rel="preconnect">` für cdn.jsdelivr.net · S · Risiko: niedrig
- [x] 0 preconnect/dns-prefetch in index.html; 9 CDN-Refs (Prism, xterm, KaTeX). → **PR #69**
- **Fix:** `<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>` + ggf. `dns-prefetch`.
- **Akzeptanz:** Hint im DOM; CDN-Assets starten früher (Performance-Panel).
- **Ergebnis:** Beide Hints im `<head>` **vor** den CDN-Tags eingefügt (verifiziert per Playwright: `document.querySelectorAll('link[rel="preconnect"],link[rel="dns-prefetch"]')` liefert beide Einträge mit `crossorigin=anonymous`). Der Timing-Effekt war in dieser Umgebung **nicht belastbar messbar** (Chromium-Connection-Reuse + variable Last; A/B-Läufe schwankten zwischen −200 ms und +900 ms) — als Hint ist die Änderung dennoch korrekt und risikofrei. Test: `test_index_preconnects_to_the_cdn_before_the_cdn_tags` (prüft Präsenz **und** Reihenfolge vor dem ersten CDN-Asset).

## 21. Approval-Poll: Visibility-Gate + Intervall · S · Risiko: niedrig
- [x] `_pollGlobalApprovals` läuft alle 3 s ohne `document.hidden`-Check (web/static/messages.js:3261, 3274). → **PR #70**
- **Fix:** `if(document.hidden) return;` + Intervall 3 s → 5–10 s; sofortiger Poll bei `visibilitychange`.
- **Akzeptanz:** Im Hintergrund-Tab keine `/api/approval/pending-all`-Requests (Netzwerk-Panel).
- **Ergebnis:** Gate `if(document.hidden) return;` am Anfang von `_pollGlobalApprovals`, Intervall 3 s → **8 s**, `visibilitychange`-Handler pollt beim Zurückkehren sofort (nur wenn der Poll aktiv ist). Browser-Verifikation (Playwright, `document.hidden` überschrieben, `api()`-Aufrufe gezählt): hidden → **0 Calls**, visible → **1 Call**, registriertes Intervall **8000 ms**. Tests: `tests/test_approval_poll_gate.py` (3).

## 22. Doppelten `_startGlobalApprovalPoll` entfernen · S · Risiko: niedrig
- [x] Zwei Definitionen: messages.js:2875 (tot) und 3261 (aktiv); doppeltes `_stop` (2912/3267). Die zweite Definition gewinnt. → **PR #71**
- **Fix:** Toten Block entfernen; sicherstellen, dass nur ein Timer existiert.
- **Akzeptanz:** `grep -c "function _startGlobalApprovalPoll" web/static/messages.js` = 1; Boot startet genau einen Poll.
- **Ergebnis:** Toter Block (56 Zeilen) + verwaiste State-Variablen `_globalApprovalPollTimer`/`_globalApprovalSessionsSeen` entfernt (letztere wurde nur noch von `_clearApprovalPendingForSession` referenziert — die Zeile mit entfernt). `grep -c` = **1** für beide Funktionen, 0 verwaiste Referenzen. Browser-Verifikation: 1 Definition, **1 Timer**, 1 Sofort-Poll, Doppelstart erzeugt **keinen** zweiten Timer. Tests: `tests/test_approval_poll_dedupe.py` (3).

## 39. CI-Gate: `node --check` über alle web/static/*.js · S · Risiko: niedrig
- [x] Kein `node --check` in tests/, scripts/ oder CI; ein SyntaxError killt die ganze UI (Skill `webui-js-parse-failure-triage`). → **PR #72**
- **Fix:** Kleines Script (z. B. `scripts/check_webui_js.py`) + Einbindung in `.github/workflows/ci.yml`.
- **Akzeptanz:** CI schlägt bei absichtlich eingebautem SyntaxError fehl; lokal Exit 0.
- **Ergebnis:** `scripts/check_webui_js.py` prüft alle `web/static/*.js` per `node --check` (Exit 1 + Node-Fehlerausgabe bei Fehlschlag), neuer CI-Job `js-parse` (ubuntu, Node 20, 5 min Timeout). Verifiziert: lokal **23 Dateien OK** (Exit 0); mit absichtlich eingebautem SyntaxError in `icons.js` → **1 von 23 schlägt fehl** (Exit 1, Node-Syntaxfehler ausgegeben), nach Revert wieder grün. Tests: `tests/test_webui_js_parse_gate.py` (4, inkl. CI-Verdrahtung).

---

# P1 — Nächste Welle (mittlerer Aufwand, hoher Nutzen)

## 7. `_prune_expired_sessions()` nicht bei jedem verify · S · Risiko: niedrig
- [x] `verify_session()` ruft prune bei jedem Verify (web/api/auth.py:378-382); prune schreibt die Session-Datei bei abgelaufenen Einträgen.
- **Fix:** Zeitgesteuert (z. B. max. 1×/60 s) statt pro Request.
- **Akzeptanz:** Verify-Pfad ohne Datei-Write im Normalfall.
- **Ergebnis:** `_prune_expired_sessions(force=False)` mit `_PRUNE_INTERVAL_SECONDS = 60`; `force=True` umgeht den Throttle für explizite Aufrufe. Verifiziert: 20 Verifies ohne abgelaufene Einträge → **0 Writes**; mit abgelaufenem Eintrag innerhalb des Fensters → 0 Writes (Eintrag bleibt); nach Ablauf des Fensters → 1 Write + Eintrag entfernt. Tests: `tests/test_session_prune_throttle.py` (3).

## 8. fastapi_bridge: Thread pro Request → bounded ThreadPoolExecutor · M · Risiko: mittel
- [x] `threading.Thread(target=self._run, daemon=True)` pro API-Call (web/api/fastapi_bridge.py:169-170). Unbegrenzt viele Threads bei Last.
- **Fix:** Bounded Executor + Backpressure (429/503 bei Überlast) oder Thread-Reuse.
- **Akzeptanz:** Lasttest mit 100 parallelen Requests → Thread-Zahl gedeckelt, keine Fehler.
- **Ergebnis:** Bounded `ThreadPoolExecutor` (16 Worker) für normale Bridge-Requests; **SSE-Pfade behalten dedizierte Threads**, weil ein Stream-Handler seinen Worker bis `wfile.finish()` hält und ein Pool sonst von wenigen offenen Streams ausgehungert würde (SSE-Liste lokal dupliziert, da `cli.web_server` dieses Modul importiert → Zirkularität; ein Test prüft Listen-Gleichheit). Backpressure: 503 + `Retry-After: 1` ab `_BRIDGE_POOL_QUEUE_LIMIT = 256` anstehenden Requests. Lasttest: **100 parallele Bridge-Requests → 16 Threads statt 100**, 100/100 OK, `_bridge_pool_pending` nach dem Lauf 0. Tests: `tests/test_bridge_pool.py` (4).

## 9. SSE-Chunk-Reads batchen · M · Risiko: mittel
- [x] `await asyncio.to_thread(self._chunks.get)` pro Chunk (web/api/fastapi_bridge.py:114); anyio-Default-Limiter = 40 Threads.
- **Fix:** Mehrere Chunks pro Thread-Aufruf drainen (z. B. `get_nowait`-Schleife nach erstem `get`).
- **Akzeptanz:** SSE-Streams unter Last blockieren den Limiter nicht mehr (Messung).
- **Ergebnis:** `_ResponseWriter.stream()` drainet nach dem ersten blockierenden `get` die Queue per `get_nowait`-Schleife und yieldet den Batch als ein Item; das `_END`-Sentinel wird für die nächste Runde zurückgelegt. Messung: **20-Chunk-Burst → 2 Thread-Hops statt 21**; 10-Chunk-Burst → 1 Yield; Live-Chunks weiterhin in Reihenfolge und vollständig; `finish()` terminiert ohne Hänger. Tests: `tests/test_sse_chunk_batching.py` (4).

## 10. Top-Assets beim Start prekomprimieren · S · Risiko: niedrig
- [x] ui.js/i18n.js/panels.js/style.css/index.html ≈ 350 KB gzip; Kompression kostet pro Request CPU (Item 4).
- **Fix:** Beim Serverstart einmal komprimieren, im RAM halten (keyed mtime).
- **Akzeptanz:** Erster Request nach Start liefert gzip ohne Kompressions-Spike.
- **Ergebnis:** `_precompress_shell_assets()` läuft als Daemon-Thread beim Startup (`on_startup`-Hook, am Dateiende registriert weil die Funktion dort definiert ist) und wärmt den Gzip-Cache für die 28 Shell-Assets aus `sw.js` SHELL_ASSETS. Messung: Kalt-Pass über 28 Assets **74,2 ms** → Warm-Pass **2,2 ms** (72 ms Kompressionsarbeit vom Request-Pfad entfernt), Cache danach 28 Einträge / 894 KB. Fehlende Assets werden übersprungen, Fehler geloggt statt zu crashen. Tests: `tests/test_shell_precompress.py` (4, inkl. Abgleich mit sw.js-Liste und Startup-Hook-Registrierung).

## 11. i18n splitten · M · Risiko: mittel
- [x] i18n.js = 663 KB, 9 Locales (en, it, ja, ru, es, de, zh, pt, ko) in einer Datei; pro Session wird 1 Locale gebraucht.
- **Fix:** Locale-Bundles in separate Dateien (z. B. `i18n/en.js`, `i18n/de.js`), Loader lädt aktive Sprache + en als Fallback (~75 KB statt 663 KB).
- **Akzeptanz:** Initialer JS-Payload sinkt um ~590 KB; Sprachwechsel lädt Bundle nach; Fallback en funktioniert.
- **Ergebnis:** 10 Bundles unter `web/static/i18n/<code>.js` (inkl. `zh-Hant`, das als eigener Top-Level-Locale in der Monolith-Datei steckte); nur **Englisch bleibt inline** (Fallback für jeden Lookup), alle anderen werden per dynamischem `import()` geladen (`LOCALE_LOADERS`). `resolveLocale()` kennt jetzt auch noch nicht geladene Locales (prüft `LOCALES` **oder** `LOCALE_LOADERS`), sonst wäre jeder Wechsel auf Englisch zurückgefallen. `setLocaleAsync`/`switchLang` async; `loadLocale` wendet sofort Englisch an und danach die echte Sprache. Messung: **668.035 B → 79.875 B roh (−88 %)**, **189.802 B → 23.093 B gzip (−88 %)**. Browser-Verifikation (Playwright, SW blockiert): vor dem Wechsel nur `['en']` geladen; `switchLang('de')` → `['en','de']`, `t('offline_title')` = „Verbindung verloren", `de.js` im Netzwerk; `switchLang('ja')` lädt nur `ja.js` nach; fehlender Key liefert den Key selbst. Tests: `tests/test_dashboard_health.py` angepasst (3 Tests lesen jetzt die Bundles statt der Monolith-Datei).
- **Hinweis:** Zwei deutsche Strings trugen bereits vorher Mojibake in der Quelle (`Bereich auswÃ¤hlen`, `Ãœberschreibungen`) — unverändert übernommen, in den Tests als Ist-Zustand dokumentiert.

## 12. panels.js lazy laden · M · Risiko: mittel
- [x] panels.js = 517 KB, lädt immer, obwohl Panels selten geöffnet werden. Skill `lazy-load-panels` existiert; Revert d1f6060 wegen Parse-Kollision.
- **Fix:** Dynamischer Import beim ersten Panel-Wechsel; vorher `node --check`-Gate (Item 39) sicherstellen; IIFE-Wrap beachten.
- **Akzeptanz:** panels.js wird erst beim ersten Panel-Öffnen geladen (Netzwerk-Panel); alle Panels funktionieren.
- **Ergebnis:** Neuer `web/static/panels-loader.js` ersetzt das statische `<script src="/static/panels.js">`. Er installiert einen Platzhalter-`switchPanel`, der beim ersten Aufruf ein `<script>`-Tag injiziert (kein `import()`, weil panels.js ein klassisches Skript mit Top-Level-Funktionen ist) und dann an die echte Implementierung delegiert; Promise wird gecacht (kein Re-Fetch), Fehlschlag ist retrybar, Version-Token bleibt erhalten. **Skill-Voraussetzung erfüllt:** `node --check`-Sweep grün (24 Dateien) **und** in CI erzwungen (Item 39). Browser-Verifikation (Playwright, SW blockiert): **0 panels.js-Requests beim Laden**, 1 nach `switchPanel('settings')`, Panel-Wechsel in beide Richtungen korrekt, zweiter Wechsel lädt **nicht** erneut, 0 JS-Fehler. Payload-Ersparnis: **517 KB** weniger beim Kalt-Load. Tests: `tests/test_lazy_panels_loader.py` (4).

## 13. Nicht-kritische Scripts on-demand laden · M · Risiko: mittel
- [x] browser.js (246 KB), gmail.js (62 KB), discord.js (17 KB), discord-chat.js (45 KB), agents.js (58 KB), swarm.js (28 KB), onboarding.js (45 KB), enhancements.js (52 KB) laden upfront ≈ 550 KB.
- **Fix:** Pro Feature-Gruppe dynamischer Import beim Panel-/Feature-Öffnen.
- **Akzeptanz:** Initialer Payload < 1,5 MB; Features laden bei Bedarf fehlerfrei.
- **Ergebnis:** Neuer `web/static/feature-loader.js` mit Panel→Datei-Map (browser, gmail/mail, discord→[discord.js, discord-chat.js], agents, swarm, onboarding); lädt per injiziertem `<script>` (klassische Skripte, kein `import()`), cacht geladene Dateien, Fehlschlag retrybar, Version-Token bleibt. `enhancements.js` bleibt **eager** (registriert eigenen DOMContentLoaded-Handler). Wrapper um `switchPanel` wird mehrfach installiert, weil `agents.js` die Funktion ebenfalls patcht. **Drei echte Abhängigkeitsfehler gefunden und behoben:** `startDashboardRefresh`/`stopDashboardRefresh`/`loadAgentsDashboard` (aus agents.js) und `loadGmailPanel` (aus gmail.js) wurden in panels.js ungeschützt aufgerufen → jetzt `typeof`-Guards. Browser-Verifikation (Playwright, SW blockiert): **0 lazy-Skripte beim Laden**, `agents.js`/`swarm.js`/`browser.js` laden beim jeweiligen Panel-Öffnen, Panels rendern, **0 JS-Fehler**. Payload-Ersparnis: **~553 KB** beim Kalt-Load. Tests: `tests/test_lazy_feature_scripts.py` (6); `test_browser_qa_contracts.py` + `test_swarm_webui.py` an die Lazy-Struktur angepasst.

## 14. xterm/Prism/KaTeX self-hosten · M · Risiko: mittel
- [x] 9 CDN-Refs (cdn.jsdelivr.net) in index.html; CDN-Ausfall = Terminal/Highlighting/KaTeX tot; zusätzliche DNS/TLS-Latenz.
- **Fix:** Assets nach web/static/vendor/ kopieren, lokale Pfade + SRI-Hashes aktualisieren.
- **Akzeptanz:** Kein externer CDN-Request mehr; alle Features funktionieren lokal.
- **Ergebnis:** Alle CDN-Abhängigkeiten nach `web/static/vendor/` geholt (120 Dateien, 6,9 MB): Prism 1.29.0 (Core, Autoloader, line-numbers, 4 Themes, **42 Sprachdateien**), xterm 5.3.0 + 2 Addons, KaTeX 0.16.22 (CSS, JS, **60 Fonts**), js-yaml 4.1.0, Mermaid 10.9.3, pdfjs-dist 4.9.155 (inkl. Worker). Zusätzlich zu den 9 Tags in index.html waren **weitere CDN-Refs in Laufzeit-Loadern** versteckt: `terminal.js` (xterm-Fallback), `boot.js` (3 Prism-Theme-URLs), `ui.js` (js-yaml, pdfjs, mermaid, katex) — alle umgestellt. Prism-Autoloader löst Sprachen jetzt aus `/static/vendor/prism/components/` auf (verifiziert). Preconnect-Hint aus Item 15 entfernt (öffnete nur noch einen nutzlosen Socket). SRI-Attribute entfallen, da die Dateien nun aus dem eigenen Repo kommen. Browser-Verifikation (Playwright, SW blockiert): **0 cdn.jsdelivr.net-Requests**; Prism-Highlighting erzeugt Tokens, KaTeX rendert (inkl. Font-Nachladen), Mermaid rendert SVG, js-yaml lädt, xterm/FitAddon/WebLinksAddon initialisiert; **0 JS-Fehler**. Tests: `tests/test_vendor_self_hosting.py` (6); `test_dashboard_frontend_contract.py` angepasst (Preconnect-Test → „kein CDN-Hint mehr").

## 19. SW: stale-while-revalidate für Shell-Assets · M · Risiko: mittel
- [x] sw.js:162-179 network-first für Shell-Assets → jeder Load wartet auf Netzwerk.
- **Fix:** Cache-first + Hintergrund-Refresh (stale-while-revalidate) für versionierte Assets; Update via Version-Bump.
- **Akzeptanz:** Wiederholter Load liefert Shell aus Cache (<100 ms), Refresh im Hintergrund.
- **Ergebnis:** Shell-Assets nutzen jetzt stale-while-revalidate: `caches.match()` zuerst, bei Treffer sofort ausliefern und den Netzwerk-Refresh per `event.waitUntil()` im Hintergrund laufen lassen; ohne Cache-Eintrag weiterhin Netzwerk mit 503-Offline-Fallback. **Sicherheit:** Alle Shell-URLs tragen `?v=<webui-version>`, und der Token enthält bei dirty worktree die newest-mtime — ein geänderter Hotfix erzeugt also eine neue URL (Cache-Miss) statt eine veraltete Datei auszuliefern; ein Test sichert, dass jeder JS/CSS-Eintrag versioniert bleibt. Browser-Verifikation (Playwright, SW aktiv): 2. und 3. Load liefern **4/4 Shell-Assets aus dem Cache** (0 B Transfer, 2–6 ms statt Netzwerk-Roundtrip), SW kontrolliert die Seite, 0 JS-Fehler. Tests: `tests/test_sw_shell_strategy.py` (4).

## 20. SW-Precache verkleinern + Update-Prompt · M · Risiko: niedrig
- [x] sw.js:24-58 precached ~3,5 MB per `addAll` beim Install; kein Update-Prompt für neue Versionen.
- **Fix:** Precache auf kritisches Minimum; Rest on-demand cachen; `updatefound`-Prompt einbauen.
- **Akzeptanz:** Install-Precache < 1 MB; neue Version zeigt Update-Hinweis.
- **Ergebnis:** Precache von 26 auf **19 Einträge** reduziert (Panel-CSS `agents*.css`, `gmail-panel.css`, `discord*.css`, `xterm.css`, `swarm.css` entfernt — sie werden beim Panel-Öffnen geladen und dann vom Fetch-Handler gecacht). Update-Prompt in der SW-Registrierung: `updatefound`/`statechange === 'installed'` → `showConfirmDialog` mit „Reload"/„Later" (Fallback auf `window.confirm`, falls ui.js noch nicht geladen ist); Flag `window.__sidekickServiceWorkerUpdateReady`. Verifiziert im Browser: SW kontrolliert die Seite, **20 Cache-Einträge**, Shell bootet nach Reload vollständig aus dem Cache (ui.js/messages.js/boot.js geladen, Styles angewendet), Panel-CSS lädt beim Panel-Öffnen nach, 0 JS-Fehler.
- **Einschränkung (nicht erreicht):** Das Akzeptanzkriterium „Install-Precache < 1 MB" ist **nicht erfüllt** — erreicht wurden 1,99 MB raw / **0,47 MB gzip** (vorher 2,08 MB / 0,49 MB). Die verbleibenden ~94 KB Ersparnis sind das Maximum, ohne die Boot-Kette zu beschädigen: `ui.js` (139 KB gz) und `style.css` (93 KB gz) sind render- bzw. boot-kritisch und müssen im Precache bleiben. Eine echte <1-MB-Grenze wäre nur über Item 16 (Minify) oder Item 18 (style.css splitten) erreichbar. Gemessen wird hier gzip (der Precache lädt über HTTP mit gzip), das Kriterium im Backlog ist unklar auf raw oder gzip bezogen.

## 26. `api()`: Default-Timeout (AbortSignal.timeout) · S–M · Risiko: niedrig
- [x] `api()` (web/static/workspace.js:1) hat keinen Default-Timeout; nur `_workspaceApiWithTimeout` für Workspace-Pfade. Hängende Requests stapeln sich.
- **Fix:** Default-Timeout (z. B. 30 s) via `AbortSignal.timeout`, überschreibbar per Option; Timeout-Fehler klar melden.
- **Akzeptanz:** Hängender Request bricht nach 30 s ab; UI bleibt bedienbar.
- **Ergebnis:** `API_DEFAULT_TIMEOUT_MS = 30000`; `api()` legt einen `AbortController` an, wenn der Aufrufer kein eigenes `signal` mitgibt (Caller-Signal gewinnt). Überschreibbar per `opts.timeoutMs`, abschaltbar mit `0`. Abort wird **nicht** retried (sonst dreifache Wartezeit) und als `TimeoutError` mit `Request timed out after <ms> ms` plus `timeoutMs`/`url` geworfen; Timer wird im `finally` immer geräumt. Browser-Verifikation (Playwright): hängender Request bricht nach **801 ms** bei `timeoutMs: 800` ab (`name: 'TimeoutError'`, klare Meldung), normale Requests laufen unverändert, Caller-Signal wird respektiert. Tests: `tests/test_api_default_timeout.py` (5).

## 27. In-Flight-Dedupe ausweiten · S–M · Risiko: niedrig
- [x] ctx-Poll (5 s, ui.js:5051) und Streaming-Poll (5 s, sessions.js:2663) können sich mit Nutzeraktionen überlappen; `_sessionListInFlight`-Guard existiert nur für die Session-Liste.
- **Fix:** Generischer In-Flight-Guard pro Endpoint (Promise-Map).
- **Akzeptanz:** Keine doppelten parallelen Requests desselben Endpoints (Netzwerk-Panel).
- **Ergebnis:** `_dedupeInFlight(key, factory)` in sessions.js: Promise-Map pro Key, identische Promise für alle überlappenden Aufrufer, Freigabe bei Settle (nur wenn der Eintrag noch der eigene ist — ein späterer Aufruf wird nicht gelöscht), synchrone Throws werden zu einer Rejection statt zu einem hängenden Eintrag. Angewendet auf den **ctx-Poll** (`ctx:<sid>`, mit `typeof`-Guard weil ui.js vor sessions.js lädt) und den **Streaming-Poll** (`session-list`). Browser-Verifikation (Playwright, Requests über Resource-Timing gezählt): **5 parallele Aufrufe mit gleichem Key → 1 Netzwerk-Request**, alle Aufrufer bekommen dieselbe Promise-Instanz, Eintrag nach Abschluss freigegeben, verschiedene Keys unabhängig (2 Requests), Rejection propagiert und gibt frei. Tests: `tests/test_in_flight_dedupe.py` (5).

## 28. /api/sessions-Cache TTL erhöhen + ETag · S · Risiko: niedrig
- [x] `_SESSION_LIST_CACHE_TTL = 2.0` (web/api/models.py:1562) bei 5-s-Poll.
- **Fix:** TTL 3–5 s; ETag/304 für unveränderte Listen.
- **Akzeptanz:** Poll-Kosten sinken; keine sichtbare Verzögerung bei Session-Änderungen.
- **Ergebnis:** TTL 2,0 → **4,0 s** (deckt den 5-s-Poll ab). ETag/304 auf `/api/sessions`: `_payload_etag()` (SHA-256 über kanonisches JSON mit sortierten Keys — stabil über Prozesse/Neustarts, nicht zeitbasiert) + `_etag_matches()` (RFC 7232, Weak/Strong/Liste/`*`). **Wichtig:** Der erste Patch landete in `web/api/routes.py`, aber `/api/sessions` hat eine **native FastAPI-Route** in `cli/web_server.py` — die Legacy-Route wird nie erreicht; Patch dorthin verschoben und den routes.py-Patch zurückgenommen. Messung (TestClient, echtes Profil): **2.653.294 B → 304 mit 0 B**, ETag stabil über identische Requests, stale ETag → voller Body. Zusätzlich die Security-Header (`nosniff`, `DENY`, `same-origin`) auf der nativen Route ergänzt — sie fehlten dort schon vorher (per `git stash` gegen Baseline verifiziert), die Legacy-Route setzte sie. Tests: `tests/test_sessions_etag.py` (5).

## 30. Agent-Health-Poll gaten · S · Risiko: niedrig
- [x] `pollAgentHealth` prüft nur `visibilityState`, nicht Panel-Sichtbarkeit (ui.js:6063); System-Health prüft beides (ui.js:5969).
- **Fix:** Panel-/Alert-Sichtbarkeit als Gate (oder Intervall adaptiv).
- **Akzeptanz:** Kein `/api/health/agent`-Poll, wenn weder Panel sichtbar noch Alert aktiv.
- **Ergebnis:** `_agentHealthShouldPoll()` = `visibilityState === 'visible'` **und** (`_agentHealthPanelIsVisible()` **oder** `_agentHealthAlertIsActive()`). Das Banner ist global, deshalb reicht Panel-Sichtbarkeit allein nicht — ein aktiver Alert muss weiter gepollt werden, damit er sich selbst wieder schließen kann. `pollAgentHealth` und `startAgentHealthMonitor` nutzen das Gate; `switchPanel` ruft `_syncAgentHealthMonitorVisibility()` auf (analog zum bestehenden System-Health-Muster — **nicht** über ein erfundenes Event, nachdem ich geprüft hatte, dass `sidekick-panel-change` nirgends gefeuert wird). Browser-Verifikation (Playwright, Requests gezählt): Chat-Panel ohne Alert → **0 Requests** trotz erzwungenem Poll-Aufruf; mit sichtbarem Alert → 1 Request; nach Cleanup wieder geschlossen. Tests: `tests/test_agent_health_gate.py` (4).

## 40. Doppelte Funktionsdefinitionen entfernen · M · Risiko: mittel
- [x] commands.js: `cmdStatus` (1276/3524) u. a. 11 Namen doppelt; spaces.js `renderSpacesPanel` (799/1207); panels.js `closeKanbanTaskDetail` (1938/2565); terminal.js `openSplitTerminal`/`closeTerminalPane`/`toggleSplitTerminal` (882/1028 ff.).
- **Fix:** Jeweils ältere Kopie entfernen (Diff prüfen, welche aktiv ist — die letzte Definition gewinnt), Verhalten verifizieren.
- **Akzeptanz:** Keine doppelten Top-Level-Definitionen; Smoke-Tests grün.
- **Ergebnis:** Tatsächlich **43 Duplikate** (nicht 11): commands.js 38, spaces.js 1, panels.js 1, terminal.js 3. Analyse vor dem Löschen: 39 Paare byte-identisch, 4 unterschiedlich. Entfernt wurden die **toten früheren** Kopien (36 identische in commands.js, 3 in terminal.js, je 1 in spaces.js/panels.js) = **1232 Zeilen**. **Wichtiger Befund:** Bei `cmdMcp` und `cmdSubagents` war die *spätere* Kopie die **schwächere** — die erste (tote) Kopie enthielt die `toggle`/`delete`/`refresh`/`tools`- bzw. `open`/`loadSubagentsPanel`-Pfade, die zur Laufzeit nie erreichbar waren. Dort wurde die **zweite** Kopie entfernt, wodurch `/mcp toggle`, `/mcp delete` und `/subagents open` wieder funktionieren (echter Funktionsverlust behoben, nicht nur Aufräumen). Verifiziert: alle 678 Funktionen vor/nach identisch vorhanden (kein Verlust), 0 Duplikate, `node --check` über 25 Dateien grün, Browser-Test (Playwright): alle deduplizierten Funktionen definiert, `cmdMcp`/`cmdSubagents`/`cmdStatus` antworten, `closeKanbanTaskDetail` wirft nicht, 37 Spaces geladen, **0 JS-Fehler**. Tests: `tests/test_duplicate_functions.py` (5); 4 bestehende Tests angepasst (sie zählten die entfernten Duplikate).

## 45. Boot parallelisieren · S · Risiko: niedrig
- [x] `await _syncGameModeStateFromServer()` läuft sequenziell nach Settings (boot.js:1933).
- **Fix:** Parallel starten (Promise.all mit Settings/Profile).
- **Akzeptanz:** Boot-Zeit sinkt messbar; keine Race-Fehler.
- **Ergebnis:** Der Game-Mode-Sync startet jetzt **vor** dem Settings-Await (`const _gameModeSync = _syncGameModeStateFromServer()` direkt am Blockanfang) und wird am Ende des Blocks weiterhin `await`ed, damit der Button-Stand vor Boot-Ende korrekt ist. Der Settings-Fetch lief bereits parallel (`_bootSettingsReady`), nur der Game-Mode-Sync war sequenziell. Messung (Playwright, Resource-Timing): beide Requests starten bei **170 ms** (vorher startete game-mode erst nach Settings-Ende bei ~1098 ms), `overlapped: true`; Boot-State korrekt (`_gameModeEnabled` gesetzt, `_sendKey`, `_botName`), 0 JS-Fehler. Tests: `tests/test_boot_parallel.py` (3).

## 47. Ladezeit-Budget als Smoke-Test · S–M · Risiko: niedrig
- [ ] browser_webui_smoke.py existiert, prüft aber kein Ladezeit-Budget.
- **Fix:** DOMContentLoaded/TTI-Messung + Budget (z. B. < 2 s lokal) als Check.
- **Akzeptanz:** Check schlägt bei Budget-Verletzung fehl.

## 48. Browser-Smoke in CI verdrahten · S–M · Risiko: mittel
- [ ] browser_webui_smoke.py hat bereits Console-Error-Check (Zeile 1983), wird aber in CI nicht ausgeführt (nur Text-Assertions in pytest).
- **Fix:** CI-Job (z. B. ubuntu + Playwright) für den Browser-Smoke; oder als optionaler Job.
- **Akzeptanz:** CI führt Browser-Smoke aus; Fehler blockieren Merge.

## 49. Perf-Marks im Frontend · S–M · Risiko: niedrig
- [x] 0 `performance.mark/measure` im Frontend.
- **Fix:** Marks für Boot, Session-Load, Render, SSE-Reconnect; Debug-Panel/Console-Ausgabe.
- **Akzeptanz:** `performance.getEntriesByType('measure')` liefert Werte; Baseline dokumentiert.
- **Ergebnis:** `_perfMark`/`_perfMeasure` in ui.js (try/catch-geguarded, `typeof performance.mark!=='function'`-Check — ein fehlendes User-Timing-API darf den instrumentierten Pfad nie brechen), exportiert als `window._perfMark`/`_perfMeasure`. Instrumentiert: **Boot** (`sidekick:boot`, boot.js), **Session-Load** (`sidekick:session-load`, sessions.js, im `finally`), **Render** (`sidekick:render`, ui.js, im `finally` — ein Throw verliert die Messung nicht), **SSE-Connect** (`sidekick:sse-connect`, sessions.js, misst bis zum `open`-Event). Messung (Playwright): `performance.getEntriesByType('measure')` liefert Boot **2386 ms**, Session-Load **1557 ms**, Render **3–41 ms**; Marks `sidekick:boot:start`, `sidekick:render:start`, `sidekick:session-load:start` vorhanden; 0 JS-Fehler. Tests: `tests/test_perf_marks.py` (6).

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
