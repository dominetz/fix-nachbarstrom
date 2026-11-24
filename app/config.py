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
# Vorschlag für neue Gruppen und GEAs, in jeder Gruppe einzeln änderbar
GEBUEHR_STANDARD_CT_KWH = float(os.environ.get("NACHBARSTROM_GEBUEHR_CT_KWH", "1.0"))
# P2P: "einmalig" = FIX vermittelt, setzt den Vertrag auf, meldet an und verrechnet das einmal an den
# Erzeuger. Danach rechnen Erzeuger und Abnehmer selbst miteinander ab, eine Schenkung gar nicht.
# "laufend" = FIX rechnet jede Periode im Namen des Erzeugers ab und verrechnet die Gebühr je kWh.
P2P_ABRECHNUNG = ("laufend" if os.environ.get("NACHBARSTROM_P2P_ABRECHNUNG", "").strip().lower() == "laufend"
                  else "einmalig")
# Einmalige Einrichtungsgebühr je P2P-Vertrag in Euro netto, Vorschlag für neue Gruppen.
# 0 = noch nicht festgelegt, dann bleibt der Knopf für die Rechnung gesperrt.
EINRICHTUNG_EUR = float(os.environ.get("NACHBARSTROM_EINRICHTUNG_EUR", "0"))

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
