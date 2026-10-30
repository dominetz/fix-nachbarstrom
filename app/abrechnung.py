"""Abrechnung aus dem EDA-Energiedatenreport.

Der Report wird als CSV aus dem EDA-Portal exportiert. Weil die Spalten je
nach Export unterschiedlich heißen können, wird hier ein einfaches Format
erwartet (Trennzeichen ; oder ,):

    zaehlpunkt;von;bis;kwh
    AT00…;2026-10-01;2026-10-31;123,4

"kwh" ist die dem Abnehmer zugeteilte, geteilte Menge im Zeitraum.
Rechnungen werden im Namen des Erzeugers erstellt (Vollmacht).
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta

from .config import ZAHLUNGSZIEL_TAGE
from .zaehlpunkt import normalisieren


@dataclass
class Zeile:
    zaehlpunkt: str
    von: date
    bis: date
    kwh: float


def _zahl(text: str) -> float:
    text = text.strip().replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "")
    return float(text.replace(",", "."))


def _datum(text: str) -> date:
    text = text.strip()
    if "." in text:
        t, m, j = text.split(".")
        return date(int(j), int(m), int(t))
    return date.fromisoformat(text)


def report_einlesen(inhalt: str) -> list[Zeile]:
    dialekt = ";" if inhalt.count(";") >= inhalt.count(",") / 2 else ","
    leser = csv.DictReader(io.StringIO(inhalt.lstrip("﻿")), delimiter=dialekt)
    spalten = {(s or "").strip().lower(): s for s in (leser.fieldnames or [])}
    fehlend = {"zaehlpunkt", "von", "bis", "kwh"} - spalten.keys()
    if fehlend:
        raise ValueError(f"Spalten fehlen: {', '.join(sorted(fehlend))}")
    zeilen = []
    for nr, z in enumerate(leser, start=2):
        try:
            zeilen.append(Zeile(
                zaehlpunkt=normalisieren(z[spalten["zaehlpunkt"]]),
                von=_datum(z[spalten["von"]]),
                bis=_datum(z[spalten["bis"]]),
                kwh=_zahl(z[spalten["kwh"]]),
            ))
        except (ValueError, KeyError) as e:
            raise ValueError(f"Zeile {nr}: {e}") from e
    return zeilen


@dataclass
class Betrag:
    netto: float
    ust: float
    brutto: float


def berechnen(kwh: float, preis_ct_kwh: float, ust_satz: float) -> Betrag:
    netto = round(kwh * preis_ct_kwh / 100, 2)
    ust = round(netto * ust_satz, 2)
    return Betrag(netto, ust, round(netto + ust, 2))


def faelligkeit(rechnungsdatum: date) -> date:
    return rechnungsdatum + timedelta(days=ZAHLUNGSZIEL_TAGE)


def rechnungsnummer(gruppe_id: int, abnehmer_id: int, bis: date) -> str:
    return f"NS-{gruppe_id:04d}-{abnehmer_id:05d}-{bis:%Y%m}"
