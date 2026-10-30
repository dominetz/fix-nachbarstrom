"""Auswertungen über die gespeicherten Viertelstundenwerte."""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from .energiedaten import FELDER
from .models import Messwert


def _bereich(von: date, bis: date) -> tuple[str, str]:
    return f"{von:%Y-%m-%d} 00:00", f"{bis + timedelta(days=1):%Y-%m-%d} 00:00"


def monatswerte(s: Session, zaehlpunkte: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """{zählpunkt: {"JJJJ-MM": {feld: kWh}}}"""
    if not zaehlpunkte:
        return {}
    monat = func.substr(Messwert.beginn, 1, 7)
    abfrage = (select(Messwert.zaehlpunkt, monat, *(func.sum(getattr(Messwert, f)) for f in FELDER))
               .where(Messwert.zaehlpunkt.in_(zaehlpunkte)).group_by(Messwert.zaehlpunkt, monat))
    ergebnis: dict = defaultdict(dict)
    for zp, m, *summen in s.exec(abfrage).all():
        ergebnis[zp][m] = dict(zip(FELDER, (x or 0.0 for x in summen)))
    return ergebnis


def summen(s: Session, zaehlpunkte: list[str], von: date, bis: date) -> dict[str, dict[str, float]]:
    """{zählpunkt: {feld: kWh, "viertelstunden": Anzahl}} im Zeitraum (beide Tage eingeschlossen)."""
    if not zaehlpunkte:
        return {}
    a, b = _bereich(von, bis)
    abfrage = (select(Messwert.zaehlpunkt, func.count(), *(func.sum(getattr(Messwert, f)) for f in FELDER))
               .where(Messwert.zaehlpunkt.in_(zaehlpunkte), Messwert.beginn >= a, Messwert.beginn < b)
               .group_by(Messwert.zaehlpunkt))
    ergebnis = {zp: {"viertelstunden": 0, **dict.fromkeys(FELDER, 0.0)} for zp in zaehlpunkte}
    for zp, anzahl, *werte in s.exec(abfrage).all():
        ergebnis[zp] = {"viertelstunden": anzahl, **dict(zip(FELDER, (x or 0.0 for x in werte)))}
    return ergebnis


def tageswerte(s: Session, zaehlpunkte: list[str], tag: date) -> dict[str, dict[str, list[float]]]:
    """{zählpunkt: {feld: 96 Werte}} für einen Tag."""
    a, b = _bereich(tag, tag)
    werte = {zp: {f: [0.0] * 96 for f in FELDER} for zp in zaehlpunkte}
    for m in s.exec(select(Messwert).where(Messwert.zaehlpunkt.in_(zaehlpunkte),
                                           Messwert.beginn >= a, Messwert.beginn < b)).all():
        q = int(m.beginn[11:13]) * 4 + int(m.beginn[14:16]) // 15
        for f in FELDER:
            werte[m.zaehlpunkt][f][q] += getattr(m, f)
    return werte


def abdeckung(s: Session) -> dict[str, dict[str, int]]:
    """{zählpunkt: {"JJJJ-MM": Anzahl Viertelstunden}}"""
    monat = func.substr(Messwert.beginn, 1, 7)
    ergebnis: dict = defaultdict(dict)
    for zp, m, anzahl in s.exec(select(Messwert.zaehlpunkt, monat, func.count())
                                .group_by(Messwert.zaehlpunkt, monat)).all():
        ergebnis[zp][m] = anzahl
    return ergebnis


def letzter_tag(s: Session, zaehlpunkte: list[str]) -> date | None:
    if not zaehlpunkte:
        return None
    letzter = s.exec(select(func.max(Messwert.beginn)).where(Messwert.zaehlpunkt.in_(zaehlpunkte))).one()
    return date.fromisoformat(letzter[:10]) if letzter else None


def bester_tag(s: Session, zaehlpunkt: str, monat: str | None = None) -> date | None:
    """Tag mit der höchsten Erzeugung, zum Zeigen des Tagesverlaufs."""
    tag = func.substr(Messwert.beginn, 1, 10)
    abfrage = select(tag, func.sum(Messwert.erzeugung)).where(Messwert.zaehlpunkt == zaehlpunkt)
    if monat:
        abfrage = abfrage.where(Messwert.beginn.like(f"{monat}%"))
    zeile = s.exec(abfrage.group_by(tag).order_by(func.sum(Messwert.erzeugung).desc()).limit(1)).first()
    return date.fromisoformat(zeile[0]) if zeile else None


def monatsschluessel(bis: date, anzahl: int = 12) -> list[str]:
    """Die letzten Monate bis einschließlich dem Monat von bis, älteste zuerst."""
    j, m = bis.year, bis.month
    liste = []
    for _ in range(anzahl):
        liste.append(f"{j:04d}-{m:02d}")
        j, m = (j - 1, 12) if m == 1 else (j, m - 1)
    return liste[::-1]


def erster_tag(s: Session, zaehlpunkte: list[str]) -> date | None:
    if not zaehlpunkte:
        return None
    erster = s.exec(select(func.min(Messwert.beginn)).where(Messwert.zaehlpunkt.in_(zaehlpunkte))).one()
    return date.fromisoformat(erster[:10]) if erster else None
