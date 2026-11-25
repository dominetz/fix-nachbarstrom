"""Rechnungen von FIX an den Erzeuger bzw. GEA-Betreiber.

Zwei Arten:
- Servicegebühr je geteilter kWh (GEA und P2P). Grundlage ist die geteilte Menge im Zeitraum,
  also die Summe der „Eigendeckung“ aller Abnehmer bzw. Wohnungen aus den EDA-Daten.
- Einrichtungsgebühr, einmal je P2P-Vertrag: FIX vermittelt, setzt den Vertrag auf und meldet
  beim Netzbetreiber an.

Bei einer P2P-Schenkung (Preis 0) verrechnet FIX nur die Einrichtung, die Servicegebühr nur mit
config.SCHENKUNG_SERVICEGEBUEHR.
"""
from datetime import date

from sqlmodel import Session, select

from . import abrechnung, config
from .models import Art, Gebuehr, Gruppe, GruppenStatus, Modell

# Ab hier ist der Vertrag unterschrieben und die Einrichtung kann verrechnet werden
EINRICHTUNG_AB = (GruppenStatus.UNTERSCHRIEBEN, GruppenStatus.REGISTRIERT, GruppenStatus.EDA_ANGEMELDET,
                  GruppenStatus.AKTIV, GruppenStatus.BEENDET)


def einmalig(g: Gruppe | None) -> bool:
    """P2P, bei dem der Erzeuger seinen Abnehmern selbst die Rechnung stellt. FIX liefert ihm die Zahlen
    und verrechnet Einrichtung und Servicegebühr. (Name aus der ersten Fassung, als es dort nur die
    Einrichtung gab.)"""
    return g is not None and g.art == Art.P2P and g.modell == Modell.SELBST


def schenkung(g: Gruppe, tag: date | None = None) -> bool:
    """P2P ohne Preis: der Strom ist geschenkt, es gibt keine Rechnungen an die Abnehmer."""
    return g.art == Art.P2P and abrechnung.preis_fuer(g, tag or date.today()) <= 0


def nur_gebuehr(g: Gruppe, tag: date | None = None) -> bool:
    """FIX stellt hier keine Rechnungen an Abnehmer oder Wohnungen, nur die eigene Servicegebühr."""
    return g.modell == Modell.SELBST or schenkung(g, tag)


def nummer(g: Gruppe, bis: date) -> str:
    return f"FIX-{g.id:04d}-{bis:%Y%m}"


def einrichtung_nummer(g: Gruppe) -> str:
    return f"FIX-{g.id:04d}-E"


def anlegen(s: Session, g: Gruppe, von: date, bis: date, kwh: float, datum: date | None = None) -> Gebuehr | None:
    """Gebührenrechnung für den Zeitraum. None, wenn keine Gebühr vereinbart ist, der Strom geschenkt ist
    oder es sie schon gibt."""
    if g.gebuehr_ct_kwh <= 0 or kwh <= 0 or (schenkung(g, von) and not config.SCHENKUNG_SERVICEGEBUEHR):
        return None
    n = nummer(g, bis)
    if s.exec(select(Gebuehr).where(Gebuehr.nummer == n)).first():
        return None
    b = abrechnung.berechnen(kwh, g.gebuehr_ct_kwh, config.DIENSTLEISTER_UST)
    datum = datum or date.today()
    gebuehr = Gebuehr(nummer=n, gruppe_id=g.id, empfaenger_id=g.erzeuger_id, von=von, bis=bis, kwh=round(kwh, 3),
                      ct_kwh=g.gebuehr_ct_kwh, ust_satz=config.DIENSTLEISTER_UST, netto=b.netto, ust=b.ust,
                      brutto=b.brutto, datum=datum, faellig=abrechnung.faelligkeit(datum))
    s.add(gebuehr)
    s.flush()
    return gebuehr


def einrichtung(s: Session, g: Gruppe) -> Gebuehr | None:
    """Die Einrichtungsrechnung der Gruppe, falls es sie schon gibt."""
    return s.exec(select(Gebuehr).where(Gebuehr.gruppe_id == g.id, Gebuehr.art == "einrichtung")).first()


def einrichtung_anlegen(s: Session, g: Gruppe, datum: date | None = None) -> Gebuehr | None:
    """Einmalige Rechnung für das Aufsetzen des P2P-Vertrags. None, wenn kein Betrag festgelegt ist,
    der Vertrag noch nicht unterschrieben ist oder es die Rechnung schon gibt. Auch bei einer Schenkung."""
    if g.art != Art.P2P or g.einrichtung_eur <= 0 or g.status not in EINRICHTUNG_AB or einrichtung(s, g):
        return None
    datum = datum or date.today()
    netto = round(g.einrichtung_eur, 2)
    ust = round(netto * config.DIENSTLEISTER_UST, 2)
    gebuehr = Gebuehr(nummer=einrichtung_nummer(g), art="einrichtung", gruppe_id=g.id, empfaenger_id=g.erzeuger_id,
                      von=datum, bis=datum, kwh=0.0, ct_kwh=0.0, ust_satz=config.DIENSTLEISTER_UST, netto=netto,
                      ust=ust, brutto=round(netto + ust, 2), datum=datum, faellig=abrechnung.faelligkeit(datum))
    s.add(gebuehr)
    s.flush()
    return gebuehr
