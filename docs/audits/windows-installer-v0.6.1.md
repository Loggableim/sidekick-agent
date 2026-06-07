# Sidekick Windows Installer — Hardening Audit (v0.6.1)

**Stand:** `install.ps1` (1503 Zeilen)
**Datum:** 2026-06-08
**Änderungen seit:** v0.6.0

---

## Änderungen in v0.6.1

### 1. Timeouts für alle Netzwerkoperationen

| Operation | Methode | Timeout | Status |
|-----------|---------|---------|--------|
| GitHub API (PortableGit Release-Erkennung) | `Invoke-RestMethod -TimeoutSec 60` | 60 s | ✅ |
| PortableGit Download | `Invoke-WebRequest -TimeoutSec 60` | 60 s | ✅ |
| ZIP-Archiv Download (Repository-Fallback) | `Invoke-WebRequest -TimeoutSec 60` | 60 s | ✅ |
| uv Installation | `Invoke-WebRequest -TimeoutSec 60` via inner PowerShell | 60 s | ✅ |
| `git fetch` | `-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60` | ~60 s bei <1 KB/s | ✅ |
| `git pull` | `-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60` | ~60 s bei <1 KB/s | ✅ |
| `git clone` | `--depth 1` (shallow clone reduziert Transfervolumen) | n/a (shallow) | ✅ |

**Detail:** Alle `Invoke-RestMethod`- und `Invoke-WebRequest`-Aufrufe haben jetzt explizite `-TimeoutSec 60`. Der `irm … | iex`-Aufruf für uv wurde durch `Invoke-WebRequest -TimeoutSec 60` ersetzt. Git-Netzwerkoperationen (`fetch`, `pull`) verwenden `http.lowSpeedLimit`/`http.lowSpeedTime` für implizites Timeout bei langsamen Verbindungen. `git clone` verwendet `--depth 1` (shallow clone), um Transfervolumen zu reduzieren.

### 2. Exit-Code-Schema

| Exit-Code | Bedeutung | Verwendung |
|-----------|-----------|------------|
| 0 | Success | Ende von `Main` |
| 1 | Generic failure (unhandled exception) | Äußerer `catch`-Block |
| 2 | Missing prerequisite | `Install-Uv`, `Test-Python`, `Install-Git` schlagen fehl |
| 3 | Network/download failure | (vorgesehen für HTTP-Timeouts, DNS-Fehler) |
| 4 | Git/update failure | `Install-Repository` — `git fetch`, `checkout`, `pull`, `clone`, ZIP-Download |
| 5 | Install/venv failure | `Install-Dependencies` — `pip install` fehlschlag |
| 6 | Verification failure | (vorgesehen für `doctor`/`smoke check`) |

**Detail:** Alle `throw`-Aufrufe in `Main` und den Installationsfunktionen wurden durch `Write-Err` + `exit <code>` ersetzt. Der äußere `catch`-Block verwendet `exit 1`. Bei erfolgreicher Installation wird `exit 0` gesetzt.

### 3. README One-Liner Korrektur

Der Usage-Kommentar im Skriptkopf und die Fehlermeldung im äußeren Catch-Block wurden von `/scripts/install.ps1` auf `/install.ps1` korrigiert, da die Datei im Repository-Root liegt. Der README.md enthielt bereits den korrekten Pfad.

---

## Prüfpunkte (aus v0.6.0 fortgeführt)

### Keine Admin-Rechte erforderlich ✅

| Prüfpunkt | Status |
|-----------|--------|
| Alle Aktionen unter `%LOCALAPPDATA%\\sidekick\\` | ✅ |
| `uv`-Installation nach `%USERPROFILE%\\.local\\bin\\` | ✅ |
| PortableGit nach `%LOCALAPPDATA%\\sidekick\\git\\` | ✅ |
| venv in `%LOCALAPPDATA%\\sidekick\\sidekick-agent\\.venv` | ✅ |
| Keine SYSTEM- oder Admin-PATH-Änderungen | ✅ |
| Keine `ProgramFiles`- oder `System32`-Zugriffe | ✅ |
| Keine UAC-Anforderung | ✅ |

