"""GEA (gemeinschaftliche Erzeugungsanlage): Auswertung je Wohnung und Abrechnungsgrundlage.

Die Erzeugung kommt vom Zählpunkt der Anlage, wenn er im Export steht. Fehlt er,
wird sie aus den zugeteilten Anteilen der Wohnungen berechnet und so beschriftet.
"""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlmodel import Session

from . import abrechnung, auswertung, diagramme
from .eda_import import FELDER
from .importe import OHNE_ZP, mitglieder
from .models import Gruppe, Teilnehmer


def _wert(m: dict, zp: str, k: str, f: str) -> float:
    return m.get(zp, {}).get(k, {}).get(f, 0.0)


def auswerten(s: Session, g: Gruppe, bis: date | None = None) -> dict | None:
    betreiber = s.get(Teilnehmer, g.erzeuger_id)
    wohnungen = mitglieder(s, g)
    zps = [t.zaehlpunkt for _, t in wohnungen]
    alle = zps + [betreiber.zaehlpunkt]
    bis = bis or auswertung.letzter_tag(s, alle, g.id)
    if not bis:
        return None
    m = auswertung.monatswerte(s, alle, g.id)
    monate = auswertung.mit_daten(auswertung.monatsschluessel(bis), m)
    anlage = [_wert(m, betreiber.zaehlpunkt, k, "erzeugung") for k in monate]
    anlage_rest = [_wert(m, betreiber.zaehlpunkt, k, "restueberschuss") for k in monate]
    zugeteilt = [sum(_wert(m, zp, k, "anteil") for zp in zps) for k in monate]
    genutzt = [sum(_wert(m, zp, k, "eigendeckung") for zp in zps) for k in monate]
    verbrauch = [sum(_wert(m, zp, k, "verbrauch") for zp in zps) for k in monate]
    aus_anlage = sum(anlage) > 0
    erzeugung = anlage if aus_anlage else [max(z, n) for z, n in zip(zugeteilt, genutzt)]
    if aus_anlage and sum(anlage_rest) > 0:
        rest = anlage_rest
    else:
        rest = [max(0.0, e - n) for e, n in zip(erzeugung, genutzt)]
    preis = abrechnung.preis_fuer(g, bis)
    zeilen = []
    for mg, t in wohnungen:
        summe = {f: sum(_wert(m, t.zaehlpunkt, k, f) for k in monate) for f in FELDER}
        zeilen.append({"t": t, "mitglied": mg, "monate": [_wert(m, t.zaehlpunkt, k, "eigendeckung") for k in monate],
                       "summe": summe, "betrag": summe["eigendeckung"] * preis / 100,
                       "quote": summe["eigendeckung"] / summe["verbrauch"] * 100 if summe["verbrauch"] else 0.0})
    beschriftung = [diagramme.monatsname(k) for k in monate]
    return {
        "bis": bis, "monate": monate, "beschriftung": beschriftung, "aus_anlage": aus_anlage,
        "erzeugung": sum(erzeugung), "genutzt": sum(genutzt), "rest": sum(rest), "verbrauch": sum(verbrauch),
        "genutzt_je_monat": genutzt, "zeilen": zeilen, "preis": preis,
        "gebuehr": sum(genutzt) * g.gebuehr_ct_kwh / 100,
        "diagramm": diagramme.balken(beschriftung, [("Im Haus genutzt", "--reihe-1", genutzt),
                                                    ("Ins Netz eingespeist", "--reihe-grau", rest)]),
    }


def tagesdiagramm(s: Session, g: Gruppe, tag: date) -> str:
    betreiber = s.get(Teilnehmer, g.erzeuger_id)
    zps = [t.zaehlpunkt for _, t in mitglieder(s, g)]
    w = auswertung.tageswerte(s, zps + [betreiber.zaehlpunkt], tag, g.id)

    def summe(feld):
        return [sum(w[zp][feld][q] for zp in zps) for q in range(96)]

    erzeugung = w[betreiber.zaehlpunkt]["erzeugung"]
    if not any(erzeugung):
        erzeugung = [max(a, e) for a, e in zip(summe("anteil"), summe("eigendeckung"))]
    return diagramme.tageskurve([("Erzeugung der Anlage", "--reihe-3", erzeugung, False),
                                 ("Verbrauch der Wohnungen", "--reihe-2", summe("verbrauch"), False),
                                 ("Im Haus genutzt", "--reihe-1", summe("eigendeckung"), True)])


def zeitraeume(s: Session, g: Gruppe) -> list[tuple[date, date]]:
    """Kalendermonate mit Daten, neueste zuerst, für die Auswahl der Abrechnungsgrundlage."""
    from .energiedaten import monate
    betreiber = s.get(Teilnehmer, g.erzeuger_id)
    zps = [t.zaehlpunkt for _, t in mitglieder(s, g)] + [betreiber.zaehlpunkt]
    erster, letzter = auswertung.erster_tag(s, zps, g.id), auswertung.letzter_tag(s, zps, g.id)
    return list(reversed(monate(erster, letzter))) if erster and letzter else []


