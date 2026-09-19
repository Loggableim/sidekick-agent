# Changelog

## [0.8.84] — 2026-09-19

### Added
- **WebUI: Ctrl/Cmd+Enter in the composer now interrupts the running turn and sends immediately** instead of following the busy input mode. When the agent is busy, Ctrl/Cmd+Enter cancels the current turn (queue + cancel + drain re-send) and starts a fresh turn with the new message; plain Enter and the Send button keep following the configured busy input mode (queue/steer/interrupt). Explicit user intent beats the preference for this one send.
- **WebUI: live thinking stream with stop/steer controls** — reasoning traces stream into the conversation as they are produced, with inline stop and steer actions.
- **WebUI: clickable stream cursor reveals live reasoning** — the decorative cursor element is now a control that expands the in-progress reasoning trace.
- **WebUI: Skills panel overhaul** — the panel now surfaces the curator module, usage telemetry, category/tag chips, and reloads automatically when skills change on disk.
- **WebUI: SSE status stream, gzip for static assets and JSON, and a completed service-worker precache** — the shell, session list, and settings responses are compressed and cached; the SW precache now covers the full shell set.
- **Launcher: `-AppMode` opens the WebUI as an Edge/Chrome app window** with a window-controls-overlay titlebar and a dedicated browser profile.
- **Nova Space supervision and governance** — presence card, supervisor ledger, managed-space action gateway, scheduler equality marker, and enrollment diagnostics.
- **Swarm core** — project-local swarm state, action policy gates, model routing, capability registry, learning/packs, pre-completion hooks, and a read-only host service, exposed through both CLI and API.
- **Models: `deepseek-v4.1-flash` added to the curated ollama-cloud list**, plus a DeepSeek V4 Flash cloud alias.

### Fixed
- **The WebUI now keeps live-stream markers for genuinely active chats.** Refreshing the page or switching Spaces while a chat was streaming made that chat render as idle — the transcript ended at the user's own pending message and no live content appeared, even though the agent kept running. Three root causes were fixed: the Space session listing no longer strips `active_stream_id`/`pending_user_message` for streams that are still alive in the process (the liveness check is an in-process registry lookup, so it is safe on the listing hot path); the session index now persists a truthful `is_streaming` flag instead of always writing `false`; and the streaming checkpoint thread now writes into the session's own Space store instead of the global default store.
- **Session switching no longer swallows clicks while another load is in flight** — clicking back onto the open chat during a Space switch used to be treated as a no-op while the other load overwrote the view.
- **Persistent goal banners are strictly session-scoped** so a goal from one chat can no longer leak into unrelated conversations.
- **Runaway sessions are bounded**: the session list caps the file size it will read, and the legacy session mirror is size-limited so a single oversized session can no longer be tripled across stores on every save.
- **Stale chat stream entries are reaped by a background tick** so dead streams stop blocking new turns.
- **Approval badge visibility restored** and the WebUI smoke suite de-flaked; lazy-loaded boot calls are guarded so a missing panel script cannot abort boot.
- **Invalid Space slugs are rejected** on creation and on config writes, and the workspace drawer toggle plus QA card were restored after a merge dropped them.
- **Usage analytics scan moved off the event loop** so the dashboard stays responsive while analytics are computed.
- **WebUI browser tool auth and locator timeouts fixed**; the browser drawer keeps its status chip in sync.
- **`ui.js` parse failure fixed** — a top-level bare block with an illegal `return` killed the whole file, silently disabling every feature defined in it.
- **Cron: path-traversal job ids are rejected before any filesystem write**, and only the newest output files are retained per job.
- **MCP: permanent 4xx errors are no longer retried on initial connect**, and the shared stderr log is size-bounded.
- **Logging: rollover no longer leaves the log file untruncated** when the rename is permanently blocked.
- **Nova lifecycle: background tick event payloads are bounded** so `events.json` stops growing without limit, and the bundled state snapshot is used as a fallback when the Space session start script is broken.
- **`restart_dashboard.ps1` waits for an idle window instead of killing mid-turn** — the previous fixed-sleep version murdered every turn longer than 90 seconds.
- **State: `update_token_counts`/`ensure_session` implemented in the SessionDB compat shim** so token accounting and session persistence stop failing silently.

