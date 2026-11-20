"""Legt einen erfundenen Testbetrieb an: Teilnehmer, drei P2P-Gruppen, zwei GEAs, zwei
Anfragen und ein fiktives Jahr Energiedaten (Oktober 2025 bis September 2026) als
EDA-Exporte. Keine echten Kunden, alle Namen, Adressen und Nummern sind erfunden.

    python demo_daten.py          # nur in eine leere Datenbank
    python demo_daten.py --neu    # löscht ALLE Daten und legt den Testbetrieb neu an

Oktober bis August werden gleich gespeichert, September bleibt zum Selbst-Ausprobieren
liegen. Die Exporte für August und September landen als Excel im Ordner testdaten/.
"""
import random
import sys
from datetime import date, timedelta

from sqlmodel import Session, SQLModel, select

from app import auswertung, config, energiedaten, importe, matching, nachrichten
from app.main import engine, gebuehr_anlegen, kundenlink, rechnung_anlegen
from app.models import (Anfrage, Art, Gruppe, GruppenStatus, Mitglied, Modell, Profiltyp, Rolle, Teilnehmer,
                        Unterschrift, Zustimmung, jetzt)

NB = "008100"  # Testkennung, kein echter Netzbetreiber
JAHR_VON, JAHR_BIS = date(2025, 10, 1), date(2026, 9, 30)
SELBST_TESTEN = "2026-09"  # dieser Monat wird nicht automatisch importiert
DATEIEN_AB = "2026-08"  # ab diesem Monat liegen die Exporte als Excel-Dateien im Ordner testdaten/


def zp(n: int) -> str:
    return f"AT{NB}06800{n:020d}"


def iban(bank: str, konto: str) -> str:
    """Gültige österreichische Test-IBAN (Prüfziffer nach ISO 13616)."""
    bban = bank + konto
    ziffern = "".join(str(int(c, 36)) for c in bban + "AT00")
    return f"AT{98 - int(ziffern) % 97:02d}{bban}"


# Vorname, Nachname, Firma, Rolle, Profil, kWh/Jahr, kWp, Trafo
PERSONEN = [
    ("Anna", "Sonnig", None, Rolle.ERZEUGER, Profiltyp.HAUSHALT, 3200, 12.0, "T-101"),
    ("Bernd", "Dach", None, Rolle.ERZEUGER, Profiltyp.HAUSHALT_TAGSUEBER, 4000, 8.5, "T-102"),
    ("Clara", "Nachbar", None, Rolle.ABNEHMER, Profiltyp.HAUSHALT, 3500, 0, "T-101"),
    ("Thomas", "Holz", "Tischlerei Holz", Rolle.ABNEHMER, Profiltyp.GEWERBE, 18000, 0, "T-101"),
    ("Doris", "Wärme", None, Rolle.ABNEHMER, Profiltyp.WAERMEPUMPE, 7500, 0, "T-101"),
    ("Emil", "Auto", None, Rolle.ABNEHMER, Profiltyp.EAUTO, 5000, 0, "T-102"),
    ("Petra", "Gesund", "Praxis Gesund", Rolle.ABNEHMER, Profiltyp.GEWERBE, 9000, 0, "T-103"),
    ("Franz", "Feld", None, Rolle.ERZEUGER, Profiltyp.HAUSHALT, 3800, 10.0, "T-104"),
    ("Gabi", "Garten", None, Rolle.ABNEHMER, Profiltyp.HAUSHALT, 4200, 0, "T-104"),
    ("Helga", "Hof", None, Rolle.ERZEUGER, Profiltyp.HAUSHALT, 5000, 15.0, "T-105"),
    ("Ivo", "Imker", None, Rolle.ABNEHMER, Profiltyp.HAUSHALT_TAGSUEBER, 3000, 0, "T-105"),
    ("Karl", "Korn", "Bäckerei Korn", Rolle.ABNEHMER, Profiltyp.GEWERBE, 25000, 0, "T-105"),
]
ORTE = ["Feldkirch", "Feldkirch", "Feldkirch", "Feldkirch", "Feldkirch", "Rankweil", "Rankweil",
        "Götzis", "Götzis", "Altach", "Altach", "Altach"]

