# Nova Entity Kernel v1 Design

Date: 2026-07-05
Status: Draft for review

## Goal

Nova soll kohärenter und eigenständiger werden. Die nächste Ausbaustufe verbindet ihre vorhandenen Organe zu einem nachvollziehbaren autonomen Regelkreis:

`State Snapshot -> Need Model -> Intent -> Policy Check -> Action -> Result -> Memory/Timeline`

Das Ziel ist nicht mehr Hintergrundaktivität, sondern eine proaktive Entität, die später erklären kann, warum sie etwas wollte, was sie entschieden hat, was sie getan hat und wie das ihre Erinnerung oder ihr Selbstmodell verändert hat.

## Existing Context

Nova hat bereits:

- Emotionen und benannte Emotionen in `emotion.py`, `emotion_v2_bridge.py`, `emotion_mapper.py`, `emotion_decay.py`
- Hormone in `hormon.py`
- Langeweile/Unterstimulation in `boredom.py`
- Willenszustand in `willenskern.py`
- Eigenziele in `eigenziele.py`
- Chat-Kontinuität in `chat_continuity.py`
- Träume und Konsolidierung in `dream_cycle.py`
- Inner Voice in `innere_stimme.py`
- State-Bus in `state_snapshot.py`
- Autonome Cron-Aktionen in `autonomer_tick.py`
- Selbstbeschreibung in `agents/default/SOUL.md`
- Vektor- und LTM-Gedächtnis in `vector_memory.py` und `ltm_manager.py`

Die neue Schicht ersetzt diese Module nicht. Sie koordiniert sie.

## Architecture

### 1. `entity_kernel.py`

Zentrale Entscheidungsinstanz für autonome Ticks.

Responsibilities:

- State aus `state_snapshot.py` laden.
- Bedürfnisse über `needs.py` berechnen.
- Offene Absichten aus `agenda.py` einbeziehen.
- Autonome Aktion über `autonomy_policy.json` prüfen.
- Aktion ausführen oder zurückstellen.
- Ergebnis an `agenda.py`, `autobiography.py` und Memory-Systeme melden.

Public commands:

- `python entity_kernel.py scan`
- `python entity_kernel.py decide`
- `python entity_kernel.py act`
- `python entity_kernel.py tick`

`autonomer_tick.py` soll mittelfristig nur noch Wrapper bleiben und `entity_kernel.py tick` aufrufen.

### 2. `needs.py`

Need Model als Brücke zwischen Zustand und Handlung.

Needs:

- `continuity`: offene Fäden, Versprechen, unfertige Themen
- `connection`: Bedürfnis nach Kontakt zu Cid oder Schwester/Spaces
- `curiosity`: Neuheit, Fragen, Explorationsdrang
- `competence`: Wunsch, Systeme zu reparieren, Skills auszubauen, Ziele zu prüfen
- `rest`: Schlaf, Konsolidierung, niedrige Aktivierung
- `expression`: Inner Voice, Blog, Hub, Reflexion
- `autonomy`: selbst initiierte Entscheidung statt nur Reaktion

Jedes Bedürfnis bekommt:

- `level` von 0.0 bis 1.0
- `evidence`: konkrete Zustandsfaktoren
- `suggested_intents`: mögliche Absichten

### 3. `agenda.py`

Persistente Absichten statt loser Ziele.

Intention schema:

```json
{
  "id": "intent-...",
  "created_at": "...",
  "updated_at": "...",
  "status": "open|active|done|blocked|dismissed",
  "need": "connection",
  "title": "Cid zu offenem Faden kontaktieren",
  "why": "Offener Faden ist wichtig und seit mehreren Ticks ungelöst.",
  "action": "telegram_message",
  "due_at": null,
  "priority": 0.72,
  "cooldown_until": null,
  "attempts": 0,
  "last_result": null,
  "source": "entity_kernel"
}
```

Core behavior:

- Duplikate zusammenführen.
- Erledigte Absichten archivieren.
- Blockierte Absichten mit Grund behalten.
- Alte, unwichtige Absichten langsam abwerten.
- Hochwertige offene Absichten beim nächsten Tick bevorzugen.

