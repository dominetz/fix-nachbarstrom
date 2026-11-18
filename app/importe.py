"""Energiedaten-Exporte in die Datenbank übernehmen.

Ablauf: Datei lesen (eda_import) -> Gemeinschaft finden (Gemeinschafts-ID, sonst
Zählpunkte) -> Werte dieser Gemeinschaft für den Zeitraum ersetzen. Eine neue GEA
lässt sich direkt aus einem Export anlegen: Der Erzeugungszählpunkt wird zur
Anlage, alle anderen Zählpunkte werden Wohnungen.
"""
import secrets

from sqlalchemy import delete, text
from sqlmodel import Session, select

from . import eda_import
from .eda_import import ZEITFORMAT, Import
from .models import (Art, Gruppe, GruppenStatus, Messwert, Mitglied, Modell, Profiltyp, Rolle,
                     Spaltenzuordnung, Teilnehmer, jetzt)
from .zaehlpunkt import maskiert, normalisieren

OHNE_ZP = "OHNE-ZP-"  # Platzhalter, solange der Zählpunkt der Anlage nicht bekannt ist


def gemerkte_zuordnung(s: Session) -> dict[str, str]:
    return {z.schluessel: z.feld for z in s.exec(select(Spaltenzuordnung)).all()}


def zuordnung_merken(s: Session, titel: str, feld: str) -> None:
    schluessel = eda_import.schluessel(titel)
    z = s.exec(select(Spaltenzuordnung).where(Spaltenzuordnung.schluessel == schluessel)).first()
    z = z or Spaltenzuordnung(schluessel=schluessel, titel=titel, feld=feld)
    z.feld = feld
    s.add(z)


def einlesen(s: Session, inhalt: bytes, dateiname: str = "") -> Import:
    return eda_import.einlesen(inhalt, gemerkte_zuordnung(s), dateiname)


def mitglieder(s: Session, g: Gruppe) -> list[tuple[Mitglied, Teilnehmer]]:
    liste = s.exec(select(Mitglied).where(Mitglied.gruppe_id == g.id).order_by(Mitglied.id)).all()
    return [(m, s.get(Teilnehmer, m.teilnehmer_id)) for m in liste]


def zaehlpunkte(s: Session, g: Gruppe) -> dict[str, Teilnehmer]:
    e = s.get(Teilnehmer, g.erzeuger_id)
    return {t.zaehlpunkt: t for t in [e, *(t for _, t in mitglieder(s, g))]}


def ziel_finden(s: Session, imp: Import) -> tuple[Gruppe | None, str]:
    """Gemeinschaft zu einem Export. Zweiter Wert: woran sie erkannt wurde oder warum nicht."""
    gruppen = s.exec(select(Gruppe).where(Gruppe.status != GruppenStatus.BEENDET)).all()
    if imp.gemeinschafts_id:
        for g in gruppen:
            if g.gemeinschafts_id and normalisieren(g.gemeinschafts_id) == imp.gemeinschafts_id:
                return g, "über die Gemeinschafts-ID"
    treffer = []
    for g in gruppen:
        if imp.gemeinschafts_id and g.gemeinschafts_id:
            continue  # andere Gemeinschaft, auch wenn Zählpunkte gleich sind
        n = len(set(zaehlpunkte(s, g)) & imp.zaehlpunkte)
        if n:
            treffer.append((n, g))
    treffer.sort(key=lambda x: -x[0])
    if treffer and (len(treffer) == 1 or treffer[0][0] > treffer[1][0]):
        return treffer[0][1], "über die Zählpunkte"
    return None, "mehrere passen" if treffer else "keine passt"


def wohnung_anlegen(s: Session, g: Gruppe, zp: str, bezeichnung: str | None = None) -> Teilnehmer:
    """Zählpunkt als Wohnung in eine GEA aufnehmen (Name und Mail trägt man später nach)."""
    t = s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == zp)).first()
    if not t:
        betreiber = s.get(Teilnehmer, g.erzeuger_id)
        nummer = len(mitglieder(s, g)) + 1
        t = Teilnehmer(rolle=Rolle.ABNEHMER, vorname="", nachname="", wohnung=bezeichnung or f"Wohnung {nummer}",
                       strasse=betreiber.strasse, plz=betreiber.plz, ort=betreiber.ort, email="",
                       zaehlpunkt=zp, netzbetreiber=zp[2:8], profiltyp=Profiltyp.HAUSHALT)
        s.add(t)
        s.flush()
    if not s.exec(select(Mitglied).where(Mitglied.gruppe_id == g.id, Mitglied.teilnehmer_id == t.id)).first():
        s.add(Mitglied(gruppe_id=g.id, teilnehmer_id=t.id))
        s.flush()
    return t


