# Startet Fix Nachbarstrom lokal unter Windows.
# Beim ersten Start wird alles eingerichtet. Das Passwort für die Verwaltung
# wird bei jedem Start abgefragt und nirgends gespeichert.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Host "Python ist nicht installiert. Bitte von https://www.python.org/downloads/ installieren" -ForegroundColor Red
    Write-Host "(beim Installieren 'Add python.exe to PATH' anhaken) und dann neu starten."
    Read-Host "Enter zum Beenden"
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Erster Start: richte die Umgebung ein (dauert 1-2 Minuten) ..."
    & $python.Source -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
}
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

if (-not (Test-Path "nachbarstrom.db")) {
    $demo = Read-Host "Testbetrieb mit erfundenen Kunden und einem fiktiven Jahr anlegen? (j/n)"
    if ($demo -eq "j") { & .\.venv\Scripts\python.exe demo_daten.py }
} else {
    $demo = Read-Host "Testbetrieb neu laden? Achtung: loescht ALLE Daten in der Plattform (j/n)"
    if ($demo -eq "j") {
        Write-Host "Lege Testbetrieb an, das dauert etwa eine Minute ..."
        & .\.venv\Scripts\python.exe demo_daten.py --neu
    }
}

$sicher = Read-Host "Passwort fuer die Verwaltung festlegen (Benutzer: admin)" -AsSecureString
$env:NACHBARSTROM_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sicher))

Write-Host ""
Write-Host "Fix Nachbarstrom laeuft auf http://localhost:8000" -ForegroundColor Green
Write-Host "Verwaltung: http://localhost:8000/verwaltung"
Write-Host "Zum Beenden dieses Fenster schliessen."
Start-Process "http://localhost:8000/verwaltung"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