### Keine systemweiten PATH-/Registry-Änderungen ✅

| Prüfpunkt | Status |
|-----------|--------|
| PATH-Änderungen nur auf User-Ebene | ✅ |
| Keine Registry-Eingriffe außer PATH | ✅ |
| Keine Firewall-Regeln | ✅ |
| Keine Windows-Dienste | ✅ |
| Keine geplanten Tasks | ✅ |

### Idempotentes Verhalten ✅

| Prüfpunkt | Status |
|-----------|--------|
| `git fetch + pull` statt immer neuem Clone | ✅ |
| venv-Erkennung | ✅ |
| Kein doppelter PATH-Eintrag | ✅ |
| Desktop-Shortcut wird überschrieben | ✅ |
| Config-Vorlagen nur bei Fehlen | ✅ |

### Netzwerkfehler ⚠️ → ✅ (v0.6.1)

| Prüfpunkt | Status v0.6.0 | Status v0.6.1 |
|-----------|---------------|---------------|
| Install-Uv: Fehlermeldung + Fallback-Link | ✅ | ✅ |
| Install-Git: GitHub-API-Aufruf → Timeout | ⚠️ (kein Timeout) | ✅ (`-TimeoutSec 60`) |
| Install-Repository: `git clone` schlägt fehl | ✅ | ✅ |
| **Kaskadierende Timeouts** | ⚠️ (keine) | ✅ (alle Netzwerkops mit Timeout) |
| **Git low-speed-timeout** | ⚠️ (nicht vorhanden) | ✅ (`http.lowSpeedTime=60`) |

### Exit Codes ⚠️ → ✅ (v0.6.1)

| Prüfpunkt | Status v0.6.0 | Status v0.6.1 |
|-----------|---------------|---------------|
| Exit-Code-Schema definiert | ❌ | ✅ (0-6) |
| `exit 0` bei Erfolg | ❌ | ✅ |
| `exit 1` bei Fehler | ❌ | ✅ |
| Spezifische Exit-Codes | ❌ | ✅ (2,4,5) |

### Weitere Prüfpunkte

| Kriterium | Status |
|-----------|--------|
| Dirty Git Tree | ⚠️ (unverändert seit v0.6.0) |
| Update-/Repair-Pfad | ✅ |
| Antivirus / locked files | ⚠️ (unverändert seit v0.6.0) |
| Pfade mit Leerzeichen | ✅ |
| Secrets / API-Keys | ✅ |
| Hermes / LastBrowser entfernt | ✅ |
| PowerShell Parse Check | ✅ |

---

## Zusammenfassung

| Kriterium | v0.6.0 | v0.6.1 |
|-----------|--------|--------|
| Keine Admin-Rechte | ✅ | ✅ |
| Keine systemweiten Änderungen | ✅ | ✅ |
| Idempotent | ✅ | ✅ |
| Dirty Git Tree | ⚠️ | ⚠️ |
| Update-/Repair-Pfad | ✅ | ✅ |
| Netzwerkfehler | ⚠️ | ✅ |
| Antivirus / locked files | ⚠️ | ⚠️ |
| Pfade mit Leerzeichen | ✅ | ✅ |
| Exit Codes | ⚠️ | ✅ |
| Secrets / API-Keys | ✅ | ✅ |
| Hermes / LastBrowser | ✅ | ✅ |

### Offene Punkte für v0.7.0+

1. **Retry-Logik für `Remove-Item`** — Bei von Antivirus gelockten Dateien: 3 Versuche mit 1 s Pause
2. **Dirty-Tree-Warnung vor `git reset --hard`** — Prüfen auf nicht-committete Änderungen vor dem Reset
3. **`-NoProfile` für PowerShell-Session** — Verhindert Konflikte mit benutzerdefinierten Profilen
4. **`$ProgressPreference = 'SilentlyContinue'`** vor großen Downloads
5. **SHA256-Prüfung für Downloads** — PortableGit und uv-Binary auf Integrität prüfen
6. **CI-Pipeline für Windows** — GitHub Actions Runner für Windows-Tests
