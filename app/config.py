"""Einstellungen. Alles über Umgebungsvariablen änderbar, nichts Geheimes im Code."""
import os

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

# Schlüssel für Unterschrifts-Links. In Betrieb unbedingt setzen.
GEHEIMNIS = os.environ.get("NACHBARSTROM_SECRET", "nur-fuer-entwicklung")
