# Kanban-orchestriert im WebUI-Chat

## Ziel

Wenn der Nutzer im normalen WebUI-Chat eine imperative Formulierung wie
„kanban orchestriert“, „Kanban orchestrieren“ oder „orchestriere das über das
Kanban-Board“ verwendet, bedeutet das eindeutig: Das aktive WebUI-Kanban-Board
ist die Orchestrierungsquelle. Der Agent legt dort valide Aufgaben und
Abhängigkeiten an; der vorhandene Gateway-Dispatcher führt die Ready-Aufgaben
aus.

## Ist-Zustand und Ursache

Der normale WebUI-Agent lädt zwar den allgemeinen CLI-Toolkatalog, aber die
Kanban-Tools werden durch `tools/kanban_tools.py` nur für Kanban-Worker oder
Profile mit explizitem `kanban`-Toolset freigegeben. Ein normaler WebUI-Chat
kann die Absicht daher nicht zuverlässig in Board-Aktionen übersetzen.

Zusätzlich beschreibt `runtime/prompt_builder.py` nur den Lifecycle eines
bereits gestarteten Kanban-Workers. Die WebUI-Orchestrator-Semantik fehlt.
Schließlich verwendet der Tool-Handler seine direkte Kanban-DB-Verbindung,
statt den thread-lokalen Workspace-Kontext des WebUI-Requests zu übernehmen.
Damit kann eine freigeschaltete Aktion auf der globalen statt auf der im
WebUI aktiven Board-Datenbank landen.

## Design

### 1. Deterministischer Session-Trigger

Eine kleine, pure Hilfe in `web/api/kanban_orchestration.py` normalisiert
Whitespace und Groß-/Kleinschreibung und erkennt nur imperative Varianten mit
dem Kanban-Begriff und einer Orchestrierungs-Verbform. Die bloße Aussage
„Die Kanban-Orchestrierung funktioniert nicht“ aktiviert den Modus nicht.

Bei einem Treffer ergänzt die WebUI-Route das bestehende Session-Toolset um
`kanban` und persistiert die Liste. Bestehende Toolsets bleiben erhalten. Das
Opt-in gilt für Folgefragen derselben Session; es verändert weder das globale
Profil noch andere Chats. Die bestehende Session-Toolset-API kann den Zustand
weiterhin löschen oder überschreiben.

### 2. Guard und WebUI-Orchestrator-Prompt

Der Kanban-Guard akzeptiert zusätzlich zum bestehenden Worker-/Profilpfad ein
Session-Opt-in aus dem aktuellen `SIDEKICK_SESSION_KEY`. Der Lookup ist
request-/sessionbezogen und darf keine globale Freischaltung erzeugen.

Wenn Kanban-Board-Tools geladen sind, aber der Prozess kein Dispatcher-Worker
ist, erhält der Agent einen eigenen WebUI-Orchestrator-Block:

- „kanban orchestriert“ bedeutet aktives WebUI-Board, nicht `delegate_task` und
  nicht nur eine CLI-Antwort.
- Zuerst das aktive Board mit `kanban_list` prüfen.
- Für jede auszuführende Arbeit `kanban_create` mit konkretem Assignee und
  klarer Beschreibung verwenden.
- Mit `parents` oder `kanban_link` Abhängigkeiten ausdrücken.
- Keine Subtasks selbst erledigen; der Board-Dispatcher übernimmt die
  Ausführung.
- Bei fehlendem Assignee oder fehlendem Board-Kontext nicht raten, sondern
  einen klaren Fehler bzw. eine Rückfrage liefern.

Die bestehende Worker-Guidance bleibt unverändert und wird nur für Prozesse
mit `SIDEKICK_KANBAN_TASK` verwendet.

### 3. Aktives WebUI-Board als Datenquelle

`tools/kanban_tools.py` verwendet bei WebUI-Ausführung die vorhandene
thread-lokale Workspace-Kanban-Verbindung aus `web/api/kanban_bridge.py`.
Dadurch werden `kanban_list`, `kanban_create` und `kanban_link` gegen dieselbe
Workspace-/Board-Auflösung ausgeführt wie die WebUI-Board-API. Außerhalb des
WebUI-Threads bleibt die bestehende globale/Worker-Auflösung erhalten.

Bei vorhandenem WebUI-Kontext gibt es keinen stillen Fallback auf eine andere
globale Board-Datenbank. Fehler werden als strukturierte Tool-Fehler an das
Modell zurückgegeben.

## Betroffene Dateien

- Neu: `web/api/kanban_orchestration.py`
- Ändern: `web/api/routes.py`
- Ändern: `tools/kanban_tools.py`
- Ändern: `runtime/prompt_builder.py`
- Ändern: `run_agent.py`
- Neu: `tests/test_kanban_orchestration.py`

Die vorhandenen Änderungen an `cli/config.py`, `cli/kanban.py`,
`cli/kanban_db.py`, `runtime/gateway/run.py`, `web/api/dispatcher.py` und
`web/api/kanban_bridge.py` werden nicht in diesen Scope aufgenommen.

## Verifikation

Der neue Testblock deckt ab:

1. positive Trigger-Varianten und negative reine Fehler-/Substantivphrasen;
2. Session-Toolset-Merge mit Erhalt bestehender Tools;
3. Guard-Verhalten für Worker, Profil-Opt-in, Session-Opt-in und normalen
   Chat;
4. Verbindung des Tool-Handlers mit dem aktiven Workspace-Board;
5. getrennte WebUI-Orchestrator-Guidance und unveränderte Worker-Guidance.

Danach laufen die fokussierten Kanban-/Dashboard-Regressionen, ein
Python-Compile-Check und `git diff --check`. Erfolg wird erst nach diesen
Checks behauptet.

## Nicht im Scope

- keine neue Dispatcher-Implementierung;
- keine Änderung der globalen `max_spawn`-Cap;
- kein automatisches Starten eines zweiten Dispatchers;
- kein dauerhaftes Aktivieren von Kanban für alle normalen Chats;
- keine Änderung an der Worker-Lifecycle-Semantik.
