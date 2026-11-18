"""Servicegebühr von FIX je geteilter kWh, für P2P-Gruppen und GEAs.

Die Gebühr geht als eigene Rechnung von FIX an den Erzeuger bzw. GEA-Betreiber.
Grundlage ist die geteilte Menge im Zeitraum, also die Summe der „Eigendeckung“
aller Abnehmer bzw. Wohnungen.
"""
from datetime import date

from sqlmodel import Session, select

from . import abrechnung, config
from .models import Gebuehr, Gruppe


def nummer(g: Gruppe, bis: date) -> str:
    return f"FIX-{g.id:04d}-{bis:%Y%m}"


def anlegen(s: Session, g: Gruppe, von: date, bis: date, kwh: float, datum: date | None = None) -> Gebuehr | None:
    """Gebührenrechnung für den Zeitraum. None, wenn keine Gebühr vereinbart ist oder es sie schon gibt."""
    if g.gebuehr_ct_kwh <= 0 or kwh <= 0:
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
