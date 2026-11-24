import os
import re
from datetime import date, datetime, timedelta

os.environ["NACHBARSTROM_DB"] = "sqlite:///:memory:"
os.environ["NACHBARSTROM_ADMIN_PASSWORD"] = "test"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import abrechnung, main, matching
from app.erinnerungen import faellige_erinnerungen
from app.models import Gruppe, GruppenStatus, Profiltyp, Rechnung, Rolle, Teilnehmer, Zustimmung
from app.zaehlpunkt import ist_gueltig, netzbetreiber_code

NB = "008100"  # Testkennung, kein echter Netzbetreiber


def zp(n: int, nb: str = NB) -> str:
    return f"AT{nb}06800{n:020d}"


def test_zaehlpunkt():
    assert ist_gueltig(zp(1))
    assert ist_gueltig(" at 008100 06800 " + "0" * 19 + "1")
    assert not ist_gueltig("AT123")
    assert netzbetreiber_code(zp(1)) == NB


def _t(id, rolle, profil=Profiltyp.HAUSHALT, kwh=3500, kwp=0, trafo="T1", nb=NB):
    return Teilnehmer(id=id, rolle=rolle, vorname="A", nachname=str(id), strasse="x", plz="6800", ort="Feldkirch",
                      email=f"{id}@example.com", zaehlpunkt=zp(id, nb), netzbetreiber=nb, trafo_id=trafo,
                      umspannwerk_id="U1", profiltyp=profil, jahresverbrauch_kwh=kwh, anlage_kwp=kwp,
                      einwilligung_daten=True)


def test_pv_jahresertrag():
    pv = matching.pv_profil(10)
    jahr = sum(tage * sum(pv[n]) for n, tage, *_ in matching.JAHRESZEITEN)
    assert jahr == pytest.approx(10 * matching.PV_ERTRAG_KWH_JE_KWP, rel=0.01)


def test_verbrauchsprofil_skaliert():
    p = matching.verbrauch_profil(Profiltyp.GEWERBE, 20000)
    jahr = sum(tage * sum(p[n]) for n, tage, *_ in matching.JAHRESZEITEN)
    assert jahr == pytest.approx(20000, rel=0.01)


def test_gewerbe_teilt_mehr_als_haushalt():
    e = _t(1, Rolle.ERZEUGER, kwh=3000, kwp=10)
    haushalt = _t(2, Rolle.ABNEHMER, Profiltyp.HAUSHALT, 4000)
    betrieb = _t(3, Rolle.ABNEHMER, Profiltyp.GEWERBE, 4000)
    m = matching.geteilte_menge(e, [haushalt])[2]
    b = matching.geteilte_menge(e, [betrieb])[3]
    assert 0 < m < b


def test_geteilt_nie_mehr_als_ueberschuss():
    e = _t(1, Rolle.ERZEUGER, kwh=3000, kwp=5)
    ab = [_t(i, Rolle.ABNEHMER, Profiltyp.GEWERBE, 50000) for i in range(2, 5)]
    v = matching.Vorschlag(e, ab, 3, matching.geteilte_menge(e, ab))
    assert v.kwh_gesamt <= v.ueberschuss_kwh + 0.01


def test_zonen_und_netzbetreiber():
    e = _t(1, Rolle.ERZEUGER, kwp=10)
    assert matching.zonelevel(e, _t(2, Rolle.ABNEHMER)) == 3
    assert matching.zonelevel(e, _t(3, Rolle.ABNEHMER, trafo="T9")) == 4
    assert matching.zonelevel(e, _t(4, Rolle.ABNEHMER, nb="009999")) is None


def test_vorschlag_bevorzugt_gleichen_trafo_und_max_fuenf():
    e = _t(1, Rolle.ERZEUGER, kwh=2000, kwp=30)
    gleich = [_t(i, Rolle.ABNEHMER, Profiltyp.GEWERBE, 8000) for i in range(2, 10)]
    fremd = [_t(20, Rolle.ABNEHMER, Profiltyp.GEWERBE, 8000, nb="009999")]
    v = matching.gruppe_vorschlagen(e, gleich + fremd)
    assert v.zonelevel == 3 and len(v.abnehmer) <= 5
    assert all(a.netzbetreiber == NB for a in v.abnehmer)


def test_erinnerungen():
    t = _t(2, Rolle.ABNEHMER)
    heute = date(2026, 11, 10)
    z = Zustimmung(id=1, gruppe_id=1, teilnehmer_id=2, freigegeben_am=heute - timedelta(days=2))
    assert [e.grund for e in faellige_erinnerungen([(z, t)], heute)] == ["erinnerung"]
    z.erinnerungen = 1
    assert faellige_erinnerungen([(z, t)], heute) == []
    z.erteilt_am = heute
    assert faellige_erinnerungen([(z, t)], heute + timedelta(days=30)) == []
    z2 = Zustimmung(id=2, gruppe_id=1, teilnehmer_id=2, freigegeben_am=heute - timedelta(days=20))
    assert faellige_erinnerungen([(z2, t)], heute)[0].grund == "abgelaufen"


def test_report_einlesen_deutsches_format():
    inhalt = f"Zaehlpunkt;Von;Bis;kWh\n{zp(2)};01.10.2026;31.10.2026;1.234,5\n"
    z = abrechnung.report_einlesen(inhalt)
    assert z[0].kwh == 1234.5 and z[0].bis == date(2026, 10, 31)
    with pytest.raises(ValueError):
        abrechnung.report_einlesen("a;b\n1;2\n")


def test_betrag():
    b = abrechnung.berechnen(100, 10, 0.2)
    assert (b.netto, b.ust, b.brutto) == (10.0, 2.0, 12.0)


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(main, "engine", engine)
    SQLModel.metadata.create_all(engine)
    with TestClient(main.app) as c:
        yield c, engine


@pytest.fixture
def laufend(monkeypatch):
    """P2P im Modell „laufend“: FIX rechnet im Namen des Erzeugers ab."""
    from app import config
    monkeypatch.setattr(config, "P2P_ABRECHNUNG", "laufend")


def _anmelden(c, rolle, n, **extra):
    daten = dict(rolle=rolle, vorname="Vor", nachname=f"Name{n}", strasse="Weg 1", plz="6800", ort="Feldkirch",
                 email=f"t{n}@example.com", zaehlpunkt=zp(n), trafo_id="T1", umspannwerk_id="U1",
                 profiltyp="gewerbe", jahresverbrauch_kwh=6000, anlage_kwp=0, einwilligung="true")
    daten.update(extra)
    return c.post("/anmelden", data=daten, follow_redirects=False)


def test_ablauf_von_anmeldung_bis_rechnung(client, laufend):
    c, engine = client
    auth = ("admin", "test")
    assert c.get("/verwaltung").status_code == 401

    r = _anmelden(c, "erzeuger", 1, anlage_kwp=12, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    assert r.status_code == 303
    danke = c.get(r.headers["location"]).text
    token = re.search(r"einladung=([\w-]+)", danke).group(1)

    assert c.get(f"/anmelden?einladung={token}").status_code == 200
    assert _anmelden(c, "abnehmer", 2, einladung=token).status_code == 303
    assert _anmelden(c, "abnehmer", 3).status_code == 303
    # Doppelter Zählpunkt und fehlende Einwilligung werden abgelehnt
    assert "schon angemeldet" in _anmelden(c, "abnehmer", 3).text
    assert "Einwilligung" in _anmelden(c, "abnehmer", 4, einwilligung="").text

    seite = c.get("/verwaltung/matching", auth=auth)
    assert "Gruppe anlegen" in seite.text
    ids = re.search(r'name="abnehmer_ids" value="([\d,]+)"', seite.text).group(1)
    assert ids.split(",")[0] == "2"  # eingeladener Wunschpartner zuerst

    r = c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": ids}, auth=auth, follow_redirects=False)
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    gruppe = c.get(f"/verwaltung/gruppe/{gid}", auth=auth).text
    for teil in ("vertrag", "vollmacht", "anmeldung"):
        assert c.get(f"/verwaltung/gruppe/{gid}/{teil}", auth=auth).status_code == 200

    links = re.findall(r"http://localhost:8000(/unterschreiben/\S+?)<", gruppe)
    assert len(links) == 1 + len(ids.split(","))
    assert c.get(links[0].rsplit("/", 1)[0] + "/falsch").status_code == 404
    for pfad in links:
        tid = int(pfad.split("/")[3])
        with Session(engine) as s:
            t = s.get(Teilnehmer, tid)
        c.post(pfad, data={"gelesen": "true", "name_getippt": f"{t.vorname} {t.nachname}"})
    with Session(engine) as s:
        assert s.get(Gruppe, gid).status == GruppenStatus.UNTERSCHRIEBEN

    c.post(f"/verwaltung/gruppe/{gid}", auth=auth, data={
        "status": "eda_angemeldet", "preis_ct_kwh": 10, "ust_satz": 0,
        "marktpartner_id": "PP000001", "gemeinschafts_id": "AT0081000000000000000000000000001"})
    heute = date.today().isoformat()
    for tid in [1] + [int(i) for i in ids.split(",")]:
        c.post(f"/verwaltung/gruppe/{gid}/zustimmung", auth=auth,
               data={"teilnehmer_id": tid, "freigegeben_am": heute, "erteilt_am": heute})
    with Session(engine) as s:
        assert s.get(Gruppe, gid).status == GruppenStatus.AKTIV

    csv = f"zaehlpunkt;von;bis;kwh\n{zp(2)};2026-10-01;2026-10-31;150\n{zp(99)};2026-10-01;2026-10-31;5\n"
    r = c.post(f"/verwaltung/gruppe/{gid}/abrechnung", auth=auth, files={"report": ("r.csv", csv, "text/csv")})
    assert "1 Rechnung" in r.text and "AT…" in r.text
    with Session(engine) as s:
        rechnung = s.exec(select(Rechnung)).one()
    assert rechnung.brutto == 15.0
    text = c.get(f"/verwaltung/rechnung/{rechnung.id}", auth=auth).text
    assert zp(2) in text and zp(1) in text and "Schlichtungsstelle" in text
    # Gleicher Report ein zweites Mal erzeugt keine doppelte Rechnung
    r = c.post(f"/verwaltung/gruppe/{gid}/abrechnung", auth=auth, files={"report": ("r.csv", csv, "text/csv")})
    assert "0 Rechnung" in r.text


def test_abnehmer_nur_einmal_vorgeschlagen(client):
    c, _ = client
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "erzeuger", 2, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 3)
    seite = c.get("/verwaltung/matching", auth=("admin", "test")).text
    assert seite.count('name="abnehmer_ids"') == 1
    assert "Warteliste" in seite


