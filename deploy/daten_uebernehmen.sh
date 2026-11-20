#!/usr/bin/env bash
# Übernimmt Datenbank, Schlüssel für die persönlichen Links und Firmendaten aus dem PC-Betrieb.
# Wird von deploy\hochladen.ps1 -Daten aufgerufen, nach einrichten.sh oder aktualisieren.sh.
#
#   bash deploy/daten_uebernehmen.sh <Ordner mit nachbarstrom.db, nachbarstrom.geheimnis, nachbarstrom.env>
#
# Die Daten auf dem Server werden vorher gesichert. Mit demselben Schlüssel und derselben Domain
# bleiben alle verschickten Links gültig.
set -euo pipefail

VOM_PC="${1:?Ordner mit den Daten vom PC angeben}"
DATEN=/var/lib/nachbarstrom
UMGEBUNG=/etc/nachbarstrom/nachbarstrom.env

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen."; exit 1; }
[[ -f $UMGEBUNG ]] || { echo "Zuerst deploy/einrichten.sh ausführen."; exit 1; }
for f in nachbarstrom.db nachbarstrom.geheimnis; do
  [[ -f $VOM_PC/$f ]] || { echo "$VOM_PC/$f fehlt."; exit 1; }
done

python3 - "$VOM_PC/nachbarstrom.db" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
ergebnis = db.execute("pragma integrity_check").fetchone()[0]
if ergebnis != "ok":
    sys.exit(f"Die Datenbank vom PC ist beschädigt: {ergebnis}")
teilnehmer = db.execute("select count(*) from teilnehmer").fetchone()[0]
print(f"Datenbank vom PC ist in Ordnung ({teilnehmer} Teilnehmer).")
PY

read -r -p "Daten vom PC übernehmen? Was jetzt auf dem Server liegt, wird gesichert und ersetzt. [j/n] " a
[[ "$a" == [jJ] ]] || { echo "Nichts geändert."; exit 1; }

systemctl stop nachbarstrom
if [[ -f $DATEN/nachbarstrom.db ]]; then
  runuser -u nachbarstrom -- /usr/local/sbin/nachbarstrom-sicherung
fi
rm -f "$DATEN/nachbarstrom.db-wal" "$DATEN/nachbarstrom.db-shm" "$DATEN/nachbarstrom.db-journal"
install -o nachbarstrom -g nachbarstrom -m 600 "$VOM_PC/nachbarstrom.db" "$DATEN/nachbarstrom.db"
install -o nachbarstrom -g nachbarstrom -m 600 "$VOM_PC/nachbarstrom.geheimnis" "$DATEN/nachbarstrom.geheimnis"
if [[ -f $VOM_PC/nachbarstrom.env ]]; then
  (cd /opt/nachbarstrom && /opt/nachbarstrom-venv/bin/python -m app.einstellungen uebernehmen "$VOM_PC/nachbarstrom.env" "$UMGEBUNG")
fi

systemctl start nachbarstrom
for _ in $(seq 1 30); do
  curl -fsS -o /dev/null http://127.0.0.1:8000/ && break
  sleep 1
done
curl -fsS -o /dev/null http://127.0.0.1:8000/ || { echo "Startet nicht:"; journalctl -u nachbarstrom -n 30 --no-pager; exit 1; }

rm -rf "$VOM_PC"   # keine Kundendaten im Home-Ordner liegen lassen
echo "Daten übernommen, die Plattform läuft."
echo "Jetzt den DNS-Eintrag auf diesen Server umstellen und NACHBARSTROM_HOSTER in $UMGEBUNG anpassen (README: Umzug vom PC)."
