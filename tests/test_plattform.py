import os
import re
from datetime import date, timedelta

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


def _anmelden(c, rolle, n, **extra):
    daten = dict(rolle=rolle, vorname="Vor", nachname=f"Name{n}", strasse="Weg 1", plz="6800", ort="Feldkirch",
                 email=f"t{n}@example.com", zaehlpunkt=zp(n), trafo_id="T1", umspannwerk_id="U1",
                 profiltyp="gewerbe", jahresverbrauch_kwh=6000, anlage_kwp=0, einwilligung="true")
    daten.update(extra)
    return c.post("/anmelden", data=daten, follow_redirects=False)


def test_ablauf_von_anmeldung_bis_rechnung(client):
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


def test_dienstleistung_kundenbereich_postausgang_preis(client):
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
    assert "aktive Gruppen" in c.get("/verwaltung", auth=auth).text


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


def test_abrechnungslauf_aus_energiedaten(client):
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
        "EDA_Export_Gruppe1_2026-08.xlsx", "EDA_Export_Gruppe1_2026-09.xlsx",
        "EDA_Export_Gruppe2_2026-08.xlsx", "EDA_Export_Gruppe2_2026-09.xlsx"]
    with Session(engine) as s:
        assert len(s.exec(select(Rechnung)).all()) == 3  # Gruppe 1, August, drei Abnehmer
        assert not s.exec(select(main.Messwert).where(main.Messwert.beginn >= "2026-09")).first()
