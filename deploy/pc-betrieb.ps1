<#
Startet oder beendet Fix Nachbarstrom im Dauerbetrieb am PC (eingerichtet mit pc-einrichten.ps1).

  Doppelklick auf "Dauerbetrieb neu starten.bat" (nach neuem Code oder geänderten Einstellungen)
  powershell -ExecutionPolicy Bypass -File deploy\pc-betrieb.ps1            # starten
  powershell -ExecutionPolicy Bypass -File deploy\pc-betrieb.ps1 -Beenden

Vor jedem Start wird die Datenbank nach betrieb\sicherung gesichert (30 Tage aufgehoben).
Protokoll: betrieb\protokoll.txt und betrieb\protokoll-fehler.txt
#>
param([switch]$Beenden, [switch]$Neustart, [switch]$Dauerhaft)
$ErrorActionPreference = "Stop"
$wurzel = Split-Path $PSScriptRoot -Parent
Set-Location $wurzel
$PORT = 8010   # 8000 bleibt frei für den Testbetrieb (start.ps1)
$betrieb = Join-Path $wurzel "betrieb"
$env:NACHBARSTROM_EINSTELLUNGEN = Join-Path $betrieb "nachbarstrom.env"
$python = Join-Path $wurzel ".venv\Scripts\python.exe"
if (-not (Test-Path $env:NACHBARSTROM_EINSTELLUNGEN)) { throw "Zuerst .\deploy\pc-einrichten.ps1 ausführen." }

function Laufend {
    $verbindung = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($verbindung) { Get-Process -Id $verbindung.OwningProcess -ErrorAction SilentlyContinue }
}

if ($Beenden -or $Neustart) {
    $p = Laufend
    if ($p) { Stop-Process -Id $p.Id -Force; Start-Sleep -Seconds 2; Write-Host "Beendet." } else { Write-Host "Lief nicht." }
    if ($Dauerhaft) {
        Disable-ScheduledTask -TaskName "Fix Nachbarstrom" -ErrorAction SilentlyContinue | Out-Null
        Write-Host "Autostart abgeschaltet."
    }
    if ($Beenden) { return }
}
if (Laufend) { Write-Host "Läuft schon: http://localhost:$PORT/verwaltung"; return }

# Pakete nur nachinstallieren, wenn sich requirements.txt geändert hat (spart den Weg ins Internet)
$merker = Join-Path $betrieb "pakete.stand"
$stand = (Get-FileHash requirements.txt).Hash
if (-not (Test-Path $merker) -or (Get-Content $merker) -ne $stand) {
    & $python -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -eq 0) { Set-Content -Path $merker -Value $stand }
}
& $python -m app.sicherung
if ($LASTEXITCODE -ne 0) { throw "Sicherung hat nicht geklappt, die Plattform wurde nicht gestartet." }

$protokoll = Join-Path $betrieb "protokoll.txt"
$fehler = Join-Path $betrieb "protokoll-fehler.txt"
foreach ($f in $protokoll, $fehler) { if (Test-Path $f) { Move-Item $f "$f.vorher" -Force } }
Start-Process -FilePath $python -WorkingDirectory $wurzel -WindowStyle Hidden `
    -RedirectStandardOutput $protokoll -RedirectStandardError $fehler `
    -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port $PORT --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header"

$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest "http://127.0.0.1:$PORT/" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { }
}
if (-not $ok) {
    Write-Host "Startet nicht. Letzte Meldungen:" -ForegroundColor Red
    if (Test-Path $fehler) { Get-Content $fehler -Tail 20 }
    exit 1
}
$adresse = (Select-String -Path $env:NACHBARSTROM_EINSTELLUNGEN -Pattern '^NACHBARSTROM_BASE_URL=(.+)$').Matches.Groups[1].Value
Write-Host "Läuft. Verwaltung am PC: http://localhost:$PORT/verwaltung" -ForegroundColor Green
Write-Host "Von außen (sobald der Tunnel eingerichtet ist): $adresse"
