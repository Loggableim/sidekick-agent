# Sidekick Windows Installer — Safety Audit (v0.6.0)

**Stand:** `install.ps1` (1484 Zeilen)
**Datum:** 2026-06-07

---

## Audit-Kriterien

### 1. Keine Admin-Rechte erforderlich

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| Alle Aktionen unter `%LOCALAPPDATA%\sidekick\` | ✅ | Kein Zugriff auf `Program Files`, `System32` o.Ä. |
| `uv`-Installation nach `%USERPROFILE%\.local\bin\` | ✅ | User-lokaler Pfad |
| PortableGit nach `%LOCALAPPDATA%\sidekick\git\` | ✅ | Keine SYSTEM-PATH-Änderungen |
| venv in `%LOCALAPPDATA%\sidekick\sidekick-agent\.venv` | ✅ | Isoliert im User-Kontext |
| Keine SYSTEM- oder Admin-PATH-Änderungen | ✅ | Nur `[EnvironmentVariableTarget]::User` |
| Keine `ProgramFiles`- oder `System32`-Zugriffe | ✅ | Explizit vermieden |
| Keine UAC-Anforderung | ✅ | Installierbar ohne Admin-Rechte |

### 2. Keine systemweiten PATH-/Registry-Änderungen ohne Zustimmung

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| PATH-Änderungen nur auf User-Ebene | ✅ | `SetEnvironmentVariable(..., "User")` — nie `"Machine"` |
| Keine Registry-Eingriffe | ✅ | Weder `HKLM` noch `HKCU` außer PATH |
| Keine Firewall-Regeln | ✅ | Kein `netsh`- oder `New-NetFirewallRule`-Aufruf |
| Keine Windows-Dienste | ✅ | Kein `New-Service` oder `sc.exe` |
| Keine geplanten Tasks | ✅ | Kein `schtasks.exe`-Aufruf |

### 3. Idempotentes Verhalten

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| `git fetch + reset` statt immer neuem Clone | ✅ | Bestehendes `.git`-Verzeichnis wird erkannt |
| venw-Erkennung | ✅ | `.venv` wird erkannt und nicht neu erstellt |
| Kein doppelter PATH-Eintrag | ✅ | `notcontains`-Prüfung vor PATH-Erweiterung |
| Desktop-Shortcut wird überschrieben | ✅ | `CreateShortcut` überschreibt vorhandene `.lnk` |
| Config-Vorlagen nur bei Fehlen | ✅ | `.env`, `config.yaml`, `SOUL.md` werden nicht überschrieben |

### 4. Dirty Git Tree

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| `git fetch + checkout + reset --hard` | ⚠️ | Überschreibt lokale (nicht-committete) Änderungen |
| Kein Stash vor Reset | ❌ | Kein `git stash` vor dem Force-Update |
| Kein Merge — reiner Force-Update | ⚠️ | Branch wird auf Remote-Spitze gesetzt |
| **Empfehlung** | → | Hinweis auf möglichen Datenverlust im Kommando-Doku oder vor reset |

### 5. Update-/Repair-Pfad

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| `-UpdateOnly` überspringt Prerequisites | ✅ | Keine Neuinstallation von `uv`, PortableGit, venv |
| `pip install` wird erneut ausgeführt | ✅ | Abhängigkeiten werden aktualisiert |
| Desktop-Shortcut + Completion laufen | ✅ | Auch im Update-Modus ausgeführt |
| `git fetch + reset` auf Branch-Spitze | ✅ | Synchronisiert mit Repository |

### 6. Fehler bei fehlendem Netz

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| Install-Uv: Fehlermeldung + Fallback-Link | ✅ | Klarer Hinweis auf manuellen Install |
| Install-Git: GitHub-API-Aufruf → Fehler | ⚠️ | GitHub-API kann ohne Timeout hängen |
| Install-Repository: `git clone` schlägt fehl | ✅ | Fehler wird geworfen |
| **Keine kaskadierenden Timeouts** | ⚠️ | `Invoke-RestMethod` hat keinen expliziten `-TimeoutSec` |
| **Empfehlung** | → | Timeout für `Invoke-RestMethod` setzen (z.B. 60s) |

### 7. Fehler bei Antivirus / locked files

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| `Remove-Item -Recurse -Force` | ⚠️ | Kann bei von AV gelockten Dateien fehlschlagen |
| CWD-Schutz | ✅ | Wenn `InstallDir == CWD` → `Set-Location $env:USERPROFILE` |
| **Keine Retry-Logik** | ⚠️ | Kein erneuter Versuch bei `Remove-Item`-Fehler |
| **Empfehlung** | → | Retry-Logik (3 Versuche mit 1s Pause) für `Remove-Item` |

### 8. Pfade mit Leerzeichen

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| `$InstallDir` enthält keine Leerzeichen | ✅ | `%LOCALAPPDATA%\sidekick\sidekick-agent` |
| `%USERPROFILE%` kann Leerzeichen enthalten | ✅ | Z.B. `C:\Users\John Doe` |
| Alle Aufrufe quoted | ✅ | `& "$gitExe" --version`, `"$venvPath\Scripts\python.exe"` |
| `Start-Process` mit quoted Strings | ✅ | Korrekt gehandhabt |
| **Gesamtbewertung** | ✅ | Keine Leerzeichen-Probleme erwartet |

### 9. Exit Codes

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| `throw` bei schweren Fehlern | ⚠️ | Kein klar definiertes Exit-Code-Schema |
| `try/catch` outer wrap | ✅ | Verhindert iex-Kill bei unbehandelten Fehlern |
| **Kein explizites `exit 0/1`** | ⚠️ | Skript beendet ohne definierten Exit-Code |
| **Empfehlung** | → | `exit 0` bei Erfolg, `exit 1` bei Fehler am Ende |

### 10. Secrets / API-Keys

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| Keine API-Key-Abfragen im Installer | ✅ | Weder Eingabe noch Speicherung |
| Keine Credentials im Code | ✅ | Keine hartcodierten Zugangsdaten |
| Keine hartcodierten Secrets | ✅ | Keine Tokens, Passwörter o.Ä. |

### 11. Sidekick / LastBrowser

| Prüfpunkt | Status | Anmerkung |
|-----------|--------|-----------|
| Kein user-facing Sidekick | ✅ | Alle Texte/Logos auf Sidekick umgestellt |
| Kein LastBrowser | ✅ | Entfernt |
| Alle Env-Vars auf `SIDEKICK_*` | ✅ | Keine veralteten `SIDEKICK_*`-Variablen |
| Repo-URL | ✅ | `Loggableim/sidekick-agent` |

---

## Zusammenfassung

| Kriterium | Status |
|-----------|--------|
| Keine Admin-Rechte erforderlich | ✅ |
| Keine systemweiten Änderungen | ✅ |
| Idempotentes Verhalten | ✅ |
| Dirty Git Tree | ⚠️ |
| Update-/Repair-Pfad | ✅ |
| Netzwerkfehler | ⚠️ |
| Antivirus / locked files | ⚠️ |
| Pfade mit Leerzeichen | ✅ |
| Exit Codes | ⚠️ |
| Secrets / API-Keys | ✅ |
| Sidekick / LastBrowser entfernt | ✅ |

### Risikomatrix

| Risiko | Wahrscheinlichkeit | Auswirkung | Priorität |
|--------|--------------------|------------|-----------|
| Dirty Tree → Datenverlust | Mittel | Mittel | Hoch |
| Netzwerk-Timeout | Niedrig | Mittel | Mittel |
| Locked Files durch AV | Niedrig | Niedrig | Niedrig |
| Fehlende Exit-Codes | Niedrig | Niedrig | Niedrig |

---

## Empfehlungen (für v0.7.0+)

1. **Timeout für `Invoke-RestMethod`** — GitHub-API- und Release-Downloads können bei langsamen Verbindungen hängen. `-TimeoutSec 60` setzen.
2. **Retry-Logik für `Remove-Item`** — Bei von Antivirus gelockten Dateien: 3 Versuche mit 1 s Pause, dann Warnung ausgeben statt Abbruch.
3. **Explizites Exit-Code-Schema** — `exit 0` bei Erfolg, `exit 1` bei abgefangenem Fehler. Erleichtert automatisierte Ausführung und CI-Integration.
4. **Dirty-Tree-Warnung vor `git reset --hard`** — Prüfen auf nicht-committete Änderungen vor dem Reset und Benutzer warnen/abbruch anbieten.
5. **`-NoProfile` für PowerShell-Session** — Verhindert Konflikte mit benutzerdefinierten PowerShell-Profilen während der Installation.
6. **Progress-Preference für Downloads** — `$ProgressPreference = 'SilentlyContinue'` vor größeren Downloads (PortableGit) reduziert Latenz durch Write-Progress-Updates.
7. **SHA256-Prüfung für Downloads** — PortableGit und uv-Binary auf Integrität prüfen nach Download.
8. **CI-Pipeline für Windows** — GitHub Actions Runner für Windows-Tests (auch wenn nur `-UpdateOnly`-Smoke-Test).