def test_iban_und_qr():
    from app import zahlung
    assert zahlung.iban_gueltig("AT61 1904 3002 3457 3201")  # Beispiel-IBAN aus der Norm
    assert not zahlung.iban_gueltig("AT61 1904 3002 3457 3202")
    daten = zahlung.epc_daten("Anna Sonnig", "AT61 1904 3002 3457 3201", 12.5, "Rechnung NS-1")
    assert daten.split("\n")[6:8] == ["AT611904300234573201", "EUR12.50"]
    assert zahlung.qr_svg("A", "AT611904300234573201", 1, "x").startswith("<svg")


def test_dienstleistung_kundenbereich_postausgang_preis(client, laufend):
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=12, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    post = c.get("/verwaltung/postausgang", auth=auth).text
    assert post.count("Ihre Anmeldung ist da") == 2
    bereich = re.search(r"http://localhost:8000(/mein/2/\w+)", post).group(1)
    assert "Wir suchen gerade" in c.get(bereich).text
    assert c.get("/mein/2/falsch").status_code == 404

    r = c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth, follow_redirects=False)
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    seite = c.get(bereich).text
    assert "Vertrag und Vollmacht unterschreiben" in seite and "Partner gefunden" in seite
    assert c.get(f"{bereich}/infoblatt").status_code == 200
    assert "Partner ist gefunden" in c.get("/verwaltung/postausgang", auth=auth).text

    # Erzeuger hinterlegt IBAN im eigenen Bereich
    erz = re.search(r"http://localhost:8000(/mein/1/\w+)", c.get("/verwaltung/postausgang", auth=auth).text).group(1)
    c.post(f"{erz}/iban", data={"iban": "AT61 1904 3002 3457 3202"})
    assert "stimmt nicht" in c.get(f"{erz}?iban_fehler=1").text
    c.post(f"{erz}/iban", data={"iban": "AT61 1904 3002 3457 3201"})
    assert "AT61 1904" in c.get(erz).text

    c.post(f"/verwaltung/gruppe/{gid}", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 10, "ust_satz": 0})
    # Preis in aktiver Gruppe nur per Ankündigung
    assert c.post(f"/verwaltung/gruppe/{gid}", auth=auth,
                  data={"status": "aktiv", "preis_ct_kwh": 12, "ust_satz": 0}).status_code == 400
    zu_frueh = (date.today() + timedelta(days=10)).isoformat()
    assert c.post(f"/verwaltung/gruppe/{gid}/preisaenderung", auth=auth,
                  data={"neuer_preis": 12, "ab": zu_frueh, "grund": "x"}).status_code == 400
    ab = date.today() + timedelta(days=40)
    c.post(f"/verwaltung/gruppe/{gid}/preisaenderung", auth=auth,
           data={"neuer_preis": 12, "ab": ab.isoformat(), "grund": "Höhere Kosten"})
    assert "Neuer Preis ab" in c.get("/verwaltung/postausgang", auth=auth).text

    # Abrechnung vor und nach dem Stichtag
    csv = (f"zaehlpunkt;von;bis;kwh\n{zp(2)};{date.today()};{date.today()};100\n"
           f"{zp(2)};{ab};{ab + timedelta(days=40)};100\n")
    c.post(f"/verwaltung/gruppe/{gid}/abrechnung", auth=auth, files={"report": ("r.csv", csv, "text/csv")})
    with Session(engine) as s:
        rechnungen = s.exec(select(Rechnung).order_by(Rechnung.bis)).all()
    assert [r.brutto for r in rechnungen] == [10.0, 12.0]
    seite = c.get(bereich).text
    assert rechnungen[0].nummer in seite
    rechnung = c.get(f"{bereich}/rechnung/{rechnungen[0].id}").text
    assert "<svg" in rechnung and "AT61 1904" in rechnung
    assert c.get(f"{erz}/rechnung/{rechnungen[0].id}").status_code == 200
    assert "Einnahmen je Jahr" in c.get(erz).text

    # Als gesendet markieren leert den Postausgang
    with Session(engine) as s:
        offen = s.exec(select(main.Nachricht)).all()
    for n in offen:
        c.post(f"/verwaltung/postausgang/{n.id}/gesendet", auth=auth)
    assert "Nichts offen" in c.get("/verwaltung/postausgang", auth=auth).text
    assert "P2P-Gruppen" in c.get("/verwaltung", auth=auth).text


def test_neue_spalten_in_alter_datenbank(monkeypatch, tmp_path):
    from sqlalchemy import inspect, text
    alt = create_engine(f"sqlite:///{tmp_path / 'alt.db'}")
    with alt.begin() as v:
        v.execute(text("CREATE TABLE teilnehmer (id INTEGER PRIMARY KEY, vorname VARCHAR)"))
    monkeypatch.setattr(main, "engine", alt)
    main._spalten_ergaenzen()
    assert "iban" in {c["name"] for c in inspect(alt).get_columns("teilnehmer")}


def test_geheimnis_bleibt_nach_neustart(monkeypatch, tmp_path):
    from app import config
    monkeypatch.delenv("NACHBARSTROM_SECRET", raising=False)
    monkeypatch.setattr(config, "GEHEIMNIS_DATEI", tmp_path / "g")
    erster = config._geheimnis()
    assert len(erster) == 64 and config._geheimnis() == erster


def _kleine_gruppe():
    e = _t(1, Rolle.ERZEUGER, kwh=3000, kwp=10)
    return e, [_t(2, Rolle.ABNEHMER, Profiltyp.HAUSHALT, 4000), _t(3, Rolle.ABNEHMER, Profiltyp.GEWERBE, 12000)]


def test_eda_export_schreiben_und_lesen():
    from app import energiedaten as ed
    e, ab = _kleine_gruppe()
    zeiten = ed.viertelstunden(date(2026, 6, 1), date(2026, 6, 3))
    spuren = ed.simulieren(e, ab, zeiten)
    erz = spuren[0].werte
    geteilt = sum(sum(s.werte["eigendeckung"]) for s in spuren[1:])
    # Was geteilt wird plus Rest ergibt die Einspeisung, kein Abnehmer bekommt mehr als er verbraucht
    assert geteilt + sum(erz["restueberschuss"]) == pytest.approx(sum(erz["erzeugung"]))
    assert all(d <= v + 1e-9 for s in spuren[1:] for d, v in zip(s.werte["eigendeckung"], s.werte["verbrauch"]))
    imp = ed.export_einlesen(ed.export_schreiben(spuren, zeiten, "AT-TEST"))
    assert len(imp.werte) == 3 * 96 * 3 and imp.zaehlpunkte == {zp(1), zp(2), zp(3)}
    assert imp.von == zeiten[0] and imp.bis == zeiten[-1]
    eingelesen = sum(w["eigendeckung"] for w in imp.werte if w["zaehlpunkt"] == zp(3))
    assert eingelesen == pytest.approx(sum(spuren[2].werte["eigendeckung"]), abs=0.01)


def test_eda_export_mit_anderen_ueberschriften():
    import io
    from openpyxl import Workbook
    from app import energiedaten as ed
    wb = Workbook()
    ws = wb.active
    ws.append(["Bericht"])
    ws.append(["Zählpunkt", zp(2), zp(1)])
    ws.append(["Energy direction", "CONSUMPTION", "GENERATION"])
    ws.append(["Zeit", "Eigendeckung gemeinschaftliche Erzeugung [KWH]", "Gesamte gemeinschaftliche Erzeugung [KWH]"])
    ws.append(["01.06.2026 12:00", "1,5", 2])
    ws.append(["01.06.2026 12:15", "0,5", 1])
    puffer = io.BytesIO()
    wb.save(puffer)
    imp = ed.export_einlesen(puffer.getvalue())
    assert sum(w["eigendeckung"] for w in imp.werte) == 2.0 and sum(w["erzeugung"] for w in imp.werte) == 3.0
    with pytest.raises(ValueError):
        ed.export_einlesen(b"keine Excel-Datei")


