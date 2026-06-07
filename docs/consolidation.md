# Sidekick Consolidation Notes

This repo is the new canonical home for Sidekick.

## Migration rules

- Migrate by subsystem, not by copying whole working directories.
- Treat the old repos as source material only:
  - `C:\HermesPortable\cids-hermes-agent`
  - `C:\HermesPortable\cids-hermes-webui`
- Never import:
  - `.hermes/`
  - `home/`
  - `spaces/`
  - `bewusstsein/`
  - `.env*` secrets
  - generated logs, sessions, caches, backups

## Naming rules

- Primary product name: `Sidekick`
- `Nova` may describe the consciousness or default assistant layer
- `Hermes` remains compatibility-only during migration
- `Nous Research` references should be kept only where provenance or license
  obligations require them

## Initial destination map

| Target area | Source of truth now | Notes |
| --- | --- | --- |
| shared config/path compatibility | new repo | canonicalize here first |
| runtime / providers / tools | `cids-hermes-agent` | migrate in focused slices |
| WebUI server / browser assets | `cids-hermes-webui` | rewire to shared config |
| CLI/TUI entrypoints | `cids-hermes-agent` | preserve behavior, re-home imports |