def grundlage_xlsx(s: Session, g: Gruppe, von: date, bis: date) -> bytes:
    """Excel mit allem, was der Betreiber für seine Abrechnung an die Wohnungen braucht."""
    from .energiedaten import monate
    betreiber = s.get(Teilnehmer, g.erzeuger_id)
    wohnungen = mitglieder(s, g)
    zps = [t.zaehlpunkt for _, t in wohnungen]
    werte = auswertung.summen(s, zps, von, bis, g.id)
    preis = abrechnung.preis_fuer(g, von)
    tage = (bis - von).days + 1
    fett = Font(bold=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Abrechnung"
    ws.append([f"Abrechnungsgrundlage {g.titel}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Zeitraum {von:%d.%m.%Y} bis {bis:%d.%m.%Y}", None, f"Preis {preis:.2f} ct/kWh netto".replace(".", ",")])
    if g.gemeinschafts_id:
        ws.append([f"Gemeinschafts-ID {g.gemeinschafts_id}"])
    ws.append(["Mengen aus den EDA-Energiedaten, Beträge berechnet (Menge × Preis), USt laut Einstellung der GEA."])
    ws.append([])
    kopf = ["Wohnung", "Name", "Zählpunkt", "Verbrauch kWh", "Zugeteilt kWh", "Eigendeckung kWh",
            "Anteil am Verbrauch %", "Preis ct/kWh", "Netto €", "USt €", "Brutto €", "Tage mit Daten"]
    ws.append(kopf)
    for zelle in ws[ws.max_row]:
        zelle.font = fett
    summe = dict.fromkeys(("verbrauch", "anteil", "eigendeckung", "netto", "ust", "brutto"), 0.0)
    for _, t in wohnungen:
        w = werte[t.zaehlpunkt]
        b = abrechnung.berechnen(w["eigendeckung"], preis, g.ust_satz)
        name = f"{t.vorname} {t.nachname}".strip() or (t.firma or "")
        ws.append([t.wohnung or "", name, t.zaehlpunkt, round(w["verbrauch"], 3), round(w["anteil"], 3),
                   round(w["eigendeckung"], 3),
                   round(w["eigendeckung"] / w["verbrauch"] * 100, 1) if w["verbrauch"] else 0,
                   preis, b.netto, b.ust, b.brutto, f"{w['tage']} von {tage}"])
        for k, v in (("verbrauch", w["verbrauch"]), ("anteil", w["anteil"]), ("eigendeckung", w["eigendeckung"]),
                     ("netto", b.netto), ("ust", b.ust), ("brutto", b.brutto)):
            summe[k] += v
    ws.append(["Summe", None, None, round(summe["verbrauch"], 3), round(summe["anteil"], 3),
               round(summe["eigendeckung"], 3), None, None, round(summe["netto"], 2), round(summe["ust"], 2),
               round(summe["brutto"], 2)])
    for zelle in ws[ws.max_row]:
        zelle.font = fett
    if g.gebuehr_ct_kwh > 0:
        ws.append([])
        ws.append([f"Servicegebühr {g.gebuehr_ct_kwh:.2f} ct/kWh netto auf {summe['eigendeckung']:.1f} kWh = "
                   f"{summe['eigendeckung'] * g.gebuehr_ct_kwh / 100:.2f} € netto (berechnet)".replace(".", ",")])
    for spalte, breite in zip("ABCDEFGHIJKL", (14, 24, 36, 14, 14, 16, 12, 12, 11, 10, 11, 14)):
        ws.column_dimensions[spalte].width = breite

    ws2 = wb.create_sheet("Monate")
    liste = monate(von, bis)
    ws2.append(["Eigendeckung je Monat in kWh"])
    ws2.append(["Wohnung", "Name", *(f"{a:%m/%Y}" for a, _ in liste), "Summe"])
    for zelle in ws2[2]:
        zelle.font = fett
    je_monat = auswertung.monatswerte(s, zps + [betreiber.zaehlpunkt], g.id)
    for _, t in wohnungen:
        reihe = [round(_wert(je_monat, t.zaehlpunkt, f"{a:%Y-%m}", "eigendeckung"), 3) for a, _ in liste]
        ws2.append([t.wohnung or "", f"{t.vorname} {t.nachname}".strip(), *reihe, round(sum(reihe), 3)])
    if not betreiber.zaehlpunkt.startswith(OHNE_ZP):
        reihe = [round(_wert(je_monat, betreiber.zaehlpunkt, f"{a:%Y-%m}", "erzeugung"), 3) for a, _ in liste]
        if any(reihe):
            ws2.append(["Anlage", "Erzeugung", *reihe, round(sum(reihe), 3)])
    ws2.column_dimensions["A"].width = 14
    ws2.column_dimensions["B"].width = 24
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()
