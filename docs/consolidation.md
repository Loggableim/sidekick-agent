# Sidekick Repo - Consolidation And Current State

Sidekick is the current monorepo. The historical split between
`cids-hermes-agent` and `cids-hermes-webui` are gone; the remaining legacy
`hermes` names in this repo are compatibility shims, aliases, or historical
references.

## What Lives Where

- `cli/` owns the human entrypoints, setup, auth, config, and TUI.
- `runtime/` owns provider adapters, the agent loop, cron, and gateway logic.
- `web/` owns the WebUI backend and frontend assets.
- `shared/` owns the low-level config, path, logging, and session helpers.
- `tools/` owns the concrete tool implementations.
- `sidekick_app/` and `sidekick_cli/` keep the entrypoint and import-compat
  layer stable.

## Naming

| Term | Meaning |
|------|---------|
| `Sidekick` | Canonical product name |
| `Nova` | Default assistant identity |
| `hermes` | Legacy CLI alias for `sidekick` |
| `HERMES_*` | Legacy env-var names kept for backward compatibility |
| `~/.hermes` | Legacy home directory fallback |
| `~/.sidekick` | Canonical home directory |

## Compatibility Rules

- `SIDEKICK_HOME` wins over `HERMES_HOME`.
- `HERMES_*` env vars are still read when a `SIDEKICK_*` replacement exists.
- `hermes` remains a documented alias for the `sidekick` binary.
- `sidekick_cli.*` imports still forward to `cli.*`.
- `~/.hermes/` state can still be migrated or reused when the old install
  layout is present.

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
- A few legacy compatibility strings remain by design.

## Canonical References

- `docs/architecture.md`
- `docs/config-reference.md`
- `docs/known-issues.md`
- `docs/release-checklist.md`
- `docs/troubleshooting.md`
