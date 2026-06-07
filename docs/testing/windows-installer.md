# Sidekick Windows Installer — Manual Test Plan (v0.6.0)

**Datum:** 2026-06-07
**Zielgruppe:** QA / Entwickler
**Geschätzter Aufwand:** ca. 2–3 Stunden (komplette Durchführung aller Testfälle)

---

## Test-Umgebung

### Anforderungen

| Komponente | Vorgabe |
|------------|---------|
| **Betriebssystem** | Windows 10 22H2+ oder Windows 11 |
| **PowerShell** | 5.1+ (Standard unter Windows 10/11) |
| **Internetverbindung** | Erforderlich (Download uv, PortableGit, Repository) |
| **Admin-Rechte** | Nicht erforderlich — Test muss als **Standard-User** durchgeführt werden |
| **RAM** | ≥ 4 GB |
| **Festplatte** | ≥ 2 GB freier Speicher |
| **Browser** | Edge, Chrome oder Firefox (für WebUI Auto-Open) |

### Empfohlene Test-VMs

Wegen der zerstörerischen Natur einiger Testfälle (dirty tree, Neuinstallation) wird die Verwendung von **Windows-Sandbox** oder einer **VM-Snapshot-basierten** Umgebung empfohlen.

---

## Testfälle

---

### TC-01: Fresh Windows Install (Happy Path)

| Feld | Wert |
|------|------|
| **ID** | TC-01 |
| **Priorität** | **Hoch** |
| **Setup** | Saubere VM / Sandbox ohne installiertes Python, Git oder uv |
| **Typ** | Funktional / Smoke |

**Steps:**

1. Öffne **PowerShell** als Standard-User (nicht als Admin)
2. Führe den One-Liner aus:
   ```powershell
   irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/main/install.ps1 | iex
   ```