def test_abrechnungslauf_aus_energiedaten(client, laufend):
    from app import energiedaten as ed
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    r = c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth, follow_redirects=False)
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    c.post(f"/verwaltung/gruppe/{gid}", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 10, "ust_satz": 0,
                                                         "rechnung_monatlich": "true"})
    # Ohne Energiedaten gibt es keine Rechnung, sondern einen Hinweis
    lauf = {"von": "2026-06-01", "bis": "2026-06-30"}
    assert "fehlen Energiedaten (Jun 26)" in c.post(f"/verwaltung/gruppe/{gid}/abrechnungslauf", auth=auth, data=lauf).text

    with Session(engine) as s:
        e, a = s.get(Teilnehmer, 1), s.get(Teilnehmer, 2)
        zeiten = ed.viertelstunden(date(2026, 6, 1), date(2026, 6, 30))
        spuren = ed.simulieren(e, [a], zeiten)
    datei = ed.export_schreiben(spuren, zeiten, "AT-TEST")
    seite = c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("juni.xlsx", datei))]).text
    assert "5.760" in seite  # 30 Tage × 96 Viertelstunden für zwei Zählpunkte
    # Nochmal hochladen ersetzt statt doppelt zu zählen
    c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("juni.xlsx", datei))])
    gruppe = c.get(f"/verwaltung/gruppe/{gid}", auth=auth).text
    assert 'value="2026-06-01"' in gruppe and "Tagesverlauf" in gruppe

    assert "1 Rechnung(en) erstellt" in c.post(f"/verwaltung/gruppe/{gid}/abrechnungslauf", auth=auth, data=lauf).text
    with Session(engine) as s:
        r = s.exec(select(Rechnung)).one()
    erwartet = sum(spuren[1].werte["eigendeckung"])
    assert r.kwh == pytest.approx(erwartet, abs=0.05) and r.brutto == pytest.approx(round(erwartet * 0.1, 2), abs=0.01)
    assert 'value="2026-07-01"' in c.get(f"/verwaltung/gruppe/{gid}", auth=auth).text  # nächster Vorschlag

    # Zahlungsstatus und Zahlungserinnerung
    assert "überfällig" in c.get("/verwaltung/rechnungen", auth=auth).text or "offen" in c.get(
        "/verwaltung/rechnungen", auth=auth).text
    c.post(f"/verwaltung/rechnung/{r.id}/mahnung", auth=auth)
    assert "Zahlungserinnerung" in c.get("/verwaltung/postausgang", auth=auth).text
    c.post(f"/verwaltung/rechnung/{r.id}/bezahlt", auth=auth, data={"zurueck": "/verwaltung/rechnungen"})
    with Session(engine) as s:
        assert s.get(Rechnung, r.id).bezahlt_am == date.today()
    assert "Sonnenstrom vom Nachbarn" in c.get(re.search(r"(/mein/2/\w+)", c.get(
        "/verwaltung/teilnehmer", auth=auth).text).group(1)).text


def test_testbetrieb_anlegen(monkeypatch, tmp_path):
    import demo_daten
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setattr(demo_daten, "engine", engine)
    monkeypatch.setattr(demo_daten.config, "TESTDATEN", tmp_path / "testdaten")
    monkeypatch.setattr(demo_daten, "JAHR_VON", date(2026, 8, 1))
    demo_daten.anlegen(neu=True)
    assert sorted(p.name for p in (tmp_path / "testdaten").glob("*.xlsx")) == [
        "EDA_Export_GEA_Bach_2026-08.xlsx", "EDA_Export_GEA_Bach_2026-09.xlsx",
        "EDA_Export_GEA_Sonnenhof_2026-08.xlsx", "EDA_Export_GEA_Sonnenhof_2026-09.xlsx",
        "EDA_Export_Gruppe1_2026-08.xlsx", "EDA_Export_Gruppe1_2026-09.xlsx",
        "EDA_Export_Gruppe2_2026-08.xlsx", "EDA_Export_Gruppe2_2026-09.xlsx"]
    with Session(engine) as s:
        # August: „Haus am Bach“ vier Wohnungen. P2P rechnen die Nachbarn selbst ab, Gruppe 1 hat
        # nur die Einrichtung bezahlt
        assert len(s.exec(select(Rechnung)).all()) == 4
        gebuehren = s.exec(select(main.Gebuehr)).all()
        assert sorted(x.art for x in gebuehren) == ["einrichtung", "kwh", "kwh"]  # Gruppe 1, Sonnenhof, Bach
        assert all(x.bezahlt_am for x in gebuehren if x.art == "einrichtung")


def test_testbetrieb_laufend(monkeypatch, tmp_path, laufend):
    import demo_daten
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setattr(demo_daten, "engine", engine)
    monkeypatch.setattr(demo_daten.config, "TESTDATEN", tmp_path / "testdaten")
    monkeypatch.setattr(demo_daten, "JAHR_VON", date(2026, 8, 1))
    demo_daten.anlegen(neu=True)
    with Session(engine) as s:
        # August: Gruppe 1 drei Abnehmer, „Haus am Bach“ vier Wohnungen
        assert len(s.exec(select(Rechnung)).all()) == 3 + 4
        assert len(s.exec(select(main.Gebuehr)).all()) == 3  # Gruppe 1, Sonnenhof, Haus am Bach
        assert not s.exec(select(main.Messwert).where(main.Messwert.beginn >= "2026-09")).first()


# ---------------------------------------------------------------- Import echter Exporte

def _xlsx(zeilen, verbunden=()):
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for z in zeilen:
        ws.append(z)
    for bereich in verbunden:
        ws.merge_cells(bereich)
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


GID = "AT00810000000000000000000TEST0001"


def test_import_verbundene_zellen_qualitaet_und_wh():
    from datetime import datetime
    from app import eda_import as ei
    datei = _xlsx([
        ["Gemeinschafts-ID", GID], [],
        ["MeteringpointID", None, zp(1), None, None, zp(2), None, None],
        ["Energierichtung", None, "CONSUMPTION", None, None, "GENERATION", None, None],
        ["Beginn", "Ende", "Gesamtverbrauch lt. Messung (bei Teilnahme gem. Erzeugung) [KWH]", "Qualität",
         "Eigendeckung gemeinschaftliche Erzeugung [KWH]", "Gesamte gemeinschaftliche Erzeugung [Wh]", "Qualität",
         "Summe"],
        [datetime(2026, 10, 1, 0, 0), datetime(2026, 10, 1, 0, 15), 0.5, "L1", 0.1, 400, "L1", 9],
        [datetime(2026, 10, 1, 0, 15), datetime(2026, 10, 1, 0, 30), 0.7, "L1", 0.2, 600, "L1", 9],
    ], verbunden=["C3:E3", "F3:G3"])
    imp = ei.einlesen(datei)
    assert imp.zaehlpunkte == {zp(1), zp(2)} and imp.erzeuger == {zp(2)}
    assert imp.gemeinschafts_id == GID and not imp.unbekannt and imp.intervall == 15
    summe = {f: round(sum(w[f] for w in imp.werte), 3) for f in ("verbrauch", "eigendeckung", "erzeugung")}
    assert summe == {"verbrauch": 1.2, "eigendeckung": 0.3, "erzeugung": 1.0}  # Wh in kWh umgerechnet


def test_import_lange_csv_mit_codes_und_zeitzone():
    from app import eda_import as ei
    csv = (f"Zählpunkt;Beginn;Ende;Metercode;Wert\n"
           f"{zp(1)};2026-10-01T00:00:00Z;2026-10-01T00:15:00Z;1-1:2.9.0 G.03;0,25\n"
           f"{zp(1)};2026-10-01T00:00:00Z;2026-10-01T00:15:00Z;Gesamtverbrauch;1,5\n").encode()
    imp = ei.einlesen(csv, dateiname="x.csv")
    assert list(imp.unbekannt) == ["1-1:2.9.0 G.03"] and imp.unbekannt["1-1:2.9.0 G.03"] == ["0,25"]
    assert imp.werte[0]["beginn"] == "2026-10-01 02:00"  # UTC in Ortszeit (Sommerzeit)
    imp = ei.einlesen(csv, {ei.schluessel("1-1:2.9.0 G.03"): "eigendeckung"})
    assert not imp.unbekannt and imp.werte[0]["eigendeckung"] == 0.25 and imp.werte[0]["verbrauch"] == 1.5


def test_import_datum_und_uhrzeit_getrennt_zp_im_titel():
    from datetime import datetime, time
    from app import eda_import as ei
    imp = ei.einlesen(_xlsx([
        ["Datum", "Uhrzeit", f"{zp(1)} Eigendeckung [kWh]", f"{zp(2)} Erzeugung [kWh]"],
        [datetime(2026, 10, 1), time(0, 0), 0.1, 2],
        ["01.10.2026", "00:15", "0,2", "3"]]))
    assert sorted({w["beginn"] for w in imp.werte}) == ["2026-10-01 00:00", "2026-10-01 00:15"]
    assert round(sum(w["eigendeckung"] for w in imp.werte), 3) == 0.3