def speichern(s: Session, g: Gruppe, imp: Import, neue_aufnehmen: bool | None = None) -> dict:
    """Werte der Gemeinschaft im Zeitraum des Exports ersetzen. Bei einer GEA werden neue
    Zählpunkte als Wohnung aufgenommen, bei P2P nur gemeldet."""
    bekannt = zaehlpunkte(s, g)
    neu = []
    if neue_aufnehmen if neue_aufnehmen is not None else g.art == Art.GEA:
        for zp in sorted(imp.zaehlpunkte - set(bekannt)):
            if zp in imp.erzeuger and g.art == Art.GEA:
                continue  # zweite Anlage: nicht als Wohnung führen
            t = wohnung_anlegen(s, g, zp)
            bekannt[zp] = t
            neu.append(t.name)
    zps = imp.zaehlpunkte & set(bekannt)
    von, bis = imp.von.strftime(ZEITFORMAT), imp.bis.strftime(ZEITFORMAT)
    s.exec(delete(Messwert).where(Messwert.gruppe_id == g.id, Messwert.zaehlpunkt.in_(zps),
                                  Messwert.beginn >= von, Messwert.beginn <= bis))
    zeilen = [{**w, "gruppe_id": g.id} for w in imp.werte if w["zaehlpunkt"] in zps]
    if zeilen:
        s.execute(Messwert.__table__.insert(), zeilen)
    if imp.gemeinschafts_id and not g.gemeinschafts_id:
        g.gemeinschafts_id = imp.gemeinschafts_id
    if g.art == Art.GEA and zeilen and (g.aktiv_seit is None or imp.von.date() < g.aktiv_seit):
        g.aktiv_seit = imp.von.date()
    s.add(g)
    return {"gruppe": g, "von": imp.von, "bis": imp.bis, "zaehlpunkte": len(zps), "werte": len(zeilen),
            "unbekannt": [maskiert(z) for z in sorted(imp.zaehlpunkte - set(bekannt))], "neu": neu,
            "hinweise": imp.hinweise}


def gea_anlegen(s: Session, imp: Import | None, angaben: dict) -> tuple[Gruppe, dict | None]:
    """Neue GEA mit Betreiber, Anlage und Wohnungen, auf Wunsch gleich mit den Werten aus dem Export."""
    anlage_zp = normalisieren(angaben.get("anlage_zp", ""))
    if not anlage_zp and imp:
        erzeuger = imp.erzeuger
        if erzeuger:  # größte Erzeugung zuerst
            summe = {zp: sum(w["erzeugung"] + w["restueberschuss"] for w in imp.werte if w["zaehlpunkt"] == zp)
                     for zp in erzeuger}
            anlage_zp = max(summe, key=summe.get)
    betreiber = s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == anlage_zp)).first() if anlage_zp else None
    if not betreiber:
        betreiber = Teilnehmer(
            rolle=Rolle.ERZEUGER, vorname=angaben.get("vorname", "").strip(),
            nachname=angaben.get("nachname", "").strip(), firma=angaben.get("firma", "").strip() or None,
            strasse=angaben.get("strasse", "").strip(), plz=angaben.get("plz", "").strip(),
            ort=angaben.get("ort", "").strip(), email=angaben.get("email", "").strip(),
            telefon=angaben.get("telefon", "").strip() or None,
            zaehlpunkt=anlage_zp or f"{OHNE_ZP}{secrets.token_hex(4).upper()}",
            netzbetreiber=anlage_zp[2:8] if anlage_zp else "", anlage_kwp=float(angaben.get("anlage_kwp") or 0),
            einwilligung_daten=True, einwilligung_zeit=jetzt())
        s.add(betreiber)
        s.flush()
    g = Gruppe(art=Art.GEA, name=angaben.get("name", "").strip() or None, erzeuger_id=betreiber.id,
               status=GruppenStatus(angaben.get("status") or GruppenStatus.VORSCHLAG.value),
               modell=Modell(angaben.get("modell") or Modell.SELBST.value),
               aufteilung=angaben.get("aufteilung") or "dynamisch",
               preis_ct_kwh=float(angaben.get("preis_ct_kwh") or 0), ust_satz=float(angaben.get("ust_satz") or 0),
               rechnung_monatlich=bool(angaben.get("rechnung_monatlich")),
               gebuehr_ct_kwh=float(angaben.get("gebuehr_ct_kwh") or 0),
               gemeinschafts_id=normalisieren(angaben.get("gemeinschafts_id", "")) or None, zonelevel=6,
               notiz=angaben.get("notiz", "").strip() or None)
    s.add(g)
    s.flush()
    ergebnis = None
    if imp:
        if imp.werte and g.status == GruppenStatus.VORSCHLAG:
            g.status = GruppenStatus.AKTIV  # Daten fließen schon, also ist die GEA in Betrieb
        for i, zp in enumerate(sorted(imp.zaehlpunkte - {anlage_zp} - imp.erzeuger), start=1):
            wohnung_anlegen(s, g, zp, f"Wohnung {i}")
        ergebnis = speichern(s, g, imp, neue_aufnehmen=False)
        weitere = imp.erzeuger - {anlage_zp}
        if weitere:
            ergebnis["hinweise"].append(f"{len(weitere)} weiterer Erzeugungszählpunkt wird nicht ausgewertet.")
    return g, ergebnis


def alte_werte_zuordnen(engine) -> None:
    """Werte aus der Zeit vor gruppe_id: der Gemeinschaft des Zählpunkts zuordnen."""
    with Session(engine) as s:
        if not s.exec(select(Messwert.id).where(Messwert.gruppe_id == None).limit(1)).first():  # noqa: E711
            return
        for g in s.exec(select(Gruppe).order_by(Gruppe.id)).all():
            zps = list(zaehlpunkte(s, g))
            s.exec(text("UPDATE messwert SET gruppe_id = :g WHERE gruppe_id IS NULL AND zaehlpunkt IN ("
                        + ",".join(f":z{i}" for i in range(len(zps))) + ")")
                   .bindparams(g=g.id, **{f"z{i}": z for i, z in enumerate(zps)}))
        s.commit()

