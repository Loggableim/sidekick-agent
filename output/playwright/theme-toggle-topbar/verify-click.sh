#!/usr/bin/env bash
set -euo pipefail
cd /c/sidekick/sidekick/output/playwright/theme-toggle-topbar
export CODEX_HOME=/c/Users/logga/.codex
export PWCLI=/c/Users/logga/.codex/skills/playwright/scripts/playwright_cli.sh
"$PWCLI" --session theme-toggle-topbar click e222
"$PWCLI" --session theme-toggle-topbar eval 'document.documentElement.classList.contains("dark")'
"$PWCLI" --session theme-toggle-topbar click e222
"$PWCLI" --session theme-toggle-topbar eval 'document.documentElement.classList.contains("dark")'