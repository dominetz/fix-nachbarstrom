"""Legt einen erfundenen Testbetrieb an: Teilnehmer, drei Gruppen und ein fiktives
Jahr Energiedaten (Oktober 2025 bis September 2026) als EDA-Exporte. Keine echten Kunden.

    python demo_daten.py          # nur in eine leere Datenbank
    python demo_daten.py --neu    # löscht ALLE Daten und legt den Testbetrieb neu an

Die Monatsexporte landen im Ordner testdaten/. Oktober bis August werden gleich
importiert, September bleibt zum Selbst-Ausprobieren liegen.
"""
import random
import sys
from datetime import date, timedelta

from sqlmodel import Session, SQLModel, select

from app import auswertung, config, energiedaten, matching, nachrichten
from app.main import engine, kundenlink, rechnung_anlegen
from app.models import (Gruppe, GruppenStatus, Messwert, Mitglied, Profiltyp, Rolle, Teilnehmer,
                        Unterschrift, Zustimmung, jetzt)

NB = "008100"  # Testkennung, kein echter Netzbetreiber
JAHR_VON, JAHR_BIS = date(2025, 10, 1), date(2026, 9, 30)
SELBST_TESTEN = "2026-09"  # dieser Monat wird nicht automatisch importiert


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
                       rechnung_monatlich=monatlich, marktpartner_id=f"PP90000{nr}",
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

        # Fiktives Jahr simulieren und als Monatsexporte schreiben
        config.TESTDATEN.mkdir(exist_ok=True)
        for alt in config.TESTDATEN.glob("*.xlsx"):
            alt.unlink()
        zeiten = energiedaten.viertelstunden(JAHR_VON, JAHR_BIS)
        for g, e, ab in gruppen[:2]:
            spuren = energiedaten.simulieren(e, ab, zeiten, seed=g.id)
            for erster, letzter in energiedaten.monate(JAHR_VON, JAHR_BIS):
                index = [i for i, z in enumerate(zeiten) if erster <= z.date() <= letzter]
                teil = [energiedaten.Spur(sp.teilnehmer, {k: v[index[0]:index[-1] + 1] for k, v in sp.werte.items()})
                        for sp in spuren]
                inhalt = energiedaten.export_schreiben(teil, zeiten[index[0]:index[-1] + 1], g.gemeinschafts_id)
                name = f"EDA_Export_Gruppe{g.id}_{erster:%Y-%m}.xlsx"
                (config.TESTDATEN / name).write_bytes(inhalt)
                if f"{erster:%Y-%m}" != SELBST_TESTEN:  # über denselben Weg wie ein echter Upload
                    imp = energiedaten.export_einlesen(inhalt)
                    s.execute(Messwert.__table__.insert(), imp.werte)
        s.commit()

        # Gruppe 1 rechnet monatlich ab: Oktober bis August schon abgerechnet, bis Juli bezahlt
        g, e, ab = gruppen[0]
        for erster, letzter in energiedaten.monate(JAHR_VON, date(2026, 8, 31)):
            werte = auswertung.summen(s, [a.zaehlpunkt for a in ab], erster, letzter)
            for a in ab:
                r = rechnung_anlegen(s, g, a, erster, letzter, werte[a.zaehlpunkt]["eigendeckung"],
                                     datum=letzter + timedelta(days=3), mail=False)
                if letzter < date(2026, 8, 1):
                    r.bezahlt_am = r.datum + timedelta(days=zufall.randint(2, 12))
        # Neue Anmeldungen ohne Gruppe bekommen ihre Willkommensmail in den Postausgang
        for i in (10, 11, 12):
            s.add(nachrichten.willkommen(leute[i], kundenlink(leute[i].id)))
        s.commit()
    print(f"Testbetrieb angelegt: {len(PERSONEN)} Teilnehmer, {len(GRUPPEN)} Gruppen, "
          f"fiktives Jahr {JAHR_VON:%m/%Y} bis {JAHR_BIS:%m/%Y}. Exporte im Ordner {config.TESTDATEN}/.")


if __name__ == "__main__":
    anlegen(neu="--neu" in sys.argv)
