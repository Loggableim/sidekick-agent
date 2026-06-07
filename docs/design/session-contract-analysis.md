"""Session Contract — Analyse der Divergenz zwischen shared.sessions und web.api

Stand: v0.2.0 (5f9edfe)
Datum: 2026-06-07

## Session-Modelle

### shared.sessions.Session (6 Felder)
- session_id, title, workspace, model, messages, created_at, updated_at
- Reines @dataclass
- Persistenz: ~/.sidekick/state/webui/sessions/<id>.json
- Nutzer: CLI (cli.cli), TUI, smoke tests

### web.api.models.Session (30+ Felder)
- session_id, title, workspace, model, model_provider, messages, tool_calls,
  created_at, updated_at, pinned, archived, project_id, profile,
  input_tokens, output_tokens, estimated_cost, personality,
  active_stream_id, pending_user_message, pending_attachments,
  pending_started_at, context_messages, compression_anchor_*,
  context_length, threshold_tokens, last_prompt_tokens,
  gateway_routing*, llm_title_generated, parent_session_id,
  worktree_*, is_cli_session, source_tag, enabled_toolsets,
  composer_draft, workspace_slug, agent_slug
- Runtime-Objekt mit JSON-Persistenz, Index, Lock- und Stream-Lifecycle
- Persistenz: ~/.hermes/webui/sessions/<id>.json (ALT!)
  Ziel: ~/.sidekick/state/webui/sessions/<id>.json (NEU)
- Nutzer: WebUI (routes.py, streaming.py, session_ops.py)

## Operationelle APIs

### retry_last(session_id) -> dict
- shared: reine Logik: load → truncate → save
- web.api: Gleiche Logik + WebUI-Lock (_get_session_agent_lock)
  + stale-object-guard (SESSIONS.get) + context_messages truncation

### undo_last(session_id) -> dict
- shared: reine Logik: load → truncate → save
- web.api: Gleiche Logik + Lock + stale-guard + context_messages

### session_status(session_id) -> dict
- shared: Reine Metadaten aus JSON
- web.api: + stream_active check, pending_user_message check

## Storage-Pfad-Divergenz

Beide schreiben Session-JSONs, aber in UNTERSCHIEDLICHE Verzeichnisse:
- shared.sessions → ~/.sidekick/state/webui/sessions/
- web.api.models → ~/.hermes/webui/sessions/

Das ist der kritischste Punkt: Sessions aus der CLI sind im WebUI unsichtbar.

## Umstellungsplan

1. web/api/config.py STATE_DIR auf kanonischen Pfad umstellen
   (SIDEKICK_WEBUI_STATE_DIR > HERMES_WEBUI_STATE_DIR > ~/.sidekick/state/webui/)
2. Legacy-Migration: Einmalige Kopie ~/.hermes/webui/sessions/* → neuer Pfad
3. web/api/session_ops.py retry_last/undo_last/session_status als thin wrappers
   die web.api.spezifische Locks hinzufügen, Logik aber an shared.sessions delegieren
4. Env-Var-Chain: SIDEKICK_WEBUI_STATE_DIR hat Vorrang vor HERMES_WEBUI_STATE_DIR
5. shared.runtime.web_state_dir() und web.api.config.STATE_DIR auf gleiche Logik
"""