def test_aufbau_bericht_ohne_nummern_und_namen():
    from app import eda_import as ei
    bericht = ei.aufbau_bericht(_xlsx([["Name", "Max Mustermann"], ["MeteringpointID", zp(1)],
                                       ["Zeit", "Eigendeckung"], ["01.10.2026 00:00", 1]]))
    assert zp(1) not in bericht and "Mustermann" not in bericht
    assert "[Nummer 1]" in bericht and "Eigendeckung" in bericht
    with pytest.raises(ValueError):
        ei.einlesen(b"\x00\x01 keine Datei")


# ---------------------------------------------------------------- GEA

def _gea_export(von, bis, anlage=20, wohnungen=(21, 22, 23), gid=GID, seed=1):
    """Fiktiver Export im Aufbau des Testbetriebs."""
    from app import energiedaten as ed
    b = _t(anlage, Rolle.ERZEUGER, kwh=0, kwp=20)
    ab = [_t(n, Rolle.ABNEHMER, Profiltyp.HAUSHALT, 3000) for n in wohnungen]
    zeiten = ed.viertelstunden(von, bis)
    spuren = ed.simulieren(b, ab, zeiten, seed=seed)
    return ed.export_schreiben(spuren, zeiten, gid), spuren


def _gea_anlegen(c, datei, **extra):
    daten = {"name": "Testhaus", "vorname": "Bea", "nachname": "Betreiber", "email": "b@example.com",
             "modell": "selbst", "aufteilung": "dynamisch", "preis_ct_kwh": "12", "gebuehr_ct_kwh": "1",
             "status": "aktiv", "rechnung_monatlich": "true", **extra}
    return c.post("/verwaltung/gea/neu", data=daten, files={"datei": ("export.xlsx", datei)},
                  auth=("admin", "test"), follow_redirects=False)


def test_gea_aus_export_anlegen_und_betreiber_laedt_hoch(client):
    from openpyxl import load_workbook
    import io
    c, engine = client
    auth = ("admin", "test")
    datei, spuren = _gea_export(date(2026, 6, 1), date(2026, 6, 30))
    r = _gea_anlegen(c, datei)
    assert r.status_code == 303 and r.headers["location"].startswith("/verwaltung/gea/")
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    with Session(engine) as s:
        g = s.get(Gruppe, gid)
        betreiber = s.get(Teilnehmer, g.erzeuger_id)
        assert g.art.value == "gea" and g.gemeinschafts_id == GID and betreiber.zaehlpunkt == zp(20)
        assert len(main.importe.mitglieder(s, g)) == 3
    seite = c.get(f"/verwaltung/gea/{gid}", auth=auth).text
    assert "Wohnung 1" in seite and "Im Haus genutzt je Wohnung" in seite
    bereich = re.search(r"http://localhost:8000(/mein/\d+/\w+)", seite).group(1)
    assert "Monatlichen EDA-Export hochladen" in c.get(bereich).text

    # Betreiber lädt den Juli selbst hoch, die Zahlen stehen sofort in seinem Bereich
    juli, _ = _gea_export(date(2026, 7, 1), date(2026, 7, 31), seed=2)
    antwort = c.post(f"{bereich}/energiedaten", files=[("dateien", ("juli.xlsx", juli))]).text
    assert "übernommen" in antwort and "Jul 26" in antwort
    # Datei einer fremden Gemeinschaft wird abgelehnt
    fremd, _ = _gea_export(date(2026, 7, 1), date(2026, 7, 2), anlage=30, wohnungen=(31,), gid="AT00810000000000000000000ANDERE01")
    assert "gehört nicht zu Ihrer GEA" in c.post(f"{bereich}/energiedaten", files=[("dateien", ("f.xlsx", fremd))]).text

    # Abrechnungsgrundlage als Excel, Summe = Eigendeckung aller Wohnungen im Juni
    x = c.get(f"{bereich}/grundlage", params={"von": "2026-06-01", "bis": "2026-06-30"})
    ws = load_workbook(io.BytesIO(x.content))["Abrechnung"]
    summe = next(z for z in ws.iter_rows(values_only=True) if z[0] == "Summe")
    erwartet = sum(sum(sp.werte["eigendeckung"]) for sp in spuren[1:])
    assert summe[5] == pytest.approx(erwartet, abs=0.05)
    assert summe[8] == pytest.approx(round(erwartet * 0.12, 2), abs=0.05)

    # Selbst abrechnende GEA: der Lauf erzeugt nur die Servicegebühr von FIX
    seite = c.post(f"/verwaltung/gruppe/{gid}/abrechnungslauf", auth=auth,
                   data={"von": "2026-06-01", "bis": "2026-06-30"}).text
    assert "Servicegebühr" in seite
    with Session(engine) as s:
        assert not s.exec(select(Rechnung)).all()
        gebuehr = s.exec(select(main.Gebuehr)).one()
    assert gebuehr.kwh == pytest.approx(erwartet, abs=0.05)
    assert gebuehr.netto == pytest.approx(round(erwartet * 0.01, 2), abs=0.01) and gebuehr.ust_satz == 0.2
    assert c.get(f"{bereich}/gebuehr/{gebuehr.id}").status_code == 200
    assert "FIX-" in c.get("/verwaltung/rechnungen", auth=auth).text


def test_gea_spalten_einmal_zuordnen(client):
    c, engine = client
    auth = ("admin", "test")
    zeilen = "Zählpunkt;Beginn;Metercode;Wert\n" + "".join(
        f"{zp(n)};01.10.2026 12:{m:02d};{code};{wert}\n" for m in (0, 15) for n, code, wert in (
            (40, "P.01", "2,0"), (41, "G.01", "1,0"), (41, "G.03", "0,8"), (42, "G.01", "0,5"), (42, "G.03", "0,5")))
    r = c.post("/verwaltung/gea/neu", data={"name": "Codes", "modell": "selbst", "aufteilung": "dynamisch"},
               files={"datei": ("codes.csv", zeilen.encode())}, auth=auth, follow_redirects=False)
    assert r.headers["location"].startswith("/verwaltung/import/")
    zuordnen = c.get(r.headers["location"], auth=auth).text
    assert "P.01" in zuordnen and "kennt die Plattform noch nicht" in zuordnen
    titel = re.findall(r"<td><b>(.*?)</b></td>", zuordnen)
    wahl = {"P.01": "erzeugung", "G.01": "verbrauch", "G.03": "eigendeckung"}
    r = c.post(r.headers["location"], auth=auth, follow_redirects=False,
               data={f"feld_{i}": wahl[t] for i, t in enumerate(titel)})
    assert r.headers["location"].startswith("/verwaltung/gea/")
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    with Session(engine) as s:
        g = s.get(Gruppe, gid)
        assert s.get(Teilnehmer, g.erzeuger_id).zaehlpunkt == zp(40)
        werte = main.auswertung.summen(s, [zp(41), zp(42)], date(2026, 10, 1), date(2026, 10, 1), gid)
    assert werte[zp(41)]["eigendeckung"] == pytest.approx(1.6) and werte[zp(42)]["verbrauch"] == pytest.approx(1.0)
    # Beim nächsten Export mit denselben Spalten fragt die Plattform nicht mehr
    seite = c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("codes2.csv", zeilen.encode()))]).text
    assert "importiert" in seite and "Codes" in seite


def test_werte_je_gemeinschaft_getrennt(client):
    """Eine Wohnung ist in einer GEA und zusätzlich in einer P2P-Gruppe."""
    c, engine = client
    auth = ("admin", "test")
    gea_datei, gea_spuren = _gea_export(date(2026, 6, 1), date(2026, 6, 2))
    gid_gea = int(_gea_anlegen(c, gea_datei).headers["location"].rsplit("/", 1)[1])
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    with Session(engine) as s:  # Wohnung 21 nimmt zusätzlich an der P2P-Gruppe teil
        g = Gruppe(erzeuger_id=1, status=GruppenStatus.AKTIV, gemeinschafts_id="AT00810000000000000000000P2P00001")
        s.add(g)
        s.commit()
        s.add(main.Mitglied(gruppe_id=g.id, teilnehmer_id=s.exec(select(Teilnehmer).where(
            Teilnehmer.zaehlpunkt == zp(21))).one().id))
        s.commit()
        gid_p2p = g.id
        e, w = s.get(Teilnehmer, 1), s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == zp(21))).one()
        from app import energiedaten as ed
        zeiten = ed.viertelstunden(date(2026, 6, 1), date(2026, 6, 2))
        p2p_spuren = ed.simulieren(e, [w], zeiten, seed=9)
    p2p_datei = ed.export_schreiben(p2p_spuren, zeiten, "AT00810000000000000000000P2P00001")
    seite = c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("p2p.xlsx", p2p_datei))]).text
    assert "über die Gemeinschafts-ID" in seite
    with Session(engine) as s:
        in_gea = main.auswertung.summen(s, [zp(21)], date(2026, 6, 1), date(2026, 6, 2), gid_gea)[zp(21)]
        in_p2p = main.auswertung.summen(s, [zp(21)], date(2026, 6, 1), date(2026, 6, 2), gid_p2p)[zp(21)]
    assert in_gea["eigendeckung"] == pytest.approx(sum(gea_spuren[1].werte["eigendeckung"]), abs=0.01)
    assert in_p2p["eigendeckung"] == pytest.approx(sum(p2p_spuren[1].werte["eigendeckung"]), abs=0.01)


