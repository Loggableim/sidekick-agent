# Codex Comparison — Sidekick Code-Map & Feature-Gap Analyse

Erstellt: 10.05.2026
Basis: `C:\Users\logga\sidekick\static\`

---

## 1. Code-Map

### HTML-Struktur (`index.html` — 1409 Zeilen)

| Struktur | Zeilen | Zweck |
|----------|--------|-------|
| `<header class="app-titlebar">` | 63-86 | Titelzeile mit Sidekick-Logo, Hamburger |
| `<nav class="rail">` | 88-106 | Desktop-Icon-Leiste (Chat/Tasks/Kanban/Skills/...) |
| `<aside class="sidebar">` | 107-125 | Mobile Sidebar (gleiche Nav + Panels) |
| `<div class="sidebar-nav">` | 109-125 | Sidebar-Navigation-Buttons |
| `#panelChat` | 127-138 | Chat-Panel mit Session-Suche |
| `#panelTasks` | 140-149 | Cron-Jobs |
| `#panelKanban` | 151-179 | Kanban-Board |
| `#panelSkills` | 181-190 | Skills |
| `#panelMemory` | 192-197 | Memory |
| `#mainChat` / `.messages` / `#msgInner` | ~330-490 | Chat-Nachrichten |
| `.composer` / `#msg` | ~500-660 | Input-Textarea + Toolbar |
| `#settingsPane*` | ~800-1200 | Einstellungen |
| Evey Tools Panel | ~267-290 | Neues Evey-Dashboard |

### CSS-Variablen-System (`style.css` — ~5200 Zeilen)

```css
:root                { --accent: #FFD700; --bg: #0D0D1A; --text: #FFF8DC; ... }
:root.dark           { --bg:#0D0D1A; --sidebar:#141425; --accent:#FFD700; ... }
:root:not(.dark)     { --bg:#FEFCF7; --sidebar:#FAF7F0; --accent:#B8860B; ... }

/* Skins überschreiben nur --accent* + --accent-bg* Variablen */
:root[data-skin="ares"]      { --accent: #C0392B; ... }
:root[data-skin="mono"]      { --accent: #666666; ... }
:root[data-skin="poseidon"]  { --accent: #0369A1; ... }

/* Font-Size via data-font-size="small|large" */
:root[data-font-size="small"] { font-size: 12px; }
:root[data-font-size="large"] { font-size: 16px; }
```

**Wichtige CSS-Klassen:**
- `.app-titlebar` — 40px+ Titlebar
- `.rail` / `.rail-btn` — Linke Icon-Navigation
- `.sidebar` / `.sidebar-nav` — Linke Panel-Seitenleiste
- `.panel-view` / `.panel-view.active` — Panel-Container
- `.messages` / `.msg-body` / `.msg-assistant` — Nachrichten
- `.composer` / `#msg` — Input-Bereich
- `.reasoning-accordion` — (NEU) Thinking-Accordion
- `.action-chips` / `.action-chip` — (NEU) Schnellaktionen

### JS-Module (key)

| Modul | Größe | Key-Funktionen |
|-------|-------|----------------|
| `ui.js` | ~7200 Z | `renderMessages()`, `setBusy()`, `addCopyButtons()`, `loadDiffInline()`, Message-Rendering |
| `messages.js` | ~2500 Z | `send()`, Stream-Handling, `S.state`, `S.pendingFiles` |
| `commands.js` | ~1000 Z | `COMMANDS[]`, `parseCommand()`, `cmdReasoning()`, `_messageHasReasoningPayload()` |
| `panels.js` | ~1800 Z | `switchPanel()`, Panel-Lifecycle |
| `boot.js` | ~1600 Z | Init, Theme, Reasoning-Chip, Event-Binding |
| `workspace.js` | ~400 Z | File-Tree, Workspace-Navigation |
| `sessions.js` | ~1500 Z | Session-List, Filter, Render |
| `terminal.js` | ~400 Z | xterm.js Terminal-Integration |
| `enhancements.js` | ~800 Z | Zusätzliche Features |
| `power.js` | ~100 Z | Shutdown/Restart |

---

## 2. Feature-Gap: 30 Codex-Vorschläge vs. aktueller Stand

### PHASE 1 — COMPOSER & INPUT (5 Tasks)

| # | Feature | Status | Details |
|---|---------|--------|---------|
| P1.1 | Drag & Drop Dateien | ❌ **Fehlt** | `S.pendingFiles` existiert, aber keine Dropzone auf `#msgInner` |
| P1.2 | Auto-Resize Textarea | ❌ **Fehlt** | `#msg` hat fixe Höhe |
| P1.3 | Slash-Command Autocomplete | ❌ **Fehlt** | `COMMANDS[]` existiert (commands.js), aber kein Popup |
| P1.4 | Prominenter Model-Switcher | ❌ **Fehlt** | Kleiner Button "Deepseek V4 Flash" |
| P1.5 | Reasoning-Stufe visuell | ❌ **Fehlt** | Chip existiert, Label als Text — keine Balken |

