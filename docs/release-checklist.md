# Release Checklist

> **For use by maintainers when cutting a new Sidekick release.**

## 1. Smoke Tests

```bash
# Full smoke suite - CLI + WebUI
python tests/smoke_all.py

# WebUI HTTP smoke only
python tests/smoke_webui.py
```

**Expected:** All tests pass (exit 0, no errors).

## 2. Branding Audit

Ensure no stale user-facing references to legacy names (`Sidekick`, `NousResearch`, `LastBrowser`) exist in installer scripts, docs, or smoke tests.

```bash
# Quick scan (excludes env-var constants and legitimate legacy references)
grep -in "Sidekick\|NousResearch\|LastBrowser" \
  --include="*.ps1" --include="*.md" --include="*.py" . \
  | grep -iv "SIDEKICK_\|SIDEKICK_\|legacy\|migration\|alias\|compat"
```

**Expected:** No matches, or only lines that are explicitly intentional legacy references. The existing smoke test `tests/smoke_all.py` also runs a branding check against the user-facing docs and help output.

## 3. Version Tag

```bash
git tag -a "v0.X.0" -m "v0.X.0 — <short description>"
```

## 4. Push Tag

```bash
git push origin master --tags
```

## 5. GitHub Release

- Go to <https://github.com/Loggableim/sidekick-agent/releases>
- Draft a new release from the `v0.X.0` tag
- Title: `v0.X.0 — <title>`
- Paste release notes from `docs/releases/v0.X.0.md`
- ⚠ **Do not tick "Set as the latest release"** unless this is the latest stable release

## 6. Installer One-Line Verification

**Test in a clean VM or fresh user profile:**

```powershell
irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex
```

Verify:
- [ ] Installer completes successfully (exit 0)
- [ ] `sidekick doctor` works
- [ ] `sidekick dashboard` opens the WebUI
- [ ] Desktop shortcut `Sidekick.lnk` exists

## 7. Windows Update Verification

On an existing install:

```powershell
cd %LOCALAPPDATA%\sidekick\sidekick-agent
git status
```

Verify:
- [ ] No local changes (clean working tree)
- [ ] On the correct branch (`main`)

Then re-run the installer (should update idempotently):

```powershell
irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex
```

## 8. Config / State Migration Check

After update on an existing install:

- [ ] All existing sessions are still present in `~/.sidekick/state/webui/sessions/`
- [ ] `~/.sidekick/config.yaml` is unchanged
- [ ] `~/.sidekick/.env` is unchanged
- [ ] `sidekick doctor` reports green (or expected warnings)

## 9. Readme / Release Notes

- [ ] `docs/releases/v0.X.0.md` is written with all changes, smoke results, and upgrade notes
- [ ] `README.md` is updated if any new user-facing features/flags were added
- [ ] `docs/troubleshooting.md` is updated if install/update/uninstall flow changed