def test_alte_werte_ohne_gemeinschaft_werden_zugeordnet(client):
    c, engine = client
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    with Session(engine) as s:
        g = Gruppe(erzeuger_id=1, status=GruppenStatus.AKTIV)
        s.add(g)
        s.commit()
        s.add(main.Mitglied(gruppe_id=g.id, teilnehmer_id=2))
        s.add(main.Messwert(zaehlpunkt=zp(2), beginn="2026-06-01 12:00", eigendeckung=1.0))
        s.commit()
        gid = g.id
    main.importe.alte_werte_zuordnen(engine)
    with Session(engine) as s:
        assert s.exec(select(main.Messwert)).one().gruppe_id == gid


def test_servicegebuehr_p2p(client, laufend):
    from app import energiedaten as ed
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    r = c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth, follow_redirects=False)
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    c.post(f"/verwaltung/gruppe/{gid}", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 10, "ust_satz": 0,
                                                         "rechnung_monatlich": "true", "gebuehr_ct_kwh": 1.5})
    with Session(engine) as s:
        zeiten = ed.viertelstunden(date(2026, 6, 1), date(2026, 6, 30))
        spuren = ed.simulieren(s.get(Teilnehmer, 1), [s.get(Teilnehmer, 2)], zeiten)
    c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("juni.xlsx", ed.export_schreiben(spuren, zeiten, "X")))])
    seite = c.post(f"/verwaltung/gruppe/{gid}/abrechnungslauf", auth=auth, data={"von": "2026-06-01", "bis": "2026-06-30"}).text
    assert "1 Rechnung(en) erstellt" in seite and "Servicegebühr" in seite
    with Session(engine) as s:
        x = s.exec(select(main.Gebuehr)).one()
        r = s.exec(select(Rechnung)).one()
    assert x.kwh == pytest.approx(r.kwh, abs=0.01) and x.netto == pytest.approx(round(r.kwh * 0.015, 2), abs=0.01)
    assert x.empfaenger_id == 1 and x.brutto == pytest.approx(round(x.netto * 1.2, 2), abs=0.01)
    assert "Servicegebühr" in c.get("/verwaltung/postausgang", auth=auth).text


# ---------------------------------------------------------------- Anfragen und Wunschnachbar

def test_anfragen_und_gea_aus_anfrage(client):
    c, engine = client
    auth = ("admin", "test")
    daten = {"vorname": "Nora", "nachname": "Test", "email": "n@example.com", "strasse": "Weg 1", "plz": "6800",
             "ort": "Feldkirch", "wohnungen": 6, "anlage_kwp": 15, "registriert": "nein", "modell": "verwaltet"}
    assert "Bitte bestätigen" in c.post("/gea", data=daten).text
    assert "Danke, Nora" in c.post("/gea", data={**daten, "einwilligung": "true"}).text
    assert "Danke, Otto" in c.post("/anfrage", data={"vorname": "Otto", "nachname": "X", "email": "o@example.com",
                                                    "nachricht": "Frage?", "einwilligung": "true"}).text
    seite = c.get("/verwaltung/anfragen", auth=auth).text
    assert "GEA-Anfrage" in seite and "Frage?" in seite
    aid = int(re.search(r"/verwaltung/gea/neu\?anfrage=(\d+)", seite).group(1))
    formular = c.get(f"/verwaltung/gea/neu?anfrage={aid}", auth=auth).text
    assert 'value="n@example.com"' in formular and "GEA Weg 1, Feldkirch" in formular
    r = c.post("/verwaltung/gea/neu", auth=auth, follow_redirects=False,
               data={"anfrage_id": aid, "name": "GEA Weg 1", "email": "n@example.com", "modell": "verwaltet",
                     "aufteilung": "dynamisch", "status": "vorschlag"})
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    with Session(engine) as s:
        a = s.get(main.Anfrage, aid)
        assert a.gruppe_id == gid and a.status == "in_bearbeitung"
    assert "noch nicht eingetragen" in c.get(f"/verwaltung/gea/{gid}", auth=auth).text or \
        "Zählpunkt der Anlage ist noch nicht eingetragen" in c.get(f"/verwaltung/gea/{gid}", auth=auth).text


def test_abnehmer_laedt_wunschnachbarn_ein(client):
    c, engine = client
    r = _anmelden(c, "abnehmer", 2, wunschpartner="Herr Sonne, Weg 3")
    danke = c.get(r.headers["location"]).text
    token = re.search(r"einladung=([\w-]+)", danke).group(1)
    formular = c.get(f"/anmelden?einladung={token}").text
    assert 'name="rolle" value="erzeuger"' in formular  # Eingeladener ist der Erzeuger
    _anmelden(c, "erzeuger", 1, einladung=token, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 3)
    seite = c.get("/verwaltung/matching", auth=("admin", "test")).text
    ids = re.search(r'name="abnehmer_ids" value="([\d,]+)"', seite).group(1)
    # Teilnehmer 1 ist der einladende Abnehmer, er steht vor dem fremden Abnehmer 3
    assert ids.split(",")[0] == "1" and "eingeladen" in seite and "Herr Sonne" in seite


# ---------------------------------------------------------------- EDA-Energiedatenreport (Monatssummen)

EC_ID = "ATCC0000TESTGEA000000000000000001"  # erfunden, Aufbau wie im Bericht: Buchstaben nach AT


def _energiedatenreport(monate=((2026, 10),), wohnungen=(51, 52), anlage=50, unvollstaendig=False):
    """Nachbau des EDA-Energiedatenreports mit erfundenen Zählpunkten und Namen."""
    import calendar
    import io
    from openpyxl import Workbook
    wb = Workbook()
    gesamt = wb.active
    gesamt.title = "Gesamtübersicht"
    detail = wb.create_sheet("Detailübersicht")
    erster, letzter = monate[0], monate[-1]
    von = f"01.{erster[1]:02d}.{erster[0]}"
    bis = f"{calendar.monthrange(*letzter)[1]:02d}.{letzter[1]:02d}.{letzter[0]}"
    for ws in (gesamt, detail):
        ws.append(["EC-ID", None, "Bezeichnung"])
        ws.append([EC_ID, None, "TEST01"])
        ws.append([None, None, None, None, "Unvollständige Daten? Erklärungen zum Energiedatenreport finden Sie hier."])
        ws.append(["Auswertungszeitraum von", von])
        ws.append(["Auswertungszeitraum bis", bis])
        ws.merge_cells("A2:B2")
    gesamt.append(["Zählpunktbezeichnung", "Zählpunktnummer", "Energierichtung", "Zeitraum",
                   "Verbrauch, abrechnungsrelevante Energiemenge", "Erzeugung, abrechnungsrelevante Energiemenge",
                   "Status Datenübermittlung", "Datenqualität", "Vorname", "Nachname", "Straße", "Hausnr.", "PLZ", "Stadt"])
    gesamt.append(["Bezeichnung so wie im EDA Portal", "Eindeutige Identifikationsnummer des Zählpunkts"])
    detail.append(["Zählpunktbezeichnung", "Zählpunktnummer", "Energierichtung", "Jahr", "Monat", "Zeitraum von",
                   "Zeitraum bis", "Gesamtverbrauch lt. Messung (bei Teilnahme gem. Erzeugung) [KWH]",
                   "Verbrauch lt. Messung entsprechend dem Teilnahmefaktor je ZP und EC-ID [KWH]",
                   "Anteil gemeinschaftliche Erzeugung [KWH]", "Eigendeckung gemeinschaftliche Erzeugung [KWH]",
                   "Eigendeckung aus erneuerbarer Energie [KWH]", "Gesamte gemeinschaftliche Erzeugung [KWH]",
                   "Erzeugung lt. Messung entsprechend dem Teilnahmefaktor und EC-ID [KWH]",
                   "Gesamt/Überschusserzeugung, Gemeinschaftsüberschuss [KWH]", "Restüberschuss bei EG und je ZP [KWH]",
                   "Datenübermittlung", "Datenqualität"])
    detail.append([None, None, None, None, None, None, None, "Summe der Energiedaten"])
    detail.append([None, None, None, None, None, von, bis, 999, 999, 999, 999, 999, 999, 999, None, 999, "Vollständig", "L1"])
    detail.append([None, None, None, None, None, None, None, "Energiedaten je Zählpunkt"])
    erwartet = {}
    for j, m in monate:
        tage = calendar.monthrange(j, m)[1]
        a, b = f"01.{m:02d}.{j}", f"{tage:02d}.{m:02d}.{j}"
        summe_eigen = 0
        for i, n in enumerate(wohnungen):
            verbrauch, eigen = 200 + 10 * i + m, 40 + 5 * i + m / 10
            summe_eigen += eigen
            erwartet[(zp(n), m)] = eigen
            status = "Unvollständig" if unvollstaendig and i == 0 else "Vollständig"
            # Teilnahmefaktor-Spalte und „erneuerbar“ absichtlich anders, sie dürfen nicht mitzählen
            detail.append([f"Top {i + 1}", zp(n), "VERBRAUCH", j, m, a, b, verbrauch, verbrauch / 2, 400, eigen,
                           eigen / 2, None, None, None, None, status, "L1"])
        detail.append(["PV Dach", zp(anlage), "ERZEUGUNG", j, m, a, b, None, None, None, None, None, 400, 380, 5,
                       400 - summe_eigen, "Vollständig", "L1"])
    for i, n in enumerate(wohnungen):
        gesamt.append([f"Top {i + 1}", zp(n), "VERBRAUCH", f"{von}-{bis}", 50.5, None, "Vollständig", "L1",
                       f"Vor{i}", f"Nach{i}", "Testgasse", str(i + 1), "6800", "Teststadt"])
    gesamt.append(["PV Dach", zp(anlage), "ERZEUGUNG", f"{von}-{bis}", None, 400.0, "Vollständig", "L1",
                   "Bea", "Betreiberin", "Testgasse", "9", "6800", "Teststadt"])
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue(), erwartet


