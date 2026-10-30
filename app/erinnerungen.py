"""Wer muss heute an die Zustimmung im Netzportal erinnert werden?

Vorarlberg Netz und enixi erinnern die Kunden nicht. Die Plattform erstellt
deshalb eine tägliche Liste mit fertigem Text. Versendet wird erst, wenn ein
Mailversand eingerichtet und freigegeben ist.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from .config import ERINNERUNG_NACH_TAGEN, MARKE, ZUSTIMMUNG_FRIST_TAGE
from .models import Teilnehmer, Zustimmung


@dataclass
class Erinnerung:
    zustimmung: Zustimmung
    teilnehmer: Teilnehmer
    frist: date
    grund: str  # "erinnerung", "letzte_chance", "abgelaufen"

    @property
    def betreff(self) -> str:
        if self.grund == "abgelaufen":
            return f"{MARKE}: Ihre Freischaltung ist abgelaufen"
        return f"{MARKE}: Bitte noch kurz im Netzportal bestätigen"

    @property
    def text(self) -> str:
        anrede = f"Hallo {self.teilnehmer.vorname},"
        if self.grund == "abgelaufen":
            return (f"{anrede}\n\ndie Frist für Ihre Bestätigung im Kundenportal von Vorarlberg Netz ist leider "
                    f"abgelaufen. Kein Problem: Wir stoßen die Anmeldung neu an und melden uns, sobald Sie "
                    f"wieder bestätigen können.\n\nIhr {MARKE}-Team")
        return (f"{anrede}\n\nfast geschafft! Damit Sie Strom von Ihrem Nachbarn bekommen, fehlt nur noch Ihre "
                f"Bestätigung im Kundenportal von Vorarlberg Netz. Das dauert zwei Minuten:\n\n"
                f"1. Im Kundenportal von Vorarlberg Netz anmelden.\n"
                f"2. Die offene Anfrage zur gemeinsamen Energienutzung öffnen.\n"
                f"3. Auf „Zustimmen“ klicken.\n\n"
                f"Bitte bis spätestens {self.frist:%d.%m.%Y} erledigen.\n\nIhr {MARKE}-Team")


def faellige_erinnerungen(zustimmungen: list[tuple[Zustimmung, Teilnehmer]],
                          heute: date | None = None) -> list[Erinnerung]:
    heute = heute or date.today()
    liste = []
    for z, t in zustimmungen:
        if z.erteilt_am or not z.freigegeben_am:
            continue
        frist = z.freigegeben_am + timedelta(days=ZUSTIMMUNG_FRIST_TAGE)
        seit = (heute - z.freigegeben_am).days
        if heute > frist:
            if z.letzte_erinnerung is None or z.letzte_erinnerung <= frist:
                liste.append(Erinnerung(z, t, frist, "abgelaufen"))
            continue
        stufen = list(ERINNERUNG_NACH_TAGEN) + [ZUSTIMMUNG_FRIST_TAGE - 1]
        faellig = [s for s in stufen if seit >= s]
        if len(faellig) > z.erinnerungen:
            grund = "letzte_chance" if seit >= ZUSTIMMUNG_FRIST_TAGE - 1 else "erinnerung"
            liste.append(Erinnerung(z, t, frist, grund))
    return liste
