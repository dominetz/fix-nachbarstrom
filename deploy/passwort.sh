#!/usr/bin/env bash
# Neues Passwort für die Verwaltung setzen. Aufruf als root: bash /opt/nachbarstrom/deploy/passwort.sh
set -euo pipefail
UMGEBUNG=/etc/nachbarstrom/nachbarstrom.env
[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen."; exit 1; }
HASH="$(cd /opt/nachbarstrom && /opt/nachbarstrom-venv/bin/python -m app.sicherheit)"
[[ "$HASH" =~ ^pbkdf2_sha256:[0-9]+:[0-9a-f]+:[0-9a-f]+$ ]] || { echo "Kein gültiger Hash."; exit 1; }
if grep -q '^NACHBARSTROM_ADMIN_PASSWORD_HASH=' "$UMGEBUNG"; then
  sed -i "s|^NACHBARSTROM_ADMIN_PASSWORD_HASH=.*|NACHBARSTROM_ADMIN_PASSWORD_HASH=$HASH|" "$UMGEBUNG"
else
  echo "NACHBARSTROM_ADMIN_PASSWORD_HASH=$HASH" >> "$UMGEBUNG"
fi
systemctl restart nachbarstrom
echo "Neues Passwort gilt."