def test_energiedatenreport_einlesen():
    from app import eda_import as ei
    datei, erwartet = _energiedatenreport(monate=((2026, 9), (2026, 10)), unvollstaendig=True)
    imp = ei.einlesen(datei)
    assert imp.summen and imp.intervall is None and not imp.unbekannt
    assert imp.gemeinschafts_id == EC_ID  # steht unter der Beschriftung „EC-ID“
    assert imp.zaehlpunkte == {zp(50), zp(51), zp(52)} and imp.erzeuger == {zp(50)}
    assert len(imp.werte) == 6 and all(w["ende"] for w in imp.werte)  # Summenzeile ohne Zählpunkt nicht dabei
    okt = {w["zaehlpunkt"]: w for w in imp.werte if w["beginn"] == "2026-10-01 00:00"}
    assert okt[zp(51)]["ende"] == "2026-11-01 00:00"
    assert okt[zp(51)]["verbrauch"] == 210 and okt[zp(51)]["eigendeckung"] == pytest.approx(erwartet[(zp(51), 10)])
    assert okt[zp(50)]["erzeugung"] == 400 and okt[zp(50)]["restueberschuss"] == pytest.approx(
        400 - erwartet[(zp(51), 10)] - erwartet[(zp(52), 10)])
    assert imp.von == datetime(2026, 9, 1) and imp.bis == datetime(2026, 10, 31, 23, 45)
    assert imp.stammdaten[zp(51)] == {"wohnung": "Top 1", "vorname": "Vor0", "nachname": "Nach0",
                                      "strasse": "Testgasse 1", "plz": "6800", "ort": "Teststadt"}
    assert any("unvollständige Daten" in h for h in imp.hinweise)
    assert any("keine Viertelstunden" in h for h in imp.hinweise)
    bericht = ei.aufbau_bericht(datei)
    assert EC_ID not in bericht and zp(51) not in bericht and "Vor0" not in bericht


def test_energiedatenreport_nur_gesamtuebersicht():
    import io
    from openpyxl import load_workbook
    from app import eda_import as ei
    datei, _ = _energiedatenreport()
    wb = load_workbook(io.BytesIO(datei))
    del wb["Detailübersicht"]
    puffer = io.BytesIO()
    wb.save(puffer)
    imp = ei.einlesen(puffer.getvalue())
    werte = {w["zaehlpunkt"]: w for w in imp.werte}
    assert werte[zp(51)]["eigendeckung"] == 50.5 and werte[zp(50)]["erzeugung"] == 400
    assert werte[zp(51)]["beginn"] == "2026-10-01 00:00" and werte[zp(51)]["ende"] == "2026-11-01 00:00"


def test_gea_aus_energiedatenreport(client):
    from app import auswertung as aw
    c, engine = client
    auth = ("admin", "test")
    datei, erwartet = _energiedatenreport(monate=((2026, 9), (2026, 10)))
    r = _gea_anlegen(c, datei, vorname="", nachname="", email="")
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    with Session(engine) as s:
        g = s.get(Gruppe, gid)
        betreiber = s.get(Teilnehmer, g.erzeuger_id)
        assert g.gemeinschafts_id == EC_ID and betreiber.zaehlpunkt == zp(50)
        assert betreiber.nachname == "Betreiberin" and betreiber.strasse == "Testgasse 9" and not betreiber.wohnung
        wohnungen = sorted(t.name for _, t in main.importe.mitglieder(s, g))
        assert wohnungen == ["Top 1, Vor0 Nach0", "Top 2, Vor1 Nach1"]
        assert aw.letzter_tag(s, [zp(51)], gid) == date(2026, 10, 31)
        assert aw.abdeckung(s)[(gid, zp(51))] == {"2026-09": 30, "2026-10": 31}
        assert not aw.hat_viertelstunden(s, [zp(50), zp(51)], gid)
    seite = c.get(f"/verwaltung/gea/{gid}", auth=auth).text
    assert "Monatssummen aus dem EDA-Energiedatenreport" in seite and "Okt 26" in seite
    # Abrechnung Oktober: nur die Servicegebühr, Menge = Eigendeckung beider Wohnungen im Oktober
    seite = c.post(f"/verwaltung/gruppe/{gid}/abrechnungslauf", auth=auth,
                   data={"von": "2026-10-01", "bis": "2026-10-31"}).text
    assert "fehlen" not in seite
    with Session(engine) as s:
        gebuehr = s.exec(select(main.Gebuehr)).one()
    assert gebuehr.kwh == pytest.approx(erwartet[(zp(51), 10)] + erwartet[(zp(52), 10)])
    # Derselbe Bericht nochmal über Energiedaten: wird erkannt, ersetzt, nichts doppelt
    seite = c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("bericht.xlsx", datei))]).text
    assert "über die Gemeinschafts-ID" in seite
    with Session(engine) as s:
        assert len(s.exec(select(main.Messwert)).all()) == 6
        # Viertelstundenwerte für Oktober ersetzen die Monatssumme
        from app import energiedaten as ed
        zeiten = ed.viertelstunden(date(2026, 10, 1), date(2026, 10, 31))
        spuren = ed.simulieren(s.get(Teilnehmer, g.erzeuger_id), [s.get(Teilnehmer, t.id) for _, t in
                                                                  main.importe.mitglieder(s, g)], zeiten)
    c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("okt.xlsx", ed.export_schreiben(spuren, zeiten, EC_ID)))])
    with Session(engine) as s:
        okt = s.exec(select(main.Messwert).where(main.Messwert.beginn >= "2026-10-01")).all()
        assert okt and all(m.ende is None for m in okt)
        assert aw.hat_viertelstunden(s, [zp(51)], gid)


# ---------------------------------------------------------------- Bezug und Einspeisung, P2P-Übersicht

def test_anmeldung_fragt_bezug_und_einspeisung(client):
    c, engine = client
    erzeuger = c.get("/anmelden?rolle=erzeuger").text
    assert "Zählpunktnummer Einspeisung" in erzeuger and 'name="zaehlpunkt_bezug"' in erzeuger
    abnehmer = c.get("/anmelden?rolle=abnehmer").text
    assert "Zählpunktnummer Bezug" in abnehmer and 'name="zaehlpunkt_einspeisung"' in abnehmer
    # gleiche Nummer für Bezug und Einspeisung wird abgelehnt, falsche Nummer auch
    r = _anmelden(c, "erzeuger", 1, anlage_kwp=10, zaehlpunkt_bezug=zp(1))
    assert r.status_code == 200 and "verschiedene Zählpunktnummern" in r.text
    r = _anmelden(c, "erzeuger", 1, anlage_kwp=10, zaehlpunkt_bezug="AT123")
    assert "Bezug stimmt nicht" in r.text
    assert _anmelden(c, "erzeuger", 1, anlage_kwp=10, zaehlpunkt_bezug=zp(101)).status_code == 303
    assert _anmelden(c, "abnehmer", 2, zaehlpunkt_einspeisung=zp(102)).status_code == 303
    assert _anmelden(c, "abnehmer", 3).status_code == 303
    with Session(engine) as s:
        e, a, b = (s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == zp(n))).one() for n in (1, 2, 3))
    assert (e.einspeise_zp, e.bezug_zp) == (zp(1), zp(101))
    assert (a.bezug_zp, a.einspeise_zp) == (zp(2), zp(102))
    assert (b.bezug_zp, b.einspeise_zp) == (zp(3), None)
    liste = c.get("/verwaltung/teilnehmer", auth=("admin", "test")).text
    assert "Bezug" in liste and "Einspeisung" in liste


def test_gea_anfrage_mit_zaehlpunkt_der_anlage(client):
    c, engine = client
    daten = {"vorname": "Nora", "nachname": "Test", "email": "n@example.com", "strasse": "Weg 1", "plz": "6800",
             "ort": "Feldkirch", "einwilligung": "true"}
    assert "stimmt nicht" in c.post("/gea", data={**daten, "anlage_zp": "AT1"}).text
    assert "Danke, Nora" in c.post("/gea", data={**daten, "anlage_zp": zp(77)}).text
    with Session(engine) as s:
        aid = s.exec(select(main.Anfrage)).one().id
    formular = c.get(f"/verwaltung/gea/neu?anfrage={aid}", auth=("admin", "test")).text
    assert f'value="{zp(77)}"' in formular


