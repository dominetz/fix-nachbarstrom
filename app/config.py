"""Einstellungen. Alles über Umgebungsvariablen änderbar, nichts Geheimes im Code."""
import os
import secrets
from pathlib import Path

DATENBANK = os.environ.get("NACHBARSTROM_DB", "sqlite:///nachbarstrom.db")
ADMIN_BENUTZER = os.environ.get("NACHBARSTROM_ADMIN_USER", "admin")
ADMIN_PASSWORT = os.environ.get("NACHBARSTROM_ADMIN_PASSWORD", "")  # leer = Verwaltung gesperrt
BASIS_URL = os.environ.get("NACHBARSTROM_BASE_URL", "http://localhost:8000")

MARKE = os.environ.get("NACHBARSTROM_MARKE", "Fix Nachbarstrom")
DIENSTLEISTER = {
    "firma": os.environ.get("NACHBARSTROM_FIRMA", "FIX PV GmbH"),
    "adresse": os.environ.get("NACHBARSTROM_FIRMA_ADRESSE", "[Adresse eintragen]"),
    "fn": os.environ.get("NACHBARSTROM_FIRMA_FN", "[Firmenbuchnummer eintragen]"),
    "email": os.environ.get("NACHBARSTROM_FIRMA_EMAIL", "office@fix-pv.com"),
}

# Frist für die Zustimmung im Netzportal. Koordinationsstelle: 30 Tage.
# Laut Handbuch von Vorarlberg Netz 7 Tage, dort noch bestätigen lassen.
ZUSTIMMUNG_FRIST_TAGE = int(os.environ.get("NACHBARSTROM_FRIST_TAGE", "7"))
ERINNERUNG_NACH_TAGEN = (2, 5)  # nach Freigabe, danach kurz vor Fristende

ZAHLUNGSZIEL_TAGE = 14

# Ordner mit den fiktiven EDA-Exporten zum Testen (legt demo_daten.py an)
TESTDATEN = Path(os.environ.get("NACHBARSTROM_TESTDATEN", "testdaten"))

# Schlüssel für die persönlichen Links (Unterschrift, Kundenbereich). Wird beim ersten
# Start zufällig erzeugt und in einer Datei aufgehoben, damit verschickte Links
# auch nach einem Neustart gültig bleiben.
GEHEIMNIS_DATEI = Path(os.environ.get("NACHBARSTROM_SECRET_FILE", "nachbarstrom.geheimnis"))


def _geheimnis() -> str:
    if os.environ.get("NACHBARSTROM_SECRET"):
        return os.environ["NACHBARSTROM_SECRET"]
    if not GEHEIMNIS_DATEI.exists():
        GEHEIMNIS_DATEI.write_text(secrets.token_hex(32), encoding="utf-8")
    return GEHEIMNIS_DATEI.read_text(encoding="utf-8").strip()


GEHEIMNIS = _geheimnis()
