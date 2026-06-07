# Sidekick

Sidekick is the single canonical repository for the standalone assistant product.
This repo is replacing the historical split between `cids-hermes-agent` and
`cids-hermes-webui`.

## Current consolidation goals

- one repo for `CLI + Agent + WebUI`
- canonical product naming under `Sidekick`
- `SIDEKICK_*` configuration as the primary interface
- strict protection against committing local secrets or private space content

## Repository layout

```text
sidekick/
  cli/        command entrypoints
  shared/     shared config, paths, env compatibility
  tests/      focused regression tests for shared behavior
  docs/       consolidation and migration docs
```

## Guardrails

- local state belongs under `~/.sidekick`, not in the repo
- `HERMES_*` env vars are read only as legacy aliases during migration
- private directories such as `home/`, `spaces/`, `.hermes/`, and
  `bewusstsein/` are explicitly ignored
- use `.env.example` as the only committed env file template

## Source repos

The current migration sources are local-only:

- `C:\HermesPortable\cids-hermes-agent`
- `C:\HermesPortable\cids-hermes-webui`

Only selected code should be ported forward. Runtime state, secrets, generated
content, backups, and personal space data must not be copied.

## Install

### Recommended

```bash
python -m pip install .
python -m sidekick_app doctor
python -m sidekick_app web serve
```

### Local development

```bash
python -m pip install -e .
python -m sidekick_app doctor
./start.sh doctor
```

On Windows:

```powershell
python -m pip install -e .
python -m sidekick_app doctor
.\start.ps1 doctor
```

If an older global `sidekick` command already exists on your PATH, prefer
`python -m sidekick_app ...` until the standalone install is the command your
shell resolves.

## Baseline commands

```bash
python -m sidekick_app doctor
python -m sidekick_app paths
python -m sidekick_app config-summary
python -m sidekick_app config show
python -m sidekick_app config get webui.port
python -m sidekick_app logs-path
python -m sidekick_app web info
python -m sidekick_app web sessions
python -m sidekick_app audit-repo
```
