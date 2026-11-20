#!/usr/bin/env bash
# Sichert Datenbank und Schlüssel für die persönlichen Links nach /var/lib/nachbarstrom/sicherung
# und löscht dort Sicherungen, die älter als 30 Tage sind. Läuft als Benutzer nachbarstrom.
# Die Sicherung liegt auf demselben Server: für den Fall, dass der Server ausfällt, zusätzlich
# die Sicherungen des Anbieters einschalten oder die Dateien regelmäßig herunterladen.
set -euo pipefail
umask 077

DATEN=/var/lib/nachbarstrom
ZIEL=$DATEN/sicherung
STAND=$(date +%Y-%m-%d_%H%M%S)
cd "$DATEN"

[[ -f $DATEN/nachbarstrom.db ]] || { echo "Keine Datenbank gefunden, nichts zu sichern."; exit 0; }
mkdir -p "$ZIEL"
# Sichere Kopie auch während die Plattform läuft (SQLite-Backup, kein einfaches cp)
python3 - "$DATEN/nachbarstrom.db" "$ZIEL/nachbarstrom-$STAND.db" <<'PY'
import sqlite3, sys
quelle, ziel = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
quelle.backup(ziel)
ziel.close()
quelle.close()
PY
if [[ -f $DATEN/nachbarstrom.geheimnis ]]; then
  cp "$DATEN/nachbarstrom.geheimnis" "$ZIEL/nachbarstrom-$STAND.geheimnis"
fi
find "$ZIEL" -maxdepth 1 -type f -name 'nachbarstrom-*' -mtime +30 -delete
echo "Gesichert: $ZIEL/nachbarstrom-$STAND.db"