### PHASE 2 — CHAT MESSAGES (9 Tasks)

| # | Feature | Status | Details |
|---|---------|--------|---------|
| P2.1 | Message-Editing + Branching | ❌ **Fehlt** | Kein Edit-Button, kein Branching |
| P2.2 | Code-Block Aktionen (Save/Run) | ❌ **Fehlt** | Nur `addCopyButtons()` (Copy) |
| P2.3 | Inline-Diff verbessern | ❌ **Fehlt** | `loadDiffInline()` existiert, keine Zeilennummern |
| P2.4 | Stream-Cursor | ❌ **Fehlt** | Kein blinkender Cursor |
| P2.5 | Reasoning-Accordion | ❌ **Fehlt** | `_messageHasReasoningPayload()` existiert, aber kein Accordion |
| P2.6 | Message-Actions (👍/📋/🔄) | ❌ **Fehlt** | Keine Action-Buttons unter Messages |
| P2.7 | Auto-Load älterer Messages | ⚠️ **Setting existiert** | Checkbox in Settings, default=false |
| P2.8 | Copy Conversation | ❌ **Fehlt** | Kein Export-Button |
| P2.9 | Markdown Vorschau im Input | ❌ **Fehlt** | Kein Preview-Toggle |

### PHASE 3 — LAYOUT & NAVIGATION (6 Tasks)

| # | Feature | Status | Details |
|---|---------|--------|---------|
| P3.1 | Schlankere Titlebar | ❌ **Fehlt** | 40px Titlebar mit Logo |
| P3.2 | Icons-only Sidebar | ❌ **Fehlt** | Sidebar zeigt Text + Icons |
| P3.3 | ⌘K Command Palette | ❌ **Fehlt** | Wichtigstes Feature! |
| P3.4 | Tab-basierte Konversationen | ❌ **Fehlt** | Nur Sidebar-Liste |
| P3.5 | Split View (Chat + Workspace) | ⚠️ **Ansätze** | `.chat-split-layout` im HTML, kein Drag-Handle |
| P3.6 | Focus Mode | ❌ **Fehlt** | Nur manuelles Panel-Schließen |

### PHASE 4 — WORKSPACE & CODE (5 Tasks)

| # | Feature | Status | Details |
|---|---------|--------|---------|
| P4.1 | Active Files Anzeige | ⚠️ **Ansätze** | `.open-files-bar` im HTML, kein Content |
| P4.2 | Syntax-Highlighting Workspace | ❌ **Fehlt** | Prism.js geladen, nicht im Workspace genutzt |
| P4.3 | Quick Peek | ❌ **Fehlt** | Kein Overlay-Preview |
| P4.4 | Git-Status Badges | ❌ **Fehlt** | Kein Git-Status |
| P4.5 | Selection Toolbar | ❌ **Fehlt** | Keine File-Aktionen |

### PHASE 5 — UI POLISH (5 Tasks)

| # | Feature | Status | Details |
|---|---------|--------|---------|
| P5.1 | Skeleton Loader | ❌ **Fehlt** | "Loading..." Text statt Skeletons |
| P5.2 | Typing/Thinking-Indikator | ❌ **Fehlt** | Kein "is thinking..." Indikator |
| P5.3 | Smooth Page-Transitions | ❌ **Fehlt** | Harter Panel-Wechsel |
| P5.4 | Accent-Color-Picker | ❌ **Fehlt** | Nur ganze Skins |
| P5.5 | Inline Terminal | ⚠️ **Ansätze** | `terminal.js` + xterm.js geladen |

---

## 3. Zusammenfassung

| Phase | Geplant | Fertig | Teilweise | Offen |
|-------|---------|--------|-----------|-------|
| P1 Composer | 5 | 0 | 0 | **5** |
| P2 Messages | 9 | 0 | 1 | **8** |
| P3 Layout | 6 | 0 | 1 | **5** |
| P4 Workspace | 5 | 0 | 1 | **4** |
| P5 Polish | 5 | 0 | 1 | **4** |
| **Total** | **30** | **0** | **4 (Ansätze)** | **26** |

**Nichts von den geplanten 30 Features ist vollständig umgesetzt.**  
4 Features haben HTML-Ansätze (Split View, Context Bar, Inline Terminal, Auto-Load Setting), aber keine funktionale Implementierung.

Die 3773 Zeilen Diff im Git sind von anderen Projekten (Evey Tools Dashboard + Chat Redesign Kanban).