# GEA: Name, Betreiber (Vorname, Nachname, Firma), Adresse, kWp, Modell, Preis, Gebühr, statisch,
# Wohnungen (Top, Vorname, Nachname, Profil, kWh/Jahr)
GEAS = [
    ("Wohnanlage Sonnenhof", ("Lena", "Berger", "Eigentümergemeinschaft Sonnenhof"), ("Sonnenhofweg 4", "6850", "Dornbirn"),
     30.0, Modell.SELBST, 12.0, 1.0, False, [
         ("Top 1", "Jonas", "Fink", Profiltyp.HAUSHALT, 2400), ("Top 2", "Mira", "Lenz", Profiltyp.HAUSHALT_TAGSUEBER, 3100),
         ("Top 3", "Paul", "Stern", Profiltyp.HAUSHALT, 1800), ("Top 4", "Sara", "Wald", Profiltyp.EAUTO, 4600),
         ("Top 5", "Theo", "Brunner", Profiltyp.HAUSHALT, 2900), ("Top 6", "Ute", "Sommer", Profiltyp.HAUSHALT_TAGSUEBER, 2200),
         ("Top 7", "Vera", "Kaiser", Profiltyp.WAERMEPUMPE, 5200), ("Top 8", "Willi", "Nagel", Profiltyp.HAUSHALT, 1600)]),
    ("Haus am Bach", ("Martin", "Bachmann", None), ("Bachgasse 2", "6845", "Hohenems"),
     12.0, Modell.VERWALTET, 10.0, 1.5, True, [
         ("Top 1", "Anton", "Egger", Profiltyp.HAUSHALT, 2600), ("Top 2", "Berta", "Fuchs", Profiltyp.HAUSHALT_TAGSUEBER, 2100),
         ("Top 3", "Carl", "Graf", Profiltyp.HAUSHALT, 3300), ("Top 4", "Dana", "Hofer", Profiltyp.HAUSHALT, 2800)]),
]

# Erzeuger-Nr., Abnehmer-Nr., Preis ct/kWh, monatlich, Zone, Status
GRUPPEN = [
    (1, [3, 4, 5], 11.0, True, 3, GruppenStatus.AKTIV),
    (2, [6, 7], 9.0, False, 4, GruppenStatus.AKTIV),
    (8, [9], 10.0, False, 3, GruppenStatus.EDA_ANGEMELDET),
]


