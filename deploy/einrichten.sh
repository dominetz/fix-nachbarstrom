#!/usr/bin/env bash
# Richtet Fix Nachbarstrom auf einem frischen Server ein (Ubuntu 24.04 oder Debian 12).
#
#   Aufruf als root aus dem entpackten Code-Ordner:  bash deploy/einrichten.sh
#   Vom Windows-PC aus geht das in einem Schritt:   deploy\hochladen.ps1 -Server <IP> -Einrichten
#
# Fragt die Domain und das Verwaltungspasswort ab. Das Passwort wird nur als Hash gespeichert.
# Testbetrieb, Firewall und tägliche Sicherung gibt es nur, wenn du sie mit j bestätigst.
set -euo pipefail

QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATEN=/var/lib/nachbarstrom
KONFIG=/etc/nachbarstrom
UMGEBUNG=$KONFIG/nachbarstrom.env
VENV=/opt/nachbarstrom-venv
BENUTZER=nachbarstrom

frage() { local antwort; read -r -p "$1 [j/n] " antwort; [[ "$antwort" == [jJ] ]]; }

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen (sudo bash deploy/einrichten.sh)."; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
case "$ID-$VERSION_ID" in
  ubuntu-24.04|debian-12) ;;
  *) echo "Gebaut für Ubuntu 24.04 und Debian 12, gefunden: $PRETTY_NAME"; frage "Trotzdem weiter?" || exit 1 ;;
esac

read -r -p "Domain der Plattform (z. B. nachbarstrom.fix-pv.com): " DOMAIN
DOMAIN="${DOMAIN,,}"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] \
  || { echo "$DOMAIN sieht nicht nach einer Domain aus."; exit 1; }

echo "== Pakete installieren"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv sqlite3 caddy rsync curl

echo "== Benutzer und Ordner"
id -u "$BENUTZER" >/dev/null 2>&1 \
  || useradd --system --home-dir "$DATEN" --shell /usr/sbin/nologin "$BENUTZER"
install -d -o "$BENUTZER" -g "$BENUTZER" -m 700 "$DATEN" "$DATEN/sicherung" "$DATEN/testdaten"
install -d -o root -g "$BENUTZER" -m 750 "$KONFIG"
[[ -d $VENV ]] || python3 -m venv "$VENV"

echo "== Einstellungen"
if [[ -f $UMGEBUNG ]]; then
  echo "$UMGEBUNG gibt es schon, bleibt unverändert."
else
  "$VENV/bin/pip" install -q --disable-pip-version-check -r "$QUELLE/requirements.txt"
  echo "Passwort für die Verwaltung festlegen (Benutzer: admin). Es wird nur als Hash gespeichert."
  HASH="$(cd "$QUELLE" && "$VENV/bin/python" -m app.sicherheit)"
  umask 027
  cat > "$UMGEBUNG" <<KONF
# Fix Nachbarstrom: Einstellungen. Nach jeder Änderung: systemctl restart nachbarstrom
NACHBARSTROM_DB=sqlite:///$DATEN/nachbarstrom.db
NACHBARSTROM_SECRET_FILE=$DATEN/nachbarstrom.geheimnis
NACHBARSTROM_TESTDATEN=$DATEN/testdaten
NACHBARSTROM_BASE_URL=https://$DOMAIN
NACHBARSTROM_ADMIN_USER=admin
NACHBARSTROM_ADMIN_PASSWORD_HASH=$HASH

# Firmendaten für Impressum, Datenschutz und Rechnungen. Werte in Anführungszeichen, ohne \$.
#NACHBARSTROM_FIRMA_ADRESSE="Straße Nr, PLZ Ort"
#NACHBARSTROM_FIRMA_FN=""
#NACHBARSTROM_FIRMA_GERICHT=""
#NACHBARSTROM_FIRMA_GF=""
#NACHBARSTROM_FIRMA_TELEFON=""
#NACHBARSTROM_FIRMA_UID=""
#NACHBARSTROM_FIRMA_IBAN=""
#NACHBARSTROM_HOSTER=""

# Erst wenn Impressum und Datenschutz geprüft sind, sonst steht dort ein Entwurfshinweis:
#NACHBARSTROM_RECHTSTEXTE_GEPRUEFT=ja
KONF
  umask 022
  chown root:"$BENUTZER" "$UMGEBUNG"
  chmod 640 "$UMGEBUNG"
fi

echo "== Dienste"
install -m 644 "$QUELLE/deploy/nachbarstrom.service" /etc/systemd/system/nachbarstrom.service
install -m 644 "$QUELLE/deploy/nachbarstrom-sicherung.service" /etc/systemd/system/nachbarstrom-sicherung.service
install -m 644 "$QUELLE/deploy/nachbarstrom-sicherung.timer" /etc/systemd/system/nachbarstrom-sicherung.timer
install -m 755 "$QUELLE/deploy/sicherung.sh" /usr/local/sbin/nachbarstrom-sicherung
systemctl daemon-reload
systemctl enable nachbarstrom.service

echo "== Code einspielen und starten"
bash "$QUELLE/deploy/aktualisieren.sh"

echo "== HTTPS (Caddy)"
if [[ -f /etc/caddy/Caddyfile ]]; then
  cp -p /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.vorher-$(date +%Y%m%d%H%M%S)"
fi
sed "s/DOMAIN_EINTRAGEN/$DOMAIN/" "$QUELLE/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile >/dev/null
systemctl enable caddy
systemctl reload-or-restart caddy
echo "Caddy holt das Zertifikat für $DOMAIN selbst, sobald der DNS-Eintrag auf diesen Server zeigt."

if frage "Firewall einschalten (offen bleiben nur SSH, HTTP und HTTPS)?"; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q ufw
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
fi

if frage "Täglich um 02:30 Serverzeit sichern (Datenbank und Schlüssel, 30 Tage aufheben)?"; then
  systemctl enable --now nachbarstrom-sicherung.timer
fi

if frage "Erfundenen Testbetrieb laden (nur zum Ausprobieren, keine echten Kunden)?"; then
  runuser -u "$BENUTZER" -- bash -c "set -a; . '$UMGEBUNG'; set +a; cd /opt/nachbarstrom && '$VENV/bin/python' demo_daten.py"
fi

echo
echo "Fertig. Öffentlich: https://$DOMAIN/   Verwaltung: https://$DOMAIN/verwaltung (Benutzer admin)"
echo "Firmendaten für Impressum und Datenschutz in $UMGEBUNG eintragen, dann: systemctl restart nachbarstrom"