3. Warte auf Completion (ca. 2–5 Minuten, abhängig von Internetgeschwindigkeit)
4. Schließe das sich öffnende Browser-Fenster

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | `uv` wird installiert (nach `%USERPROFILE%\.local\bin\`) | `Get-Command uv` |
| 2 | Python 3.11+ wird via uv installiert | `uv python list` |
| 3 | PortableGit wird heruntergeladen + extrahiert (bei fehlendem Git) | `Test-Path "$env:LOCALAPPDATA\sidekick\git\bin\git.exe"` |
| 4 | Repository wird gecloned nach `%LOCALAPPDATA%\sidekick\sidekick-agent\` | `Test-Path "$env:LOCALAPPDATA\sidekick\sidekick-agent\.git"` |
| 5 | venv wird erstellt | `Test-Path "$env:LOCALAPPDATA\sidekick\sidekick-agent\.venv\Scripts\python.exe"` |
| 6 | `pip install -e ".[all]"` läuft erfolgreich | Keine Fehler in der Ausgabe |
| 7 | Desktop Shortcut `Sidekick.lnk` auf Desktop | `Test-Path "$env:USERPROFILE\Desktop\Sidekick.lnk"` |
| 8 | WebUI öffnet sich im Browser auf `http://127.0.0.1:8787` | Sichtbare Prüfung |
| 9 | `SIDEKICK_HOME` und `SIDEKICK_GIT_BASH_PATH` gesetzt | `$env:SIDEKICK_HOME` |
| 10 | Keine Fehler / `throw` in der Ausgabe | Konsolenausgabe prüfen |

**Pass Criteria:** Alle 10 Erwartungen erfüllt

---

### TC-02: Idempotenz (Zweites Ausführen)

| Feld | Wert |
|------|------|
| **ID** | TC-02 |
| **Priorität** | **Hoch** |
| **Setup** | TC-01 wurde erfolgreich ausgeführt |
| **Typ** | Funktional / Regression |

**Steps:**

1. Führe denselben One-Liner erneut aus
2. Warte auf Completion

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | `git fetch + reset` (kein neues Clone) | Ausgabe zeigt `git fetch`, kein `git clone` |
| 2 | venw wird erkannt, nicht neu erstellt | Kein "Creating venv..." |
| 3 | `pip install` läuft erneut (aktualisiert ggf.) | Zeile mit `pip install -e ".[all]"` |
| 4 | Desktop Shortcut wird überschrieben | Kein Fehler |
| 5 | WebUI öffnet sich | Browser-Fenster erscheint |
| 6 | Keine doppelten PATH-Einträge | `$env:Path -split ';' | Group-Object | Where Count -gt 1` → leer |

**Pass Criteria:** Keine Konflikte, keine doppelten Einträge, kein Fehler

---

### TC-03: UpdateOnly (-UpdateOnly)

| Feld | Wert |
|------|------|
| **ID** | TC-03 |
| **Priorität** | **Hoch** |
| **Setup** | TC-01 wurde erfolgreich ausgeführt |
| **Typ** | Funktional |

**Steps:**

1. Navigiere zum Installationsverzeichnis:
   ```powershell
   cd "$env:LOCALAPPDATA\sidekick\sidekick-agent"
   ```
2. Führe den Installer mit `-UpdateOnly` aus:
   ```powershell
   .\install.ps1 -UpdateOnly
   ```
3. Warte auf Completion

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Keine Prerequisite-Installation (uv, git, python) | Keine Download-/Install-Ausgaben |
| 2 | `git fetch + reset` | Ausgabe sichtbar |
| 3 | `pip install` wird ausgeführt | Ausgabe sichtbar |
| 4 | Desktop Shortcut wird erstellt/überschrieben | Kein Fehler |
| 5 | WebUI Auto-Open | Browser-Fenster erscheint |
| 6 | Dauer < 60 s | Stoppuhr |

**Pass Criteria:** Schneller Durchlauf (< 60 s), nur git + pip + shortcut ohne Prerequisites

---

### TC-04: Ohne API-Key (Doctor & Dashboard)

| Feld | Wert |
|------|------|
| **ID** | TC-04 |
| **Priorität** | Mittel |
| **Setup** | TC-01 wurde erfolgreich ausgeführt |
| **Typ** | Funktional |

**Steps:**

1. Öffne ein neues PowerShell-Fenster
2. Führe aus: `sidekick doctor`
3. Führe aus: `sidekick dashboard`

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | `sidekick doctor` exit 0 (healthy) oder exit 1 (warnings) | `$LASTEXITCODE` |
| 2 | Kein Traceback / Stacktrace in der Ausgabe | Konsolenausgabe prüfen |
| 3 | "No API keys configured" als Warnung (kein Fehler) | Ausgabe enthält Warnung |
| 4 | `sidekick dashboard` startet WebUI | Browser öffnet `http://127.0.0.1:8787` |

**Pass Criteria:** Keine Crashs, klare Warnungen, WebUI erreichbar

---

### TC-05: Git fehlt — PortableGit-Download

| Feld | Wert |
|------|------|
| **ID** | TC-05 |
| **Priorität** | **Hoch** |
| **Setup** | VM/Sandbox **ohne** installiertes Git (kein `git --version`) |
| **Typ** | Funktional / Edge Case |

**Steps:**

1. Stelle sicher, dass Git nicht installiert ist: `git --version` → CommandNotFoundException
2. Führe install.ps1 aus

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | PortableGit wird heruntergeladen (x64 oder ARM64) | Download-Ausgabe sichtbar |
| 2 | Extraktion nach `%LOCALAPPDATA%\sidekick\git\` erfolgreich | `Test-Path "$env:LOCALAPPDATA\sidekick\git\bin\git.exe"` |
| 3 | Git funktioniert nach Install | `& "$env:LOCALAPPDATA\sidekick\git\bin\git.exe" --version` |
| 4 | `git clone` klappt | Repository wird erfolgreich gecloned |

**Pass Criteria:** PortableGit wird korrekt heruntergeladen und verwendet

---

### TC-06: Python/uv fehlt — uv-Provisionierung

| Feld | Wert |
|------|------|
| **ID** | TC-06 |
| **Priorität** | **Hoch** |
| **Setup** | VM/Sandbox **ohne** Python und ohne uv |
| **Typ** | Funktional / Edge Case |

**Steps:**

1. Stelle sicher, dass Python nicht installiert ist: `python --version` → CommandNotFoundException
2. Stelle sicher, dass uv nicht installiert ist: `uv --version` → CommandNotFoundException
3. Führe install.ps1 aus

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | uv wird installiert (via astral.sh) | Download-Ausgabe sichtbar |
| 2 | Python 3.11+ wird via uv installiert | `uv python list` zeigt Python 3.11+ |
| 3 | venv wird erstellt | `Test-Path "$env:LOCALAPPDATA\sidekick\sidekick-agent\.venv\Scripts\python.exe"` |
| 4 | `pip install -e ".[all]"` läuft | Keine Fehler |

**Pass Criteria:** uv und Python werden korrekt provisioniert, venv funktioniert

---

### TC-07: Zielordner existiert bereits mit lokalen Änderungen (Dirty Tree)

| Feld | Wert |
|------|------|
| **ID** | TC-07 |
| **Priorität** | Mittel |
| **Setup** | TC-01 ausgeführt, dann manuelle Änderungen im Clone |
| **Typ** | Edge Case / Zerstörend |

**Steps:**

1. Navigiere zu `%LOCALAPPDATA%\sidekick\sidekick-agent\`
2. Erstelle eine neue Datei: `echo "test" > dirty-test-file.txt`
3. Ändere eine bestehende Datei: `Add-Content README.md "`nLokale Änderung"`
4. Führe install.ps1 erneut aus

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | `git fetch + reset --hard` überschreibt lokale Änderungen | Ausgabe zeigt `reset --hard` |
| 2 | `dirty-test-file.txt` existiert nicht mehr | `Test-Path dirty-test-file.txt` → `False` |
| 3 | README.md ist zurückgesetzt | Original-Content ohne "Lokale Änderung" |
| 4 | Kein Abbruch/Fehler | Installer läuft vollständig durch |

**⚠️ Hinweis:** Dieser Testfall ist **zerstörend** — lokale Änderungen gehen verloren. Nur in VM/Sandbox durchführen.

**Pass Criteria:** Installer läuft durch, dirty files werden resettet

---

### TC-08: Pfad mit Leerzeichen (Benutzername enthält Spaces)

| Feld | Wert |
|------|------|
| **ID** | TC-08 |
| **Priorität** | Mittel |
| **Setup** | Windows-Benutzer mit Leerzeichen im Namen (z.B. "John Doe") |
| **Typ** | Edge Case |

**Steps:**

1. Erstelle (oder verwende) einen Windows-User mit Leerzeichen im Namen
2. Führe install.ps1 aus

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Alle Aktionen erfolgreich | Keine Fehler |
| 2 | Keine "Path not found"-Fehler | Konsolenausgabe prüfen |
| 3 | Desktop Shortcut unter `C:\Users\John Doe\Desktop\Sidekick.lnk` | `Test-Path "$env:USERPROFILE\Desktop\Sidekick.lnk"` |
| 4 | WebUI öffnet sich | Browser-Fenster erscheint |
| 5 | `sidekick doctor` funktioniert | Keine Pfad-Fehler |

**Pass Criteria:** Keine Pfad-Probleme trotz Spaces

---

### TC-09: Desktop Shortcut — Inhalt und Funktionalität

| Feld | Wert |
|------|------|
| **ID** | TC-09 |
| **Priorität** | Mittel |
| **Setup** | TC-01 wurde erfolgreich ausgeführt |
| **Typ** | Funktional / Visuell |

**Steps:**

1. Öffne den Desktop-Ordner: `explorer $env:USERPROFILE\Desktop`
2. Rechtsklick auf `Sidekick.lnk` → Eigenschaften
3. Doppelklick auf `Sidekick.lnk`

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | `Sidekick.lnk` existiert | `Test-Path "$env:USERPROFILE\Desktop\Sidekick.lnk"` |
| 2 | Target: Pfad zu `sidekick.exe` (in `.venv\Scripts\`) | Eigenschaften-Dialog → Ziel |
| 3 | Arguments: `dashboard` | Eigenschaften-Dialog → Argumente |
| 4 | Icon: `sidekick-taskbar.ico` oder `favicon.ico` | Eigenschaften-Dialog → Icon |
| 5 | Doppelklick startet `sidekick dashboard` + WebUI | Browser öffnet `http://127.0.0.1:8787` |

**Pass Criteria:** Shortcut ist korrekt konfiguriert und funktional

---

### TC-10: WebUI Auto-Open — Health-Check

| Feld | Wert |
|------|------|
| **ID** | TC-10 |
| **Priorität** | **Hoch** |
| **Setup** | TC-01 wurde erfolgreich ausgeführt |
| **Typ** | Funktional / Smoke |

**Steps:**

1. Führe install.ps1 aus
2. Warte auf das automatisch geöffnete Browser-Fenster
3. Prüfe den Health-Endpoint

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Browser öffnet `http://127.0.0.1:8787` | Sichtbare Prüfung |
| 2 | `/health`-Endpoint retourniert HTTP 200 | `curl.exe http://127.0.0.1:8787/health` (in zweitem Terminal) |
| 3 | WebUI-Seite wird geladen (kein White-Screen) | Sichtbare Prüfung |
| 4 | Kein Port-Konflikt (8787 nicht belegt) | `netstat -an | findstr ":8787"` zeigt LISTENING |

**Pass Criteria:** WebUI erreichbar, Health-Endpoint antwortet mit 200

---

### TC-11: Kein Admin-Test (Standard-User ohne Rechte)

| Feld | Wert |
|------|------|
| **ID** | TC-11 |
| **Priorität** | **Hoch** |
| **Setup** | VM mit Standard-User (kein Administrator) |
| **Typ** | Sicherheit / Funktional |

**Steps:**

1. Melde dich als **Standard-Benutzer** an (kein Admin)
2. Versuche: `whoami /groups | findstr "S-1-16-12288"` → sollte **nichts** zurückgeben
3. Führe install.ps1 aus

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Keine UAC-Prompt | Kein Fenster "Möchten Sie zulassen..." |
| 2 | Keine "Access denied"-Fehler | Konsolenausgabe prüfen |
| 3 | Alle Dateien unter `%LOCALAPPDATA%` | `Get-ChildItem "$env:LOCALAPPDATA\sidekick" -Recurse` |
| 4 | Kein einziger File außerhalb von User-Profil | `Get-ChildItem C:\Program* -ErrorAction SilentlyContinue` → kein Sidekick |

**Pass Criteria:** Vollständige Installation ohne Admin-Rechte

---

### TC-12: Execution Policy Restricted

| Feld | Wert |
|------|------|
| **ID** | TC-12 |
| **Priorität** | Mittel |
| **Setup** | Standard Windows 10/11 mit Restricted Execution Policy |
| **Typ** | Edge Case / Dokumentation |

**Steps:**

1. Prüfe aktuelle Policy: `Get-ExecutionPolicy` → sollte `Restricted` sein
2. Führe den One-Liner aus: `irm ... | iex`
3. Wenn fehlschlägt, versuche den Workaround aus dem Troubleshooting:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/main/install.ps1 -OutFile install.ps1
   .\install.ps1
   ```

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Bei `Restricted`: Fehler "ExecutionPolicy" | Konsolenausgabe zeigt Fehler |
| 2 | Troubleshooting-Hinweis im Error | Link oder Hinweis auf Workaround |
| 3 | Workaround mit `-Scope Process` funktioniert | Installation läuft durch |
| 4 | Systemweite Policy bleibt `Restricted` | `Get-ExecutionPolicy` → `Restricted` |

**Pass Criteria:** Klare Fehlermeldung bei Restricted + funktionierender Workaround

---

### TC-13: Kein Internet — Graceful Failure

| Feld | Wert |
|------|------|
| **ID** | TC-13 |
| **Priorität** | Niedrig |
| **Setup** | VM ohne Internetverbindung (Netzwerkadapter deaktiviert) |
| **Typ** | Edge Case |

**Steps:**

1. Deaktiviere die Netzwerkverbindung in der VM
2. Führe install.ps1 aus (lokal oder via One-Liner)

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Klare Fehlermeldung bei fehlender Internetverbindung | Konsolenausgabe |
| 2 | Kein endloses Hängen | Timeout nach angemessener Zeit |
| 3 | Kein teilweiser Install (keine halbfertigen Downloads) | Keine korrupten Dateien |

**Pass Criteria:** Klarer Fehler, keine Korruption, kein Hängen

---

### TC-14: `-NoDoctor` Flag

| Feld | Wert |
|------|------|
| **ID** | TC-14 |
| **Priorität** | Niedrig |
| **Setup** | VM ohne vorherige Installation |
| **Typ** | Funktional |

**Steps:**

1. Führe install.ps1 mit `-NoDoctor` aus
2. Prüfe auf Post-Install-Check

**Expected Results:**

| # | Erwartung | Prüfmethode |
|---|-----------|-------------|
| 1 | Installation läuft vollständig durch | Kein Fehler |
| 2 | Kein `sidekick doctor`-Aufruf nach Install | Konsolenausgabe zeigt kein `doctor` |
| 3 | WebUI öffnet sich trotzdem | Browser-Fenster erscheint |

**Pass Criteria:** Installation ohne Post-Install-Check, aber mit WebUI

---

## Test-Matrix (Übersicht)

| ID | Testfall | Priorität | Typ | Setup-Zeit | Durchführung |
|----|----------|-----------|-----|------------|-------------|
| TC-01 | Fresh Windows Install | 🔴 Hoch | Smoke | 5 min | 15 min |
| TC-02 | Idempotenz (2. Ausführung) | 🔴 Hoch | Regression | 0 min (TC-01) | 10 min |
| TC-03 | UpdateOnly | 🔴 Hoch | Funktional | 0 min (TC-01) | 5 min |
| TC-04 | Ohne API-Key (Doctor) | 🟡 Mittel | Funktional | 0 min (TC-01) | 5 min |
| TC-05 | Git fehlt (PortableGit) | 🔴 Hoch | Edge Case | 5 min | 15 min |
| TC-06 | Python/uv fehlt | 🔴 Hoch | Edge Case | 5 min | 15 min |
| TC-07 | Dirty Tree | 🟡 Mittel | Zerstörend | 0 min (TC-01) | 10 min |
| TC-08 | Pfad mit Leerzeichen | 🟡 Mittel | Edge Case | 15 min | 15 min |
| TC-09 | Desktop Shortcut | 🟡 Mittel | Visuell | 0 min (TC-01) | 5 min |
| TC-10 | WebUI Auto-Open | 🔴 Hoch | Smoke | 0 min (TC-01) | 5 min |
| TC-11 | Kein Admin | 🔴 Hoch | Sicherheit | 15 min | 15 min |
| TC-12 | Execution Policy | 🟡 Mittel | Edge Case | 5 min | 10 min |
| TC-13 | Kein Internet | 🟢 Niedrig | Edge Case | 5 min | 10 min |
| TC-14 | NoDoctor Flag | 🟢 Niedrig | Funktional | 0 min (TC-01) | 5 min |

### Empfohlene Test-Reihenfolge (Minimal)

Für einen schnellen Smoke-Test reichen folgende Testfälle in dieser Reihenfolge:

1. **TC-01** — Fresh Install (Grundlage für alles Weitere)
2. **TC-02** — Idempotenz (wichtig für Updates)
3. **TC-03** — UpdateOnly (wichtig für den Update-Pfad)
4. **TC-10** — WebUI Auto-Open (Kernerwartung)
5. **TC-11** — Kein Admin (Sicherheitsversprechen)

### Test-Reihenfolge (Vollständig)

Für einen vollständigen Testlauf inklusive Edge Cases:

1. TC-12 (Execution Policy — frisches System)
2. TC-13 (Kein Internet — frisches System, danach Netzwerk wieder aktivieren)
3. TC-05 (Git fehlt — frisches System)
4. TC-06 (Python/uv fehlt — frisches System)
5. TC-01 (Fresh Install — saubere VM)
6. TC-02 (Idempotenz)
7. TC-03 (UpdateOnly)
8. TC-04 (Doctor ohne API-Key)
9. TC-07 (Dirty Tree — VM-Snapshot zurücksetzen)
10. TC-08 (Pfad mit Leerzeichen — spezielle VM)
11. TC-09 (Desktop Shortcut)
12. TC-10 (WebUI Auto-Open)
13. TC-11 (Kein Admin — spezielle VM)
14. TC-14 (NoDoctor)

---

## Bekannte Probleme / Notes

- **TC-07 (Dirty Tree):** Zerstörend — Datenverlust möglich. Nur in VM/Sandbox durchführen.
- **TC-08 (Leerzeichen):** Benötigt einen Windows-User mit Leerzeichen im Namen. Alternativ kann der Pfad manuell manipuliert werden.
- **TC-13 (Kein Internet):** `Invoke-RestMethod` hat **kein** explizites Timeout — der Test kann bis zu 2 Minuten dauern (Default-Timeout von PowerShell).
- **Test-Umgebung:** Für TC-05, TC-06, TC-07, TC-08 und TC-11 werden separate VMs oder Snapshots empfohlen, da sich die Setups gegenseitig beeinflussen.

---

## Dokumentation

| Dokument | Pfad |
|----------|------|
| Release Notes | `docs/releases/v0.6.0.md` |
| Installer Safety Audit | `docs/audits/windows-installer-v0.6.0.md` |
| Troubleshooting (Windows) | `docs/troubleshooting.md` |