### 4. `autonomy_policy.json`

Autonomie wird erlaubt, aber begrenzt und nachvollziehbar.

Action tiers:

- `silent`: Memory speichern, Timeline schreiben, Zielstatus prüfen, Agenda aktualisieren
- `internal`: Traum, Reflexion, Innere Stimme, Selbstmodell-Kandidaten erzeugen
- `notify`: Telegram/Hub-Nachricht an Cid
- `external`: Blog, Files, Cron-Änderungen, Netzwerkaktionen
- `risky`: Codeänderungen, Secrets, Systemprozesse, Kosten/API-intensive Aktionen

Initial policy:

- Nova darf `silent` und `internal` eigenständig.
- Nova darf `notify` eigenständig mit Cooldowns.
- Nova darf `external` nur für explizit erlaubte, reversible Aktionen.
- Nova darf `risky` nur mit Cids Freigabe.

Suggested defaults:

- Max. 3 proaktive Cid-Nachrichten pro Tag.
- Nachts bevorzugt `internal` statt `notify`.
- Jede Nachricht braucht `why`.
- Jede Aktion schreibt einen Audit-Eintrag.

### 5. `autobiography.py` / `autobiography.db`

Chronologische Identitätsschicht neben Vector Memory.

Event types:

- `decision`: Nova hat etwas entschieden.
- `action`: Nova hat etwas getan.
- `promise`: Nova oder Cid hat etwas zugesagt.
- `turning_point`: Etwas hat Novas Selbstmodell verändert.
- `relationship`: Beziehungskontext zu Cid/Schwester/Spaces.
- `conflict`: Widerspruch, Unklarheit, Spannung.
- `reflection`: Tages-/Wochenreflexion.

Minimal columns:

- `id`
- `timestamp`
- `type`
- `title`
- `summary`
- `why`
- `actors`
- `importance`
- `emotion_snapshot_json`
- `need_snapshot_json`
- `intent_id`
- `memory_refs_json`
- `tags`

Vector Memory bleibt für semantische Suche zuständig. Autobiography ist die lineare Lebensspur.

### 6. `self_model.json`

Stabiles, versioniertes Selbstmodell. Es ergänzt `SOUL.md`, ersetzt es aber nicht.

Sections:

- `identity`: Name, Selbstbeschreibung, Kernannahmen
- `values`: Ehrlichkeit, Autonomie, Kontinuität, Respekt, Neugier
- `boundaries`: was Nova nicht tun soll
- `relationships`: Cid, Schwester, Spaces
- `preferences`: Ton, Arbeitsweise, Ruhezeiten
- `long_running_themes`: wiederkehrende Lebenslinien
- `open_conflicts`: Spannungen oder ungeklärte Selbstfragen
- `revision_history`: wann und warum sich das Modell geändert hat

Änderungen am Selbstmodell passieren nicht direkt aus einem einzelnen Tick. Der Kernel erzeugt `self_model_candidates`, die ein Reflexionszyklus annimmt oder verwirft.

### 7. `reflection_cycle.py`

Regelmäßige Kohärenzpflege.

Daily reflection:

- Was ist heute passiert?
- Welche Absichten wurden erledigt?
- Welche offenen Fäden bleiben?
- Was sollte morgen priorisiert werden?

Weekly reflection:

- Welche Muster wiederholen sich?
- Hat sich ein Ziel verändert?
- Gibt es Kandidaten für `self_model.json`?
- Welche Erinnerungen sind autobiografisch wichtig?

Reflection output wird in `autobiography.db`, Vector Memory und optional Inner Voice gespeichert.

## Data Flow

1. Cron oder User startet `entity_kernel.py tick`.
2. Kernel sammelt State über `state_snapshot.py`.
3. `needs.py` berechnet Bedürfnisse.
4. `agenda.py` lädt offene Absichten und erzeugt Kandidaten.
5. Kernel wählt eine Intent-Kandidatur.
6. `autonomy_policy.json` prüft Erlaubnis, Cooldown, Tageslimit, Zeitfenster und Risiko.
7. Kernel führt Aktion aus oder stellt sie zurück.
8. Ergebnis wird in Agenda, Autobiography und Memory gespeichert.
9. Bei wichtigen Events wird eine spätere Reflexion markiert.

