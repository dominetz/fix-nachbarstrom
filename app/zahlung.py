"""Zahlung per Überweisung: IBAN prüfen und QR-Code für die Banking-App.

Die Abnehmer zahlen direkt auf das Konto des Erzeugers. Der QR-Code folgt dem
EPC-Standard (GiroCode), den österreichische Banking-Apps lesen können.
"""
import segno


def iban_normalisieren(text: str) -> str:
    return "".join(text.split()).upper()


def iban_gueltig(text: str) -> bool:
    iban = iban_normalisieren(text)
    if not (15 <= len(iban) <= 34 and iban[:2].isalpha() and iban[2:4].isdigit() and iban.isalnum()):
        return False
    ziffern = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(ziffern) % 97 == 1


def iban_lesbar(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def epc_daten(name: str, iban: str, betrag: float, verwendungszweck: str) -> str:
    return "\n".join(["BCD", "002", "1", "SCT", "", name[:70], iban_normalisieren(iban),
                      f"EUR{betrag:.2f}", "", "", verwendungszweck[:140]])


def qr_svg(name: str, iban: str, betrag: float, verwendungszweck: str) -> str:
    return segno.make(epc_daten(name, iban, betrag, verwendungszweck), error="m").svg_inline(scale=3)
