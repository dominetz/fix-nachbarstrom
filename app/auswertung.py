"""Auswertungen über die gespeicherten Viertelstundenwerte.

Alle Abfragen lassen sich auf eine Gruppe oder GEA (gruppe_id) einschränken. Das
ist nötig, wenn ein Zählpunkt in mehreren Gemeinschaften ist: Verbrauch und
Eigendeckung stehen dann je Gemeinschaft in einem eigenen Export.

Summen aus dem EDA-Energiedatenreport (Messwert.ende gesetzt) zählen zum Monat
ihres Beginns, für die Vollständigkeit zählen alle Tage ihres Zeitraums. Im
Tagesverlauf kommen sie nicht vor.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from .eda_import import FELDER
from .models import Messwert


def _bereich(von: date, bis: date) -> tuple[str, str]:
    return f"{von:%Y-%m-%d} 00:00", f"{bis + timedelta(days=1):%Y-%m-%d} 00:00"


def _filter(abfrage, zaehlpunkte: list[str], gruppe_id: int | None):
    abfrage = abfrage.where(Messwert.zaehlpunkt.in_(zaehlpunkte))
    return abfrage.where(Messwert.gruppe_id == gruppe_id) if gruppe_id is not None else abfrage


def monatswerte(s: Session, zaehlpunkte: list[str], gruppe_id: int | None = None) -> dict[str, dict[str, dict[str, float]]]:
    """{zählpunkt: {"JJJJ-MM": {feld: kWh}}}"""
    if not zaehlpunkte:
        return {}
    monat = func.substr(Messwert.beginn, 1, 7)
    abfrage = _filter(select(Messwert.zaehlpunkt, monat, *(func.sum(getattr(Messwert, f)) for f in FELDER)),
                      zaehlpunkte, gruppe_id).group_by(Messwert.zaehlpunkt, monat)
    ergebnis: dict = defaultdict(dict)
    for zp, m, *summen in s.exec(abfrage).all():
        ergebnis[zp][m] = dict(zip(FELDER, (x or 0.0 for x in summen)))
    return ergebnis


def _tage(beginn: str, ende: str, bis: str | None = None) -> list[date]:
    """Tage, die eine Summe abdeckt (ende ausschließlich), höchstens bis zur Grenze bis."""
    a = datetime.strptime(beginn, "%Y-%m-%d %H:%M")
    e = datetime.strptime(min(ende, bis) if bis else ende, "%Y-%m-%d %H:%M")
    tage, tag = [], a.date()
    while datetime.combine(tag, datetime.min.time()) < e and len(tage) < 400:
        tage.append(tag)
        tag += timedelta(days=1)
    return tage


def summen(s: Session, zaehlpunkte: list[str], von: date, bis: date,
           gruppe_id: int | None = None) -> dict[str, dict[str, float]]:
    """{zählpunkt: {feld: kWh, "viertelstunden": Anzahl Werte, "tage": Tage mit Werten}} im Zeitraum
    (beide Tage eingeschlossen). Summen zählen, wenn ihr Beginn im Zeitraum liegt."""
    if not zaehlpunkte:
        return {}
    a, b = _bereich(von, bis)
    tag = func.substr(Messwert.beginn, 1, 10)
    abfrage = _filter(select(Messwert.zaehlpunkt, func.count(), func.count(func.distinct(tag)),
                             *(func.sum(getattr(Messwert, f)) for f in FELDER)), zaehlpunkte, gruppe_id)
    abfrage = abfrage.where(Messwert.beginn >= a, Messwert.beginn < b, Messwert.ende == None)  # noqa: E711
    ergebnis = {zp: {"viertelstunden": 0, "tage": 0, **dict.fromkeys(FELDER, 0.0)} for zp in zaehlpunkte}
    for zp, anzahl, tage, *werte in s.exec(abfrage.group_by(Messwert.zaehlpunkt)).all():
        ergebnis[zp] = {"viertelstunden": anzahl, "tage": tage, **dict(zip(FELDER, (x or 0.0 for x in werte)))}
    perioden = _filter(select(Messwert), zaehlpunkte, gruppe_id).where(
        Messwert.beginn >= a, Messwert.beginn < b, Messwert.ende != None)  # noqa: E711
    for m in s.exec(perioden).all():
        e = ergebnis[m.zaehlpunkt]
        e["tage"] += len(_tage(m.beginn, m.ende, b))
        for f in FELDER:
            e[f] += getattr(m, f)
    return ergebnis


def tageswerte(s: Session, zaehlpunkte: list[str], tag: date,
               gruppe_id: int | None = None) -> dict[str, dict[str, list[float]]]:
    """{zählpunkt: {feld: 96 Werte}} für einen Tag. Gröbere Werte (z. B. Stunden) landen in
    der ersten Viertelstunde ihres Zeitraums."""
    a, b = _bereich(tag, tag)
    werte = {zp: {f: [0.0] * 96 for f in FELDER} for zp in zaehlpunkte}
    abfrage = _filter(select(Messwert), zaehlpunkte, gruppe_id).where(Messwert.beginn >= a, Messwert.beginn < b,
                                                                       Messwert.ende == None)  # noqa: E711
    for m in s.exec(abfrage).all():
        q = int(m.beginn[11:13]) * 4 + int(m.beginn[14:16]) // 15
        for f in FELDER:
            werte[m.zaehlpunkt][f][q] += getattr(m, f)
    return werte


def abdeckung(s: Session) -> dict[tuple[int | None, str], dict[str, int]]:
    """{(gruppe_id, zählpunkt): {"JJJJ-MM": Anzahl Tage mit Werten}}"""
    monat = func.substr(Messwert.beginn, 1, 7)
    tag = func.substr(Messwert.beginn, 1, 10)
    ergebnis: dict = defaultdict(dict)
    for gid, zp, m, anzahl in s.exec(select(Messwert.gruppe_id, Messwert.zaehlpunkt, monat,
                                            func.count(func.distinct(tag)))
                                     .where(Messwert.ende == None)  # noqa: E711
                                     .group_by(Messwert.gruppe_id, Messwert.zaehlpunkt, monat)).all():
        ergebnis[(gid, zp)][m] = anzahl
    perioden: dict = defaultdict(set)
    for gid, zp, beginn, ende in s.exec(select(Messwert.gruppe_id, Messwert.zaehlpunkt, Messwert.beginn,
                                               Messwert.ende).where(Messwert.ende != None)).all():  # noqa: E711
        perioden[(gid, zp)].update(_tage(beginn, ende))
    for schluessel, tage in perioden.items():
        je_monat: dict = defaultdict(int)
        for t in tage:
            je_monat[f"{t:%Y-%m}"] += 1
        for m, anzahl in je_monat.items():
            ergebnis[schluessel][m] = min(tage_im_monat(m), ergebnis[schluessel].get(m, 0) + anzahl)
    return ergebnis


def letzter_tag(s: Session, zaehlpunkte: list[str], gruppe_id: int | None = None) -> date | None:
    """Letzter Tag mit Werten. Bei Summen der letzte Tag ihres Zeitraums."""
    if not zaehlpunkte:
        return None
    letzter = s.exec(_filter(select(func.max(Messwert.beginn)), zaehlpunkte, gruppe_id)
                     .where(Messwert.ende == None)).one()  # noqa: E711
    ende = s.exec(_filter(select(func.max(Messwert.ende)), zaehlpunkte, gruppe_id)).one()
    tage = [date.fromisoformat(letzter[:10])] if letzter else []
    if ende:
        tage.append((datetime.strptime(ende, "%Y-%m-%d %H:%M") - timedelta(minutes=1)).date())
    return max(tage) if tage else None


def hat_viertelstunden(s: Session, zaehlpunkte: list[str], gruppe_id: int | None = None) -> bool:
    """Gibt es Werte für einen Tagesverlauf (nicht nur Summen aus dem Energiedatenreport)?"""
    return bool(zaehlpunkte) and s.exec(_filter(select(Messwert.id), zaehlpunkte, gruppe_id)
                                        .where(Messwert.ende == None).limit(1)).first() is not None  # noqa: E711


def erster_tag(s: Session, zaehlpunkte: list[str], gruppe_id: int | None = None) -> date | None:
    if not zaehlpunkte:
        return None
    erster = s.exec(_filter(select(func.min(Messwert.beginn)), zaehlpunkte, gruppe_id)).one()
    return date.fromisoformat(erster[:10]) if erster else None


def bester_tag(s: Session, zaehlpunkt: str, monat: str | None = None, gruppe_id: int | None = None) -> date | None:
    """Tag mit der höchsten Erzeugung, zum Zeigen des Tagesverlaufs."""
    tag = func.substr(Messwert.beginn, 1, 10)
    abfrage = _filter(select(tag, func.sum(Messwert.erzeugung)), [zaehlpunkt], gruppe_id).where(
        Messwert.ende == None)  # noqa: E711
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


def tage_im_monat(schluessel: str) -> int:
    j, m = int(schluessel[:4]), int(schluessel[5:7])
    naechster = date(j + m // 12, m % 12 + 1, 1)
    return (naechster - date(j, m, 1)).days


def mit_daten(schluessel: list[str], monatswerte: dict) -> list[str]:
    """Monate vor dem ersten Monat mit Daten weglassen (junge Gruppen)."""
    erster = min((k for werte in monatswerte.values() for k in werte), default=schluessel[0])
    return [k for k in schluessel if k >= erster]
