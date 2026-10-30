"""Texte für den Postausgang. Jede Mail entsteht als Entwurf und wird erst
von Hand verschickt (Mailprogramm öffnet sich mit fertigem Text)."""
from datetime import date
from urllib.parse import quote

from .config import DIENSTLEISTER, MARKE
from .models import Nachricht, Rolle, Teilnehmer

GRUSS = f"\n\nIhr {MARKE}-Team\n{DIENSTLEISTER['firma']} · {DIENSTLEISTER['email']}"


def mailto(email: str, betreff: str, text: str) -> str:
    return f"mailto:{email}?subject={quote(betreff)}&body={quote(text)}"


def willkommen(t: Teilnehmer, kundenlink: str) -> Nachricht:
    if t.rolle == Rolle.ERZEUGER:
        mitte = ("wir suchen jetzt passende Abnehmer in Ihrer Nähe. Kennen Sie schon jemanden? "
                 "In Ihrem Bereich finden Sie einen Einladungslink zum Weitergeben.")
    else:
        mitte = "wir suchen jetzt eine passende PV-Anlage in Ihrer Nähe und melden uns mit einem Vorschlag."
    return Nachricht(teilnehmer_id=t.id, art="willkommen", betreff=f"{MARKE}: Ihre Anmeldung ist da",
                     text=f"Hallo {t.vorname},\n\ndanke für Ihre Anmeldung! {mitte}\n\n"
                          f"Ihr persönlicher Bereich, dort sehen Sie jederzeit den Stand:\n{kundenlink}{GRUSS}")


def unterschrift(t: Teilnehmer, gruppe_id: int, link: str, kundenlink: str) -> Nachricht:
    return Nachricht(teilnehmer_id=t.id, gruppe_id=gruppe_id, art="unterschrift",
                     betreff=f"{MARKE}: Ihr Partner ist gefunden, bitte unterschreiben",
                     text=f"Hallo {t.vorname},\n\nwir haben eine passende Gruppe für Sie gefunden. "
                          f"Bitte lesen und unterschreiben Sie Vertrag und Vollmacht online, das dauert "
                          f"etwa fünf Minuten:\n{link}\n\nDanach melden wir alles beim Netzbetreiber an. "
                          f"Sie bekommen dann eine Mail von Vorarlberg Netz und bestätigen einmal im "
                          f"Kundenportal.\n\nDen Stand sehen Sie jederzeit in Ihrem Bereich:\n{kundenlink}{GRUSS}")


def rechnung(t: Teilnehmer, gruppe_id: int, nummer: str, brutto: str, faellig: date, link: str) -> Nachricht:
    return Nachricht(teilnehmer_id=t.id, gruppe_id=gruppe_id, art="rechnung",
                     betreff=f"{MARKE}: Rechnung {nummer}",
                     text=f"Hallo {t.vorname},\n\nIhre Rechnung {nummer} über {brutto} € ist da, "
                          f"fällig bis {faellig:%d.%m.%Y}. Sie können sie hier ansehen, als PDF speichern "
                          f"und mit dem QR-Code in Ihrer Banking-App bezahlen:\n{link}{GRUSS}")


def preisaenderung(t: Teilnehmer, gruppe_id: int, alt: str, neu: str, ab: date, grund: str,
                   link: str) -> Nachricht:
    return Nachricht(teilnehmer_id=t.id, gruppe_id=gruppe_id, art="preisaenderung",
                     betreff=f"{MARKE}: Neuer Preis ab {ab:%d.%m.%Y}",
                     text=f"Hallo {t.vorname},\n\nder Preis für Ihren Nachbarstrom ändert sich ab "
                          f"{ab:%d.%m.%Y} von {alt} auf {neu} ct/kWh netto.\n\nGrund: {grund}\n\n"
                          f"Sind Sie nicht einverstanden, können Sie bis zu diesem Tag widersprechen "
                          f"oder den Vertrag kostenlos kündigen. Eine Antwort auf diese Mail genügt.\n\n"
                          f"Ihr Bereich: {link}{GRUSS}")


def zahlungserinnerung(t: Teilnehmer, gruppe_id: int, nummer: str, brutto: str, faellig: date,
                       link: str) -> Nachricht:
    return Nachricht(teilnehmer_id=t.id, gruppe_id=gruppe_id, art="zahlungserinnerung",
                     betreff=f"{MARKE}: Zahlungserinnerung zu Rechnung {nummer}",
                     text=f"Hallo {t.vorname},\n\nfür Ihre Rechnung {nummer} über {brutto} € (fällig am "
                          f"{faellig:%d.%m.%Y}) haben wir noch keinen Zahlungseingang gesehen. Vielleicht ist sie "
                          f"untergegangen. Hier können Sie sie ansehen und mit dem QR-Code bezahlen:\n{link}\n\n"
                          f"Haben Sie schon überwiesen? Dann ist diese Mail hinfällig.{GRUSS}")
