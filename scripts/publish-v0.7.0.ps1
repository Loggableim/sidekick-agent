#!/usr/bin/env pwsh
# ============================================================================
# Sidekick v0.7.0 — GitHub Recovery Script
# ============================================================================
# Run this from a machine with working internet (Windows/macOS/Linux).
# Assumes you have a clone of Loggableim/sidekick-agent with push access.
#
# Usage:
#   ./scripts/publish-v0.7.0.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

Write-Host "═══ Sidekick v0.7.0 — GitHub Recovery ═══" -ForegroundColor Cyan

# ── 1. Verify we are in the right repo ──
$remote = git remote -v 2>&1
if ($remote -notmatch "Loggableim/sidekick-agent") {
    Write-Error "Wrong repo — expected Loggableim/sidekick-agent"
    exit 1
}
Write-Host "✓ Correct repo: Loggableim/sidekick-agent" -ForegroundColor Green

# ── 2. Fetch and verify local state ──
git fetch origin
git log --oneline v0.5.0..v0.7.0 --left-right

Write-Host "`nReady to push. Review the commits above, then:" -ForegroundColor Yellow

# ── 3. Push all branches and tags ──
git push origin master --tags

if ($LASTEXITCODE -ne 0) {
    Write-Error "Push failed: $LASTEXITCODE"
    exit 1
}
Write-Host "✓ Push successful" -ForegroundColor Green

# ── 4. Verify tags on remote ──
$tags = git ls-remote --tags origin | Select-String "v0.7.0"
if ($tags) {
    Write-Host "✓ v0.7.0 tag verified on remote" -ForegroundColor Green
} else {
    Write-Error "v0.7.0 tag not found on remote"
    exit 1
}

# ── 5. Make repo public ──
gh repo edit Loggableim/sidekick-agent --visibility public

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to set repo to public"
    exit 1
}
Write-Host "✓ Repo set to public" -ForegroundColor Green

# ── 6. Verify raw URLs ──
$installUrl = "https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1"
$uninstallUrl = "https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/uninstall.ps1"

$installOk = (Invoke-WebRequest -Uri $installUrl -UseBasicParsing -TimeoutSec 10).StatusCode -eq 200
$uninstallOk = (Invoke-WebRequest -Uri $uninstallUrl -UseBasicParsing -TimeoutSec 10).StatusCode -eq 200

if ($installOk) { Write-Host "✓ install.ps1 raw URL reachable" -ForegroundColor Green } else { Write-Error "install.ps1 not reachable" }
if ($uninstallOk) { Write-Host "✓ uninstall.ps1 raw URL reachable" -ForegroundColor Green } else { Write-Error "uninstall.ps1 not reachable" }

# ── 7. Final installer command ──
Write-Host ""
Write-Host "══════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  SIDEKICK v0.7.0 — VERIFIED AND PUBLIC" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Install:  irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex" -ForegroundColor Green
Write-Host "  Uninstall: .\uninstall.ps1 [-RemoveUserData]" -ForegroundColor Green
Write-Host "  Repo:     https://github.com/Loggableim/sidekick-agent" -ForegroundColor Green
Write-Host ""
