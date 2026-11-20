<#
Lädt den Code vom PC auf den Server und spielt ihn dort ein.

  .\deploy\hochladen.ps1 -Server 203.0.113.10 -Einrichten   # beim ersten Mal
  .\deploy\hochladen.ps1 -Server 203.0.113.10               # bei jedem Update

Braucht Git und den SSH-Schlüssel, der beim Server hinterlegt ist (Windows hat ssh und scp
eingebaut). Hochgeladen wird nur der eingecheckte Stand (git archive), nie die Datenbank,
der Schlüssel oder Testdaten vom PC.
#>
param(
    [Parameter(Mandatory = $true)] [string]$Server,
    [string]$Benutzer = "root",
    [switch]$Einrichten
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
git archive --format=tar.gz -o $archiv HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive hat nicht geklappt." }

$ziel = "$Benutzer@$Server"
$sudo = if ($Benutzer -eq "root") { "" } else { "sudo " }
$skript = if ($Einrichten) { "einrichten.sh" } else { "aktualisieren.sh" }

Write-Host "Lade Stand $stand ($zweig) auf $Server ..."
Push-Location $env:TEMP   # relativer Pfad, damit scp das Laufwerk "C:" nicht für einen Server hält
scp $datei "${ziel}:nachbarstrom.tar.gz"
$ok = $LASTEXITCODE -eq 0
Remove-Item $datei
Pop-Location
if (-not $ok) { throw "Hochladen hat nicht geklappt." }

ssh -t $ziel "rm -rf ~/nachbarstrom-code && mkdir ~/nachbarstrom-code && tar -xzf ~/nachbarstrom.tar.gz -C ~/nachbarstrom-code && rm ~/nachbarstrom.tar.gz && ${sudo}bash ~/nachbarstrom-code/deploy/$skript"
if ($LASTEXITCODE -ne 0) { throw "Auf dem Server hat etwas nicht geklappt, siehe Meldungen oben." }
