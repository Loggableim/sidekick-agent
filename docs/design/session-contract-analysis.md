"""Session Contract - historical analysis of shared.sessions and web.api

Stand: v0.2.0 (5f9edfe)
Datum: 2026-06-07

Historical note:
- The current tree uses the same session storage root for both layers:
  `~/.sidekick/state/webui/sessions/`.
- `shared.sessions` now preserves unknown WebUI metadata fields on load/save,
  so cross-surface round-trips do not drop extra JSON keys.
- The models still differ in shape; this file documents the original split and
  the remaining object-shape differences.

## Session models

### shared.sessions.Session (6 fields)
- session_id, title, workspace, model, messages, created_at, updated_at
- Plain `@dataclass`
- Current persist path: `~/.sidekick/state/webui/sessions/<id>.json`
- Users: CLI (`cli.cli`), TUI, smoke tests

### web.api.models.Session (30+ fields)
- session_id, title, workspace, model, model_provider, messages, tool_calls,
  created_at, updated_at, pinned, archived, project_id, profile,
  input_tokens, output_tokens, estimated_cost, personality,
  active_stream_id, pending_user_message, pending_attachments,
  pending_started_at, context_messages, compression_anchor_*,
  context_length, threshold_tokens, last_prompt_tokens,
  gateway_routing*, llm_title_generated, parent_session_id,
  worktree_*, is_cli_session, source_tag, enabled_toolsets,
  composer_draft, workspace_slug, agent_slug
- Rich runtime object with JSON persistence, index, lock, and stream lifecycle
- Current persist path: `~/.sidekick/state/webui/sessions/<id>.json`
- Users: WebUI (`routes.py`, `streaming.py`, `session_ops.py`)

## Operationelle APIs

### retry_last(session_id) -> dict
- shared: plain load -> truncate -> save
- web.api: same logic + WebUI lock (`_get_session_agent_lock`)
  + stale-object guard (`SESSIONS.get`) + `context_messages` truncation

### undo_last(session_id) -> dict
- shared: plain load -> truncate -> save
- web.api: same logic + lock + stale guard + `context_messages`

### session_status(session_id) -> dict
- shared: plain metadata from JSON
- web.api: + `stream_active` check, `pending_user_message` check

## Storage note

Both layers now write the same directory:
- shared.sessions -> `~/.sidekick/state/webui/sessions/`
- web.api.models -> `~/.sidekick/state/webui/sessions/`

The remaining difference is the object form, not the storage root.

## Historical plan

1. `web/api/config.py` STATE_DIR on canonical path
   (`SIDEKICK_WEBUI_STATE_DIR > HERMES_WEBUI_STATE_DIR > ~/.sidekick/state/webui/`)
2. Legacy migration: one-time copy `~/.hermes/webui/sessions/*` -> new path
3. `web/api/session_ops.py` thin wrappers over `shared.sessions`
4. Env-var chain: `SIDEKICK_WEBUI_STATE_DIR` wins over `HERMES_WEBUI_STATE_DIR`
5. `shared.runtime.web_state_dir()` and `web.api.config.STATE_DIR` on same logic

All of the above are now implemented in the current tree.
"""