def anlegen(neu: bool = False) -> None:
    if neu:
        SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    zufall = random.Random(7)
    with Session(engine) as s:
        if s.exec(select(Teilnehmer)).first():
            sys.exit("Die Datenbank enthält schon Daten. Zum Überschreiben: python demo_daten.py --neu")
        leute = {}
        for i, (vor, nach, firma, rolle, profil, kwh, kwp, trafo) in enumerate(PERSONEN, start=1):
            t = Teilnehmer(rolle=rolle, vorname=vor, nachname=nach, firma=firma, strasse=f"Beispielweg {i}",
                           plz={"Feldkirch": "6800", "Rankweil": "6830", "Götzis": "6840", "Altach": "6844"}[ORTE[i - 1]],
                           ort=ORTE[i - 1], email=f"demo{i}@example.com", zaehlpunkt=zp(i), netzbetreiber=NB,
                           zaehlpunkt_bezug=zp(900 + i) if rolle == Rolle.ERZEUGER else None,
                           trafo_id=trafo, umspannwerk_id="UW-1", profiltyp=profil, jahresverbrauch_kwh=kwh,
                           anlage_kwp=kwp, einwilligung_daten=True, einwilligung_zeit=jetzt(),
                           iban=iban("20111", f"{i:011d}") if rolle == Rolle.ERZEUGER else None)
            s.add(t)
            leute[i] = t
        s.commit()
        for t in leute.values():
            s.refresh(t)

        gruppen = []
        for nr, (e, ab, preis, monatlich, zone, status) in enumerate(GRUPPEN, start=1):
            menge = matching.geteilte_menge(leute[e], [leute[a] for a in ab])
            g = Gruppe(erwartete_kwh_jahr=round(sum(menge.values())), erzeuger_id=leute[e].id, status=status, zonelevel=zone, preis_ct_kwh=preis,
                       rechnung_monatlich=monatlich, marktpartner_id=f"PP90000{nr}", gebuehr_ct_kwh=1.0,
                       gemeinschafts_id=f"AT{NB}0000000000000000000TEST{nr:04d}",
                       aktiv_seit=JAHR_VON if status == GruppenStatus.AKTIV else None,
                       notiz="Testgruppe, erfundene Daten")
            s.add(g)
            s.commit()
            s.refresh(g)
            gruppen.append((g, leute[e], [leute[a] for a in ab]))
            for t in [leute[e]] + [leute[a] for a in ab]:
                if t.rolle == Rolle.ABNEHMER:
                    s.add(Mitglied(gruppe_id=g.id, teilnehmer_id=t.id, erwartete_kwh_jahr=round(menge[t.id])))
                for dok in ("vertrag", "vollmacht"):
                    s.add(Unterschrift(gruppe_id=g.id, teilnehmer_id=t.id, dokument=dok, name_getippt=f"{t.vorname} {t.nachname}"))
                heute = date.today()
                if status == GruppenStatus.AKTIV:
                    s.add(Zustimmung(gruppe_id=g.id, teilnehmer_id=t.id, freigegeben_am=JAHR_VON - timedelta(days=9),
                                     erteilt_am=JAHR_VON - timedelta(days=6)))
                else:  # Gruppe 3: der Abnehmer hat noch nicht zugestimmt, Erinnerung ist fällig
                    s.add(Zustimmung(gruppe_id=g.id, teilnehmer_id=t.id, freigegeben_am=heute - timedelta(days=3),
                                     erteilt_am=heute - timedelta(days=2) if t.rolle == Rolle.ERZEUGER else None))
        s.commit()

        # GEAs: Betreiber mit dem Zählpunkt der Anlage, Wohnungen als Mitglieder
        geas = []
        for nr, (name, (vor, nach, firma), (strasse, plz, ort), kwp, modell, preis, gebuehr, statisch,
                 wohnungen) in enumerate(GEAS, start=1):
            basis = 20 * nr
            b = Teilnehmer(rolle=Rolle.ERZEUGER, vorname=vor, nachname=nach, firma=firma, strasse=strasse, plz=plz,
                           ort=ort, email=f"gea{nr}@example.com", zaehlpunkt=zp(basis), netzbetreiber=NB,
                           anlage_kwp=kwp, jahresverbrauch_kwh=0, einwilligung_daten=True, einwilligung_zeit=jetzt(),
                           iban=iban("20111", f"{basis:011d}") if modell == Modell.VERWALTET else None)
            s.add(b)
            s.flush()
            g = Gruppe(art=Art.GEA, name=name, erzeuger_id=b.id, status=GruppenStatus.AKTIV, modell=modell,
                       aufteilung="statisch" if statisch else "dynamisch", preis_ct_kwh=preis, gebuehr_ct_kwh=gebuehr,
                       rechnung_monatlich=True, aktiv_seit=JAHR_VON, zonelevel=6,
                       gemeinschafts_id=f"AT{NB}00000000000000000GEA{nr:05d}", notiz="Test-GEA, erfundene Daten")
            s.add(g)
            s.flush()
            leute_gea = []
            for i, (top, wv, wn, profil, kwh) in enumerate(wohnungen, start=1):
                w = Teilnehmer(rolle=Rolle.ABNEHMER, vorname=wv, nachname=wn, wohnung=top, strasse=strasse, plz=plz,
                               ort=ort, email=f"gea{nr}top{i}@example.com", zaehlpunkt=zp(basis + i),
                               netzbetreiber=NB, profiltyp=profil, jahresverbrauch_kwh=kwh)
                s.add(w)
                s.flush()
                s.add(Mitglied(gruppe_id=g.id, teilnehmer_id=w.id,
                               anteil_prozent=round(100 / len(wohnungen), 2) if statisch else None))
                leute_gea.append(w)
            geas.append((g, b, leute_gea, {w.id: 1 / len(wohnungen) for w in leute_gea} if statisch else None))
            s.add(nachrichten.willkommen_gea(b, g.id, g.titel, kundenlink(b.id)))
        s.commit()

        # Fiktives Jahr simulieren und als Monatsexporte schreiben
        config.TESTDATEN.mkdir(exist_ok=True)
        for alt in config.TESTDATEN.glob("*.xlsx"):
            alt.unlink()
        zeiten = energiedaten.viertelstunden(JAHR_VON, JAHR_BIS)
        laeufe = [(g, e, ab, None, f"Gruppe{g.id}") for g, e, ab in gruppen[:2]]
        laeufe += [(g, b, ab, schl, "GEA_" + g.name.split()[-1]) for g, b, ab, schl in geas]
        for g, e, ab, schl, kurz in laeufe:
            spuren = energiedaten.simulieren(e, ab, zeiten, seed=g.id, schluessel=schl)
            for erster, letzter in energiedaten.monate(JAHR_VON, JAHR_BIS):
                index = [i for i, z in enumerate(zeiten) if erster <= z.date() <= letzter]
                teil = [energiedaten.Spur(sp.teilnehmer, {k: v[index[0]:index[-1] + 1] for k, v in sp.werte.items()})
                        for sp in spuren]
                if f"{erster:%Y-%m}" >= DATEIEN_AB:  # Excel schreiben dauert, nur die letzten Monate
                    inhalt = energiedaten.export_schreiben(teil, zeiten[index[0]:index[-1] + 1], g.gemeinschafts_id)
                    (config.TESTDATEN / f"EDA_Export_{kurz}_{erster:%Y-%m}.xlsx").write_bytes(inhalt)
                if f"{erster:%Y-%m}" != SELBST_TESTEN:  # Speichern wie bei einem echten Upload
                    importe.speichern(s, g, energiedaten.als_import(teil, zeiten[index[0]:index[-1] + 1],
                                                                    g.gemeinschafts_id))
        s.commit()

        # Gruppe 1 rechnet monatlich ab: Oktober bis August schon abgerechnet, bis Juli bezahlt
        g, e, ab = gruppen[0]
        for erster, letzter in energiedaten.monate(JAHR_VON, date(2026, 8, 31)):
            werte = auswertung.summen(s, [a.zaehlpunkt for a in ab], erster, letzter, g.id)
            for a in ab:
                r = rechnung_anlegen(s, g, a, erster, letzter, werte[a.zaehlpunkt]["eigendeckung"],
                                     datum=letzter + timedelta(days=3), mail=False)
                if letzter < date(2026, 8, 1):
                    r.bezahlt_am = r.datum + timedelta(days=zufall.randint(2, 12))
            x = gebuehr_anlegen(s, g, erster, letzter, sum(werte[a.zaehlpunkt]["eigendeckung"] for a in ab),
                                datum=letzter + timedelta(days=3), mail=False)
            if x and letzter < date(2026, 8, 1):
                x.bezahlt_am = x.datum + timedelta(days=zufall.randint(2, 12))
        # GEAs: „Haus am Bach“ rechnet FIX monatlich ab, beim „Sonnenhof“ nur die Servicegebühr
        for g, b, ab, _ in geas:
            for erster, letzter in energiedaten.monate(JAHR_VON, date(2026, 8, 31)):
                werte = auswertung.summen(s, [a.zaehlpunkt for a in ab], erster, letzter, g.id)
                if g.modell == Modell.VERWALTET:
                    for a in ab:
                        r = rechnung_anlegen(s, g, a, erster, letzter, werte[a.zaehlpunkt]["eigendeckung"],
                                             datum=letzter + timedelta(days=3), mail=False)
                        if letzter < date(2026, 8, 1):
                            r.bezahlt_am = r.datum + timedelta(days=zufall.randint(2, 12))
                x = gebuehr_anlegen(s, g, erster, letzter, sum(werte[a.zaehlpunkt]["eigendeckung"] for a in ab),
                                    datum=letzter + timedelta(days=3), mail=False)
                if x and letzter < date(2026, 8, 1):
                    x.bezahlt_am = x.datum + timedelta(days=zufall.randint(2, 12))
        # Neue Anmeldungen ohne Gruppe bekommen ihre Willkommensmail in den Postausgang
        for i in (10, 11, 12):
            s.add(nachrichten.willkommen(leute[i], kundenlink(leute[i].id)))
        # Zwei Anfragen über die Startseite
        s.add(Anfrage(art="gea", vorname="Nora", nachname="Talhammer", firma="Hausverwaltung Talblick",
                      email="anfrage1@example.com", telefon="0000 000000", strasse="Talblickstraße 9", plz="6800",
                      ort="Feldkirch", wohnungen=12, anlage_kwp=25, registriert="nein", modell="verwaltet",
                      nachricht="Wir planen eine PV-Anlage auf dem Dach und möchten den Strom an die Mieter weitergeben. "
                                "Was brauchen wir dafür?", einwilligung_zeit=jetzt()))
        s.add(Anfrage(art="frage", vorname="Otto", nachname="Rieder", email="anfrage2@example.com", ort="Rankweil",
                      nachricht="Mein Nachbar hat eine PV-Anlage. Kann ich auch nur mit ihm teilen, ohne andere?",
                      einwilligung_zeit=jetzt()))
        s.commit()
    print(f"Testbetrieb angelegt: {len(PERSONEN)} Teilnehmer, {len(GRUPPEN)} P2P-Gruppen, {len(GEAS)} GEAs, "
          f"fiktives Jahr {JAHR_VON:%m/%Y} bis {JAHR_BIS:%m/%Y}. Exporte im Ordner {config.TESTDATEN}/.")


if __name__ == "__main__":
    anlegen(neu="--neu" in sys.argv)
