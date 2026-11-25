"""Einstellungen. Alles über Umgebungsvariablen änderbar, nichts Geheimes im Code."""
import os
import secrets
from pathlib import Path

from . import einstellungen

# Am PC (Dauerbetrieb) stehen die Einstellungen in einer Datei, gleich aufgebaut wie auf dem Server
if os.environ.get("NACHBARSTROM_EINSTELLUNGEN"):
    einstellungen.laden(os.environ["NACHBARSTROM_EINSTELLUNGEN"])

DATENBANK = os.environ.get("NACHBARSTROM_DB", "sqlite:///nachbarstrom.db")
ADMIN_BENUTZER = os.environ.get("NACHBARSTROM_ADMIN_USER", "admin")
ADMIN_PASSWORT = os.environ.get("NACHBARSTROM_ADMIN_PASSWORD", "")  # leer = Verwaltung gesperrt
# Auf dem Server: nur der Hash des Passworts (python -m app.sicherheit), kein Klartext
ADMIN_PASSWORT_HASH = os.environ.get("NACHBARSTROM_ADMIN_PASSWORD_HASH", "")
BASIS_URL = os.environ.get("NACHBARSTROM_BASE_URL", "http://localhost:8000")

MARKE = os.environ.get("NACHBARSTROM_MARKE", "Fix Nachbarstrom")
DIENSTLEISTER = {
    "firma": os.environ.get("NACHBARSTROM_FIRMA", "FIX PV GmbH"),
    "adresse": os.environ.get("NACHBARSTROM_FIRMA_ADRESSE", "[Adresse eintragen]"),
    "fn": os.environ.get("NACHBARSTROM_FIRMA_FN", "[Firmenbuchnummer eintragen]"),
    "email": os.environ.get("NACHBARSTROM_FIRMA_EMAIL", "office@fix-pv.com"),
    "uid": os.environ.get("NACHBARSTROM_FIRMA_UID", "[UID eintragen]"),
    "gericht": os.environ.get("NACHBARSTROM_FIRMA_GERICHT", "[Firmenbuchgericht eintragen]"),
    "geschaeftsfuehrung": os.environ.get("NACHBARSTROM_FIRMA_GF", "[Geschäftsführung eintragen]"),
    "telefon": os.environ.get("NACHBARSTROM_FIRMA_TELEFON", "[Telefon eintragen]"),
    "hoster": os.environ.get("NACHBARSTROM_HOSTER", "[Hosting-Anbieter eintragen]"),
    "iban": os.environ.get("NACHBARSTROM_FIRMA_IBAN", ""),  # für den QR-Code auf Gebührenrechnungen
}
# Impressum und Datenschutzerklärung sind Entwürfe, bis sie geprüft sind. Erst dann "ja" setzen,
# vorher steht auf beiden Seiten ein Hinweis.
RECHTSTEXTE_GEPRUEFT = os.environ.get("NACHBARSTROM_RECHTSTEXTE_GEPRUEFT", "") == "ja"
# Umsatzsteuer auf die Servicegebühr von FIX (GmbH, Regelbesteuerung angenommen, mit Steuerberater klären)
DIENSTLEISTER_UST = float(os.environ.get("NACHBARSTROM_FIRMA_UST", "0.20"))
# Servicegebühr je kWh, Vorschlag für neue GEAs, in jeder Gemeinschaft einzeln änderbar
GEBUEHR_STANDARD_CT_KWH = float(os.environ.get("NACHBARSTROM_GEBUEHR_CT_KWH", "1.0"))
# Servicegebühr je geteilter kWh, Vorschlag für neue P2P-Gruppen (Vorgabe vom 25.11.2026: 1,5 ct)
P2P_GEBUEHR_CT_KWH = float(os.environ.get("NACHBARSTROM_P2P_GEBUEHR_CT_KWH", "1.5"))
# P2P: FIX verrechnet dem Erzeuger einmal die Einrichtung und laufend die Servicegebühr je geteilter
# kWh (die Daten kommen über das EDA-Portal). Je Gruppe wählbar ist nur, wer die Rechnungen an die
# Abnehmer stellt, hier der Vorschlag für neue Gruppen:
# "verwaltet" = FIX im Namen des Erzeugers, "selbst" = der Erzeuger, FIX liefert ihm die Zahlen.
P2P_MODELL = "selbst" if os.environ.get("NACHBARSTROM_P2P_MODELL", "").strip().lower() == "selbst" else "verwaltet"
# Schenkung (Preis 0): keine Rechnungen an die Abnehmer, FIX verrechnet nur die Einrichtung.
# "ja" = auch die Servicegebühr je kWh.
SCHENKUNG_SERVICEGEBUEHR = os.environ.get("NACHBARSTROM_SCHENKUNG_GEBUEHR", "").strip().lower() == "ja"
# Einmalige Einrichtungsgebühr je P2P-Vertrag in Euro netto, Vorschlag für neue Gruppen
# (Vorgabe vom 25.11.2026: 150 €). Bei 0 bleibt der Knopf für die Rechnung gesperrt.
EINRICHTUNG_EUR = float(os.environ.get("NACHBARSTROM_EINRICHTUNG_EUR", "150"))

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
        try:
            GEHEIMNIS_DATEI.chmod(0o600)  # nur für den eigenen Benutzer lesbar
        except OSError:
            pass
    return GEHEIMNIS_DATEI.read_text(encoding="utf-8").strip()


GEHEIMNIS = _geheimnis()
