# Sidekick Repo - Consolidation And Current State

Sidekick is the unified monorepo for the WebUI, Nova, CLI, gateway, and
background automation.

## What Lives Where

- `cli/` owns the human entrypoints, setup, auth, config, and TUI.
- `runtime/` owns provider adapters, the agent loop, cron, and gateway logic.
- `web/` owns the WebUI backend and frontend assets.
- `shared/` owns the low-level config, path, logging, and session helpers.
- `tools/` owns the concrete tool implementations.
- `sidekick_app/` owns the application bootstrap.

## Naming

| Term | Meaning |
|------|---------|
| `Sidekick` | Canonical product name |
| `Nova` | Default assistant identity |
| `~/.sidekick` | Canonical home directory |

## Compatibility Rules

- `SIDEKICK_HOME` selects the active state root.
- The default state root is `~/.sidekick`.

## Current Product Posture

The repository is now organized around one shared runtime and three first-class
surfaces:

- CLI and TUI
- WebUI
- Messaging gateway / background automation

The remaining gaps are the ones tracked in `docs/known-issues.md` and the
current roadmap:

- Windows CI is active.
- The session layer still has two object models, but shared round-tripping
  plus shared list/status snapshots now preserve WebUI-only metadata.
- The WebUI is the primary product surface; CLI and gateway support it.

## Canonical References

- `docs/architecture.md`
- `docs/config-reference.md`
- `docs/known-issues.md`
- `docs/release-checklist.md`
- `docs/troubleshooting.md`