def test_p2p_uebersicht(client):
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    _anmelden(c, "abnehmer", 3, wunschpartner="Familie Sonne")
    c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth)
    seite = c.get("/verwaltung/p2p", auth=auth).text
    assert "Peer-to-Peer" in seite and 'href="/verwaltung/p2p" class="aktiv"' in seite
    gruppen, frei = seite.split('id="frei"')
    assert "/verwaltung/gruppe/1" in gruppen and "nach der Unterschrift" in gruppen
    assert "Familie Sonne" in frei and "1 noch ohne Gruppe" in gruppen


def test_passwort_nur_als_hash():
    from app import sicherheit
    h = sicherheit.passwort_hash("richtig-und-lang", iterationen=1000)
    assert h.startswith("pbkdf2_sha256:1000:") and "richtig" not in h and "$" not in h
    assert sicherheit.passwort_pruefen("richtig-und-lang", h)
    assert sicherheit.passwort_pruefen("richtig-und-lang", h)  # zweites Mal aus dem Zwischenspeicher
    assert not sicherheit.passwort_pruefen("falsch", h)
    assert not sicherheit.passwort_pruefen("richtig-und-lang", "kaputt")
    assert not sicherheit.passwort_pruefen("richtig-und-lang", "md5:1:aa:bb")


def test_verwaltung_mit_hash_und_sperre(client, monkeypatch):
    from app import config, sicherheit
    c, _ = client
    monkeypatch.setattr(config, "ADMIN_PASSWORT", "")
    monkeypatch.setattr(config, "ADMIN_PASSWORT_HASH", sicherheit.passwort_hash("geheim-1234567", iterationen=1000))
    monkeypatch.setattr(main, "sperre", sicherheit.Sperre(versuche=3, fenster=900))
    assert c.get("/verwaltung", auth=("admin", "geheim-1234567")).status_code == 200
    assert c.get("/verwaltung", auth=("admin", "test")).status_code == 401  # Klartext gilt nicht mehr
    assert c.get("/verwaltung", auth=("jemand", "geheim-1234567")).status_code == 401
    assert c.get("/verwaltung", auth=("admin", "falsch")).status_code == 401
    r = c.get("/verwaltung", auth=("admin", "geheim-1234567"))
    assert r.status_code == 429 and "15 Minuten" in r.text
    main.sperre.erfolg("testclient")
    assert c.get("/verwaltung", auth=("admin", "geheim-1234567")).status_code == 200


def test_sicherheits_kopfzeilen_und_rechtsseiten(client, monkeypatch):
    from app import config
    c, _ = client
    start = c.get("/")
    assert start.headers["x-content-type-options"] == "nosniff"
    assert start.headers["x-frame-options"] == "DENY"
    assert start.headers["referrer-policy"] == "same-origin"
    assert "x-robots-tag" not in start.headers
    assert "googleapis" not in start.text and "/static/schrift/rubik-latin-400-normal.woff2" in start.text
    assert 'href="/impressum"' in start.text and 'href="/datenschutz"' in start.text
    assert c.get("/static/schrift/rubik-latin-700-normal.woff2").status_code == 200
    assert "noindex" in c.get("/verwaltung").headers["x-robots-tag"]
    assert "noindex" in c.get("/mein/irgendwas").headers["x-robots-tag"]
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    for seite in ("/impressum", "/datenschutz"):
        r = c.get(seite)
        assert r.status_code == 200 and "Entwurf" in r.text and config.DIENSTLEISTER["firma"] in r.text
    monkeypatch.setattr(config, "RECHTSTEXTE_GEPRUEFT", True)
    assert "Entwurf" not in c.get("/datenschutz").text
    assert "set-cookie" not in start.headers


def test_adresse_hinter_tunnel():
    from starlette.requests import Request

    def anfrage(host, kopf=None):
        return Request({"type": "http", "client": (host, 1), "headers": [(k.encode(), v.encode()) for k, v in (kopf or {}).items()]})

    assert main._adresse(anfrage("127.0.0.1", {"cf-connecting-ip": "198.51.100.4"})) == "198.51.100.4"
    assert main._adresse(anfrage("127.0.0.1")) == "127.0.0.1"
    # Von außen (Adresse schon aus X-Forwarded-For) zählt ein mitgeschickter Kopf nicht
    assert main._adresse(anfrage("203.0.113.9", {"cf-connecting-ip": "198.51.100.4"})) == "203.0.113.9"


def test_einstellungsdatei_und_umzug(tmp_path, monkeypatch):
    from app import einstellungen
    pc = tmp_path / "pc.env"
    pc.write_text("\ufeff# Kommentar\nNACHBARSTROM_DB=sqlite:///betrieb/nachbarstrom.db\n"
                  'NACHBARSTROM_FIRMA_ADRESSE="Teststraße 1, 6800 Feldkirch"\r\n'
                  "NACHBARSTROM_FIRMA_UID='ATU00000000'\nNACHBARSTROM_ADMIN_PASSWORD_HASH=pbkdf2_sha256:1:aa:bb\n"
                  "NACHBARSTROM_HOSTER=Tunnel\nNACHBARSTROM_FIRMA_GF=\"A$B\"\nNACHBARSTROM_RECHTSTEXTE_GEPRUEFT=ja\n",
                  encoding="utf-8")
    werte = einstellungen.lesen(pc)
    assert werte["NACHBARSTROM_FIRMA_ADRESSE"] == "Teststraße 1, 6800 Feldkirch"
    assert werte["NACHBARSTROM_FIRMA_UID"] == "ATU00000000"

    monkeypatch.setenv("NACHBARSTROM_FIRMA_UID", "schon gesetzt")
    monkeypatch.delenv("NACHBARSTROM_FIRMA_ADRESSE", raising=False)
    einstellungen.laden(pc)
    import os
    assert os.environ["NACHBARSTROM_FIRMA_UID"] == "schon gesetzt"
    assert os.environ["NACHBARSTROM_FIRMA_ADRESSE"] == "Teststraße 1, 6800 Feldkirch"

    server = tmp_path / "server.env"
    server.write_text("# Server\nNACHBARSTROM_DB=sqlite:////var/lib/nachbarstrom/nachbarstrom.db\n"
                      "NACHBARSTROM_ADMIN_PASSWORD_HASH=pbkdf2_sha256:9:cc:dd\n"
                      '#NACHBARSTROM_FIRMA_ADRESSE="Straße Nr, PLZ Ort"\nNACHBARSTROM_FIRMA_ADRESSE="alt"\n'
                      '#NACHBARSTROM_HOSTER=""\n', encoding="utf-8")
    uebernommen, uebersprungen = einstellungen.uebernehmen(pc, server)
    assert uebernommen == ["NACHBARSTROM_FIRMA_ADRESSE", "NACHBARSTROM_FIRMA_UID", "NACHBARSTROM_RECHTSTEXTE_GEPRUEFT"]
    assert uebersprungen == ["NACHBARSTROM_FIRMA_GF"]
    neu = einstellungen.lesen(server)
    assert neu["NACHBARSTROM_DB"] == "sqlite:////var/lib/nachbarstrom/nachbarstrom.db"  # Server-Pfad bleibt
    assert neu["NACHBARSTROM_ADMIN_PASSWORD_HASH"] == "pbkdf2_sha256:9:cc:dd"  # Server-Passwort bleibt
    assert neu["NACHBARSTROM_FIRMA_ADRESSE"] == "Teststraße 1, 6800 Feldkirch"
    assert "NACHBARSTROM_HOSTER" not in neu
    assert server.read_text(encoding="utf-8").count("NACHBARSTROM_FIRMA_ADRESSE") == 1


def test_sicherung_am_pc(tmp_path):
    import os
    import sqlite3
    import time
    from app import sicherung
    db, geheimnis = tmp_path / "nachbarstrom.db", tmp_path / "nachbarstrom.geheimnis"
    with sqlite3.connect(db) as v:
        v.execute("create table teilnehmer (id integer)")
        v.execute("insert into teilnehmer values (1)")
    geheimnis.write_text("abc")
    ordner = tmp_path / "sicherung"
    ordner.mkdir()
    alt = ordner / "nachbarstrom-alt.db"
    alt.write_text("x")
    os.utime(alt, (time.time() - 40 * 86400,) * 2)
    ziel = sicherung.sichern(db, geheimnis, ordner)
    assert sqlite3.connect(ziel).execute("select count(*) from teilnehmer").fetchone()[0] == 1
    assert ziel.with_suffix(".geheimnis").read_text() == "abc"
    assert not alt.exists()
    assert sicherung.sichern(tmp_path / "fehlt.db", geheimnis, ordner) is None

    umzug = tmp_path / "umzug"
    sicherung.fuer_umzug(db, geheimnis, umzug)
    assert sorted(p.name for p in umzug.iterdir()) == ["nachbarstrom.db", "nachbarstrom.geheimnis"]
    assert sicherung.db_pfad("sqlite:///betrieb/nachbarstrom.db").as_posix() == "betrieb/nachbarstrom.db"


