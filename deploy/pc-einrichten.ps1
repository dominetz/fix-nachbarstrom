<#
Übergang bis zum eigenen Server: Fix Nachbarstrom läuft dauerhaft auf diesem PC.

  Doppelklick auf "Dauerbetrieb einrichten.bat" im Ordner der Plattform

Die echten Daten liegen im Ordner betrieb\ (getrennt vom Testbetrieb in nachbarstrom.db), die
Einstellungen in betrieb\nachbarstrom.env, gleich aufgebaut wie später auf dem Server. So lassen
sich Daten, Schlüssel und Firmendaten beim Umzug einfach mitnehmen (hochladen.ps1 -Daten).

Fragt Domain und Verwaltungspasswort ab (gespeichert wird nur ein Hash) und einzeln, ob die
Plattform beim Anmelden von selbst startet und ob der Ruhezustand aus soll. Von außen erreichbar
wird sie erst über den Tunnel, siehe README.
#>
$ErrorActionPreference = "Stop"
$wurzel = Split-Path $PSScriptRoot -Parent
Set-Location $wurzel
$betrieb = Join-Path $wurzel "betrieb"
$datei = Join-Path $betrieb "nachbarstrom.env"

function Frage([string]$text) { (Read-Host "$text (j/n)") -eq "j" }

if (-not (Test-Path ".venv")) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $py) { throw "Python ist nicht installiert (https://www.python.org/downloads/)." }
    Write-Host "Richte die Python-Umgebung ein (1-2 Minuten) ..."
    & $py.Source -m venv .venv
}
$python = Join-Path $wurzel ".venv\Scripts\python.exe"
& $python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Pakete konnten nicht installiert werden." }

New-Item -ItemType Directory -Force -Path $betrieb | Out-Null
if (Test-Path $datei) {
    Write-Host "$datei gibt es schon, bleibt unverändert."
} else {
    $domain = Read-Host "Domain der Plattform (Enter = nachbarstrom.fix-pv.com)"
    if (-not $domain) { $domain = "nachbarstrom.fix-pv.com" }
    $domain = $domain.Trim().ToLower()
    if ($domain -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$') {
        throw "$domain sieht nicht nach einer Domain aus."
    }
    Write-Host "Passwort für die Verwaltung festlegen (Benutzer: admin, mindestens 12 Zeichen). Gespeichert wird nur ein Hash."
    $hash = ""
    foreach ($versuch in 1..3) {
        $hash = (& $python -m app.sicherheit | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $hash -match '^pbkdf2_sha256:') { break }
        $hash = ""
    }
    if (-not $hash) { throw "Kein Passwort festgelegt." }
    $zeilen = @(
        "# Fix Nachbarstrom am PC (Übergang bis zum Server). Nach Änderungen: 'Dauerbetrieb neu starten.bat'",
        "NACHBARSTROM_DB=sqlite:///betrieb/nachbarstrom.db",
        "NACHBARSTROM_SECRET_FILE=betrieb/nachbarstrom.geheimnis",
        "NACHBARSTROM_TESTDATEN=betrieb/testdaten",
        "NACHBARSTROM_BASE_URL=https://$domain",
        "NACHBARSTROM_ADMIN_USER=admin",
        "NACHBARSTROM_ADMIN_PASSWORD_HASH=$hash",
        "",
        "# Firmendaten für Impressum, Datenschutz und Rechnungen. Werte in Anführungszeichen, ohne `$.",
        "# Sie kommen beim Umzug auf den Server mit.",
        '#NACHBARSTROM_FIRMA_ADRESSE="Straße Nr, PLZ Ort"',
        '#NACHBARSTROM_FIRMA_FN=""',
        '#NACHBARSTROM_FIRMA_GERICHT=""',
        '#NACHBARSTROM_FIRMA_GF=""',
        '#NACHBARSTROM_FIRMA_TELEFON=""',
        '#NACHBARSTROM_FIRMA_UID=""',
        '#NACHBARSTROM_FIRMA_IBAN=""',
        "# Steht in der Datenschutzerklärung. Am PC mit Tunnel z. B. den Tunnel-Anbieter nennen.",
        '#NACHBARSTROM_HOSTER=""',
        "",
        "# Erst wenn Impressum und Datenschutz geprüft sind, sonst steht dort ein Entwurfshinweis:",
        "#NACHBARSTROM_RECHTSTEXTE_GEPRUEFT=ja"
    )
    [IO.File]::WriteAllLines($datei, $zeilen, (New-Object Text.UTF8Encoding $false))
    Write-Host "Einstellungen gespeichert: $datei"
}

if (Frage "Plattform beim Anmelden an diesem PC automatisch starten?") {
    try {
        $aktion = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\pc-betrieb.ps1`""
        $ausloeser = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $optionen = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName "Fix Nachbarstrom" -Action $aktion -Trigger $ausloeser -Settings $optionen `
            -Description "Startet Fix Nachbarstrom (Übergang bis zum Server)" -Force | Out-Null
        Write-Host "Startet ab jetzt bei jeder Anmeldung. Abschalten: Aufgabenplanung, Aufgabe 'Fix Nachbarstrom'."
    } catch {
        Write-Host "Autostart ging nicht ($($_.Exception.Message)). Dann nach dem Anmelden 'Dauerbetrieb neu starten.bat' doppelklicken." -ForegroundColor Yellow
    }
}

if (Frage "Ruhezustand ausschalten, solange der PC am Strom hängt? Sonst ist die Plattform im Ruhezustand nicht erreichbar") {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
}

& (Join-Path $PSScriptRoot "pc-betrieb.ps1")
