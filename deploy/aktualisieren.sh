#!/usr/bin/env bash
# Spielt neuen Code ein und startet die Plattform neu.
#
#   Aufruf als root aus dem entpackten Code-Ordner:  bash deploy/aktualisieren.sh
#   Vom Windows-PC aus:                              deploy\hochladen.ps1 -Server <IP>
#
# Vorher wird die Datenbank gesichert. Der bisherige Code bleibt in /opt/nachbarstrom.vorher,
# startet die neue Version nicht, kann man mit j sofort zurückgehen.
set -euo pipefail

QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODE=/opt/nachbarstrom
VENV=/opt/nachbarstrom-venv
DATEN=/var/lib/nachbarstrom

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen."; exit 1; }
[[ -f /etc/nachbarstrom/nachbarstrom.env ]] || { echo "Zuerst deploy/einrichten.sh ausführen."; exit 1; }
[[ "$QUELLE" != "$CODE" ]] || { echo "Bitte aus dem hochgeladenen Code-Ordner aufrufen, nicht aus $CODE."; exit 1; }

if [[ -f $DATEN/nachbarstrom.db ]]; then
  echo "== Sicherung vor dem Update"
  install -m 755 "$QUELLE/deploy/sicherung.sh" /usr/local/sbin/nachbarstrom-sicherung
  runuser -u nachbarstrom -- /usr/local/sbin/nachbarstrom-sicherung
fi

echo "== Pakete"
"$VENV/bin/pip" install -q --disable-pip-version-check -r "$QUELLE/requirements.txt"

echo "== Code"
rm -rf "$CODE.neu"
rsync -a --delete \
  --exclude .git --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.venv' --exclude 'venv' \
  --exclude '*.db' --exclude 'nachbarstrom.geheimnis' --exclude testdaten --exclude tests \
  "$QUELLE/" "$CODE.neu/"
"$VENV/bin/python" -m compileall -q "$CODE.neu/app" >/dev/null || true
chown -R root:root "$CODE.neu"
chmod -R u=rwX,go=rX "$CODE.neu"
if [[ -d $CODE ]]; then
  rm -rf "$CODE.vorher"
  mv "$CODE" "$CODE.vorher"
fi
mv "$CODE.neu" "$CODE"

echo "== Neustart"
systemctl restart nachbarstrom
for _ in $(seq 1 30); do
  curl -fsS -o /dev/null http://127.0.0.1:8000/ && break
  sleep 1
done
if curl -fsS -o /dev/null http://127.0.0.1:8000/; then
  echo "Läuft."
else
  echo "Die neue Version startet nicht. Letzte Meldungen:"
  journalctl -u nachbarstrom -n 30 --no-pager || true
  if [[ -d $CODE.vorher ]] && read -r -p "Zurück auf die vorige Version? [j/n] " a && [[ "$a" == [jJ] ]]; then
    rm -rf "$CODE"
    mv "$CODE.vorher" "$CODE"
    systemctl restart nachbarstrom
    echo "Vorige Version läuft wieder. Die Sicherung von eben liegt in $DATEN/sicherung."
  fi
  exit 1
fi
