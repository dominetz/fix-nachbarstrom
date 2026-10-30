"""Prüfung österreichischer Zählpunktnummern.

Aufbau (33 Stellen): "AT" + 6 Ziffern Netzbetreiber + 5 Ziffern Postleitzahl
+ 20 alphanumerische Stellen für den Zählpunkt selbst.
"""
import re

_PATTERN = re.compile(r"^AT\d{6}\d{5}[0-9A-Z]{20}$")


def normalisieren(nummer: str) -> str:
    """Leerzeichen, Punkte und Bindestriche entfernen, Großbuchstaben."""
    return re.sub(r"[\s.\-]", "", nummer or "").upper()


def ist_gueltig(nummer: str) -> bool:
    return bool(_PATTERN.match(normalisieren(nummer)))


def netzbetreiber_code(nummer: str) -> str:
    """Die 6-stellige Netzbetreiber-Kennung (Stellen 3 bis 8)."""
    n = normalisieren(nummer)
    if not ist_gueltig(n):
        raise ValueError(f"Ungültige Zählpunktnummer: {nummer!r}")
    return n[2:8]


def maskiert(nummer: str) -> str:
    """Für Anzeigen gegenüber Dritten: nur die letzten 4 Stellen zeigen."""
    n = normalisieren(nummer)
    return "AT" + "…" + n[-4:] if len(n) > 6 else n