def test_zustimmung_zieht_status_nach(client):
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    r = c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth, follow_redirects=False)
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    with Session(engine) as s:
        g = s.get(Gruppe, gid)
        g.status = GruppenStatus.UNTERSCHRIEBEN
        s.add(g)
        s.commit()
    heute = date.today().isoformat()
    c.post(f"/verwaltung/gruppe/{gid}/zustimmung", auth=auth, data={"teilnehmer_id": 1, "freigegeben_am": heute})
    with Session(engine) as s:
        assert s.get(Gruppe, gid).status == GruppenStatus.EDA_ANGEMELDET
    for tid in (1, 2):
        c.post(f"/verwaltung/gruppe/{gid}/zustimmung", auth=auth, data={"teilnehmer_id": tid, "erteilt_am": heute})
    with Session(engine) as s:
        assert s.get(Gruppe, gid).status == GruppenStatus.AKTIV


def test_anmeldung_behaelt_auswahl_nach_fehler(client):
    c, _ = client
    seite = _anmelden(c, "abnehmer", 5, zaehlpunkt="AT123", profiltyp="waermepumpe").text
    assert '<option value="waermepumpe" selected>' in seite
    assert 'name="einwilligung" value="true" checked' in seite


# ---------------------------------------------------------------- P2P: einmal aufsetzen, dann erledigt

def _pfad(link: str) -> str:
    return "/" + link.split("://", 1)[1].split("/", 1)[1]


def test_p2p_einmalig_einrichtung(client):
    from app import config
    c, engine = client
    auth = ("admin", "test")
    assert config.P2P_ABRECHNUNG == "einmalig"  # Standard
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    r = c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth, follow_redirects=False)
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    seite = c.get(f"/verwaltung/gruppe/{gid}", auth=auth).text
    assert "Einrichtungsgebühr" in seite and "Noch kein Betrag festgelegt" in seite
    assert "Preisänderung ankündigen" not in seite and "Rechnungen erstellen" not in seite
    # Vor der Unterschrift wird nichts verrechnet
    assert c.post(f"/verwaltung/gruppe/{gid}/einrichtung", auth=auth).status_code == 400

    # Betrag setzen, Gruppe aktiv: die Übersicht erinnert ans Verrechnen
    c.post(f"/verwaltung/gruppe/{gid}", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 10, "ust_satz": 0,
                                                         "einrichtung_eur": 150})
    with Session(engine) as s:
        g = s.get(Gruppe, gid)
        assert g.einrichtung_eur == 150 and g.gebuehr_ct_kwh == config.GEBUEHR_STANDARD_CT_KWH  # nicht überschrieben
    assert f"Einrichtung Gruppe {gid}" in c.get("/verwaltung", auth=auth).text

    r = c.post(f"/verwaltung/gruppe/{gid}/einrichtung", auth=auth, follow_redirects=False)
    assert r.status_code == 303
    with Session(engine) as s:
        x = s.exec(select(main.Gebuehr)).one()
    assert x.art == "einrichtung" and x.nummer == f"FIX-{gid:04d}-E" and x.empfaenger_id == 1
    assert x.netto == 150 and x.brutto == pytest.approx(150 * (1 + config.DIENSTLEISTER_UST))
    assert f"Einrichtung Gruppe {gid}" not in c.get("/verwaltung", auth=auth).text
    # Nur einmal, Betrag danach fest
    assert c.post(f"/verwaltung/gruppe/{gid}/einrichtung", auth=auth).status_code == 400
    assert c.post(f"/verwaltung/gruppe/{gid}", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 10,
                                                                "ust_satz": 0, "einrichtung_eur": 99}).status_code == 400
    assert "für die Einrichtung" in c.get("/verwaltung/postausgang", auth=auth).text
    # Keine laufende Abrechnung und keine Preisänderung über FIX
    assert c.post(f"/verwaltung/gruppe/{gid}/abrechnungslauf", auth=auth,
                  data={"von": "2026-06-01", "bis": "2026-06-30"}).status_code == 400
    assert c.post(f"/verwaltung/gruppe/{gid}/preisaenderung", auth=auth,
                  data={"neuer_preis": 12, "ab": str(date.today() + timedelta(days=40)), "grund": "x"}).status_code == 400

    # Vertrag, Vollmacht und Infoblatt: der Erzeuger rechnet selbst ab
    vertrag = c.get(f"/verwaltung/gruppe/{gid}/vertrag", auth=auth).text
    assert "Die Rechnung stellt der Erzeuger selbst" in vertrag and "im Namen des Erzeugers" not in vertrag
    assert "Rechnungen und Gutschriften" not in c.get(f"/verwaltung/gruppe/{gid}/vollmacht", auth=auth).text
    assert "Stellt der Erzeuger selbst" in c.get(f"/verwaltung/gruppe/{gid}/infoblatt", auth=auth).text

    # Kundenbereich: der Erzeuger sieht unsere Rechnung, braucht aber keine IBAN für Abnehmer-Rechnungen
    bereich = _pfad(main.kundenlink(1))
    seite = c.get(bereich).text
    assert f"{bereich}/gebuehr/{x.id}" in seite and "Unsere Rechnung" in seite and "Konto für Zahlungen" not in seite
    rechnung = c.get(f"{bereich}/gebuehr/{x.id}").text
    assert "Einrichtung Peer-to-Peer-Vertrag" in rechnung and "Leistungsdatum" in rechnung
    assert "Ihre Rechnung kommt direkt vom Erzeuger" in c.get(_pfad(main.kundenlink(2))).text


def test_p2p_schenkung_ohne_rechnung(client):
    c, _ = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth)
    assert "Es gibt keine Rechnung" in c.get("/verwaltung/gruppe/1/vertrag", auth=auth).text
    assert "Keine, der Strom ist geschenkt" in c.get("/verwaltung/gruppe/1/infoblatt", auth=auth).text
    assert "Der Strom ist geschenkt" in c.get(_pfad(main.kundenlink(2))).text


def test_p2p_schenkung_laufend_nur_servicegebuehr(client, laufend):
    from app import energiedaten as ed
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth)
    c.post("/verwaltung/gruppe/1", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 0, "ust_satz": 0,
                                                    "rechnung_monatlich": "true", "gebuehr_ct_kwh": 1.5})
    with Session(engine) as s:
        zeiten = ed.viertelstunden(date(2026, 6, 1), date(2026, 6, 30))
        spuren = ed.simulieren(s.get(Teilnehmer, 1), [s.get(Teilnehmer, 2)], zeiten)
    c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("juni.xlsx", ed.export_schreiben(spuren, zeiten, "X")))])
    seite = c.post("/verwaltung/gruppe/1/abrechnungslauf", auth=auth, data={"von": "2026-06-01", "bis": "2026-06-30"}).text
    assert "Schenkung: keine Rechnungen" in seite
    with Session(engine) as s:
        assert not s.exec(select(Rechnung)).all()
        assert s.exec(select(main.Gebuehr)).one().art == "kwh"


def test_p2p_einrichtung_plus_laufend(client, laufend):
    """Einmalig plus laufend: Einrichtungsgebühr und danach die normale Abrechnung mit Servicegebühr."""
    from app import energiedaten as ed
    c, engine = client
    auth = ("admin", "test")
    _anmelden(c, "erzeuger", 1, anlage_kwp=10, jahresverbrauch_kwh=3000, profiltyp="haushalt")
    _anmelden(c, "abnehmer", 2)
    c.post("/verwaltung/gruppe", data={"erzeuger_id": 1, "abnehmer_ids": "2"}, auth=auth)
    c.post("/verwaltung/gruppe/1", auth=auth, data={"status": "aktiv", "preis_ct_kwh": 10, "ust_satz": 0,
                                                    "rechnung_monatlich": "true", "gebuehr_ct_kwh": 1.5,
                                                    "einrichtung_eur": 150})
    seite = c.get("/verwaltung/gruppe/1", auth=auth).text
    assert "Einrichtungsgebühr" in seite and "Rechnungen erstellen" in seite and "Preisänderung ankündigen" in seite
    assert "Einrichtung Gruppe 1" in c.get("/verwaltung", auth=auth).text
    r = c.post("/verwaltung/gruppe/1/einrichtung", auth=auth, follow_redirects=False)
    assert r.headers["location"].endswith("#einrichtung")
    with Session(engine) as s:
        zeiten = ed.viertelstunden(date(2026, 6, 1), date(2026, 6, 30))
        spuren = ed.simulieren(s.get(Teilnehmer, 1), [s.get(Teilnehmer, 2)], zeiten)
    c.post("/verwaltung/energiedaten", auth=auth, files=[("dateien", ("juni.xlsx", ed.export_schreiben(spuren, zeiten, "X")))])
    seite = c.post("/verwaltung/gruppe/1/abrechnungslauf", auth=auth, data={"von": "2026-06-01", "bis": "2026-06-30"}).text
    assert "1 Rechnung(en) erstellt" in seite
    with Session(engine) as s:
        assert sorted(x.art for x in s.exec(select(main.Gebuehr)).all()) == ["einrichtung", "kwh"]
    # Der Erzeuger sieht beide Rechnungen von uns und hat sie als Aufgabe
    bereich = c.get(_pfad(main.kundenlink(1))).text
    assert "Unsere Rechnungen an Sie" in bereich and "FIX-0001-E" in bereich and "FIX-0001-202606" in bereich
    assert bereich.count("Unsere Rechnung FIX-0001-") == 2