## [0.8.83] — 2026-07-26

### Fixed
- Restored the single-click workflow palette trigger and bounded its menu on desktop.
- Treat attached `about:blank` browser sessions as an intentional empty state.
- Keep the workflow status chip in sync when the browser drawer opens or closes.
- Restored asynchronous Game Mode title generation by matching the canonical Unicode title placeholder.
- Declared `tzdata` so Windows installations can resolve IANA time zones.
- Repaired stale smoke checks for the canonical Sidekick home, session, and branding contracts.
- Make fresh-install analytics return an empty valid report until the session database has analytics columns.

## [0.8.81] — 2026-07-10

### Fixed
- **Nova's router-primary provider pool now keeps `gpt:oss-20b` as the configured slot but reports and selects `deepseek-v4-flash` in Game Mode** so the router health view matches the live remote-safe runtime.
- **GPU Game Mode watchdog now retries transient Windows `jobs.json` replace failures** so the cron pausing path no longer dies on `PermissionError` when another process briefly holds the file.
- **Local transcription subprocesses now force UTF-8 with replacement on Windows** so ffmpeg conversion and local STT command wrappers stop crashing on cp1252 decode errors.
- **Game Mode watchdog now leaves Nova's remote-safe dream/reflection tick enabled and only pauses jobs explicitly flagged for Game Mode blocking** so Ollama Cloud DeepSeek V4 Flash keeps running in Game Mode instead of being shut off by a name-based heuristic.
- **OpenRouter auxiliary clients now read `OPENROUTER_API_KEY` through the shared dotenv-aware config fallback** so goal-judge and other text tasks can still bootstrap from `.env` when the process environment is empty.
- **The MCP stdio proxy now survives Windows narrow encodings while forwarding JSON-RPC and stderr noise** so unicode output no longer kills Firecrawl or other stdio-backed servers with a `charmap` encode failure.
- **Firecrawl MCP now normalizes the legacy local `npx firecrawl-mcp` config to the hosted Firecrawl HTTP endpoint** so MCP discovery stops trying to launch a broken local server and uses the supported transport instead.
- **Nova Game Mode title and fact helpers now route through Ollama Cloud DeepSeek V4 Flash instead of falling back to local models** so Game Mode keeps Nova useful without touching local GPU-backed endpoints.
- **Game Mode toggles now synchronise the lockfile and watchdog state immediately** so enabling or disabling Game Mode no longer waits for the watchdog cron to catch up.
- **Appstore Mail setup now opens as a fullscreen modal overlay again** instead of a bottom-sheet strip.
- **Mail now reopens on the active inbox when available, then the default inbox, before falling back to the first inbox** so reopening the Mail panel no longer jumps to the wrong mailbox.
- **Reasoning-effort copy in the WebUI now shows the intended brain glyph and separators** instead of `??`/`?`.
- **Static WebUI placeholders in the composer, goal banner, sandbox toggle, agent wizard, and mail panels now use real icons and labels** instead of `?`/`??`.
- **Slash-command help, toasts, and list separators in `web/static/commands.js` now use the intended glyphs** instead of mojibake.
- **The WebUI language selector now exposes proper menu ARIA state** and keeps `aria-expanded` in sync while opening, switching, and closing the dropdown.
- **Language selector options in the WebUI now render actual flags and labels** instead of `????`.
- **Attachment-Dateilinks im WebUI-Chat zeigen jetzt den vollständigen Pfad** statt nur den Dateinamen. ([#4226210](https://github.com/Loggableim/sidekick-agent/commit/4226210))
