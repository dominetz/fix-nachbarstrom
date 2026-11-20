<#
Lädt den Code vom PC auf den Server und spielt ihn dort ein.

  powershell -ExecutionPolicy Bypass -File deploy\hochladen.ps1 -Server 203.0.113.10 -Einrichten
  powershell -ExecutionPolicy Bypass -File deploy\hochladen.ps1 -Server 203.0.113.10
  powershell -ExecutionPolicy Bypass -File deploy\hochladen.ps1 -Server 203.0.113.10 -Einrichten -Daten

Erste Zeile beim ersten Mal, zweite bei jedem Update, dritte für den Umzug vom PC-Betrieb.

Mit -Daten kommen Datenbank, Schlüssel und Firmendaten aus dem PC-Betrieb (Ordner betrieb\) mit.
Der PC-Betrieb wird dafür beendet und sein Autostart abgeschaltet, damit danach nur noch der
Server Daten annimmt.

Braucht Git und den SSH-Schlüssel, der beim Server hinterlegt ist (Windows hat ssh und scp
eingebaut). Hochgeladen wird nur der eingecheckte Stand (git archive), nie die Datenbank,
der Schlüssel oder Testdaten vom PC.
#>
param(
    [Parameter(Mandatory = $true)] [string]$Server,
    [string]$Benutzer = "root",
    [switch]$Einrichten,
    [switch]$Daten
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (git status --porcelain --untracked-files=no) {
    Write-Host "Achtung: Änderungen, die nicht eingecheckt sind, werden nicht hochgeladen." -ForegroundColor Yellow
}
$stand = git rev-parse --short HEAD
$zweig = git rev-parse --abbrev-ref HEAD
$datei = "nachbarstrom-$stand.tar.gz"
$archiv = Join-Path $env:TEMP $datei
# Ohne CRLF-Umwandlung, sonst laufen die Skripte auf dem Server nicht
git -c core.autocrlf=false -c core.eol=lf archive --format=tar.gz -o $archiv HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive hat nicht geklappt." }

$ziel = "$Benutzer@$Server"
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$betrieb = Join-Path (Get-Location) "betrieb"
if ($Daten -and -not (Test-Path (Join-Path $betrieb "nachbarstrom.db"))) {
    throw "Kein PC-Betrieb gefunden (betrieb\nachbarstrom.db fehlt)."
}
$sudo = if ($Benutzer -eq "root") { "" } else { "sudo " }
$skript = if ($Einrichten) { "einrichten.sh" } else { "aktualisieren.sh" }

Write-Host "Lade Stand $stand ($zweig) auf $Server ..."
Push-Location $env:TEMP   # relativer Pfad, damit scp das Laufwerk "C:" nicht für einen Server hält
scp $datei "${ziel}:nachbarstrom.tar.gz"
$ok = $LASTEXITCODE -eq 0
Remove-Item $datei
Pop-Location
if (-not $ok) { throw "Hochladen hat nicht geklappt." }

$befehl = "rm -rf ~/nachbarstrom-code && mkdir ~/nachbarstrom-code && tar -xzf ~/nachbarstrom.tar.gz -C ~/nachbarstrom-code && rm ~/nachbarstrom.tar.gz"
if ($Daten) {
    Write-Host "Beende den PC-Betrieb und packe die Daten ..."
    & (Join-Path $PSScriptRoot "pc-betrieb.ps1") -Beenden -Dauerhaft
    $env:NACHBARSTROM_EINSTELLUNGEN = Join-Path $betrieb "nachbarstrom.env"
    Push-Location $env:TEMP
    Remove-Item -Recurse -Force "nachbarstrom-daten", "nachbarstrom-daten.tar.gz" -ErrorAction SilentlyContinue
    Push-Location (Split-Path $PSScriptRoot -Parent)
    & $python -m app.sicherung --umzug (Join-Path $env:TEMP "nachbarstrom-daten")
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) { Pop-Location; throw "Daten konnten nicht kopiert werden." }
    Copy-Item $env:NACHBARSTROM_EINSTELLUNGEN "nachbarstrom-daten\nachbarstrom.env"
    tar -czf "nachbarstrom-daten.tar.gz" -C "nachbarstrom-daten" .
    scp "nachbarstrom-daten.tar.gz" "${ziel}:nachbarstrom-daten.tar.gz"
    $ok = $LASTEXITCODE -eq 0
    Remove-Item -Recurse -Force "nachbarstrom-daten", "nachbarstrom-daten.tar.gz"
    Pop-Location
    if (-not $ok) { throw "Hochladen der Daten hat nicht geklappt." }
    $befehl += " && rm -rf ~/nachbarstrom-daten && mkdir -m 700 ~/nachbarstrom-daten && tar -xzf ~/nachbarstrom-daten.tar.gz -C ~/nachbarstrom-daten && rm ~/nachbarstrom-daten.tar.gz"
    $befehl += " && ${sudo}env NACHBARSTROM_MIT_DATEN=1 bash ~/nachbarstrom-code/deploy/$skript"
    $befehl += " && ${sudo}bash ~/nachbarstrom-code/deploy/daten_uebernehmen.sh ~/nachbarstrom-daten"
} else {
    $befehl += " && ${sudo}bash ~/nachbarstrom-code/deploy/$skript"
}
ssh -t $ziel $befehl
if ($LASTEXITCODE -ne 0) {
    if ($Daten) {
        Write-Host "Der PC-Betrieb ist beendet, seine Daten sind unverändert. Wieder starten: Doppelklick auf" -ForegroundColor Yellow
        Write-Host "'Dauerbetrieb neu starten.bat', Autostart in der Aufgabenplanung wieder aktivieren." -ForegroundColor Yellow
    }
    throw "Auf dem Server hat etwas nicht geklappt, siehe Meldungen oben."
}