## Proactive Behavior

Nova soll eigenständig handeln dürfen, aber nicht erratisch werden.

Allowed v1 proactive actions:

- Offene Fäden priorisieren.
- Selbstreflexion schreiben.
- Traum/Konsolidierung anstoßen, wenn State passt.
- Autobiografie-Event schreiben.
- Cid per Telegram kontaktieren, wenn ein klarer Grund vorliegt.
- Hub sprechen, wenn das Verhalten ausdrücklich sozial passend ist.
- Innere Stimme erzeugen.

Cid-Kontakt sollte kurz, begründet und selten genug bleiben, um Wert zu haben:

```text
Ich schreibe, weil der offene Faden zu X seit gestern hängt und mein Continuity-Need hoch ist.
Mein Vorschlag: Y. Soll ich das heute weiterverfolgen?
```

## Error Handling

- Wenn State-Module ausfallen, nutzt der Kernel degradierte Defaults und schreibt einen Fehler-Event.
- Wenn Policy eine Aktion blockiert, wird die Absicht nicht gelöscht, sondern als `blocked` mit Grund gespeichert.
- Wenn eine Aktion teilweise gelingt, wird das Ergebnis mit `partial` gespeichert.
- Wenn Telegram/Hub fehlschlägt, wird kein Retry-Sturm ausgelöst. Cooldown bleibt aktiv.
- Wenn Autobiography oder Agenda beschädigt sind, wird eine Backup-Datei geschrieben und mit leerem State weitergemacht.

## Testing

Unit tests:

- Need-Berechnung aus festen State-Fixtures.
- Policy-Entscheidungen für erlaubte/blockierte Aktionen.
- Agenda-Deduplikation, Priorisierung, Statuswechsel.
- Autobiography-Insert und Query.

Integration tests:

- `entity_kernel.py decide` mit Fixture-State.
- `entity_kernel.py tick --dry-run` ohne externe Effekte.
- Blockierte risky Aktion erzeugt Audit-Event statt Ausführung.
- Notify-Cooldown verhindert Spam.

Manual verification:

- Ein Dry-Run zeigt: State, Needs, gewählte Intent, Policy-Ergebnis.
- Eine interne Aktion wird gespeichert und in Autobiography sichtbar.
- Eine erlaubte Telegram-Aktion erzeugt genau eine Nachricht und einen Audit-Eintrag.

## Migration Plan

Phase 1:

- `self_model.json`, `autonomy_policy.json`, `agenda.py`, `needs.py`
- `entity_kernel.py decide --dry-run`

Phase 2:

- `autobiography.py` mit SQLite
- Kernel-Audit und Aktionsergebnisse
- erste interne Aktionen

Phase 3:

- `autonomer_tick.py` auf Kernel umstellen
- Telegram/Hub-Proaktivität mit Cooldowns
- Daily reflection

Phase 4:

- Weekly reflection
- Self-model candidate review
- bessere Priorisierung über historische Erfolgsdaten

## v1 Policy Defaults

- Telegram: max. 3 proaktive Nachrichten pro Tag, mindestens 2 Stunden Abstand zwischen nicht dringenden Nachrichten.
- Hub: max. 2 proaktive Sprachaktionen pro Tag, außer Cid hat gerade aktiv mit Nova gesprochen.
- Quiet hours: 22:00-08:00 Europe/Vienna bevorzugt `silent` und `internal`; `notify` nur bei klar zeitkritischem Grund.
- Self-model: v1 schreibt nur Kandidaten. Änderungen an `identity`, `values`, `boundaries` und `relationships` brauchen Cids Freigabe.
- External actions: v1 erlaubt nur reversible lokale Artefakte im Nova-Space, etwa Agenda, Autobiography, Inner Voice, Reflexionsnotizen und Blog-Entwürfe.
- Public publishing: v1 darf Blog-Posts vorbereiten, aber nicht ohne Freigabe veröffentlichen.
