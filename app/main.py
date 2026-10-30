"""Fix Nachbarstrom: Web-Anwendung.

Öffentlich: Anmeldung, Einladungslink, Unterschrift, persönlicher Bereich je Teilnehmer.
Verwaltung (Passwort): Matching, Gruppen, Unterlagen für ebUtilities/EDA,
Erinnerungen, Postausgang, Preisänderung, Abrechnung.
"""
import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from . import abrechnung, config, matching, nachrichten, zahlung
from .erinnerungen import faellige_erinnerungen
from .models import (STATUS_TEXT, Einladung, Gruppe, GruppenStatus, Mitglied,
                     Nachricht, Profiltyp, Rechnung, Rolle, Teilnehmer, Unterschrift,
                     Zustimmung, jetzt)
from .zaehlpunkt import ist_gueltig, maskiert, netzbetreiber_code, normalisieren

engine = create_engine(config.DATENBANK, connect_args={"check_same_thread": False})


@asynccontextmanager
async def _lebenszyklus(_app):
    SQLModel.metadata.create_all(engine)
    _spalten_ergaenzen()
    yield


def _spalten_ergaenzen():
    """Neue Felder in eine bestehende Datenbank übernehmen, ohne Daten zu verlieren."""
    pruefer = inspect(engine)
    for tabelle in SQLModel.metadata.sorted_tables:
        if not pruefer.has_table(tabelle.name):
            continue
        vorhanden = {c["name"] for c in pruefer.get_columns(tabelle.name)}
        for spalte in tabelle.columns:
            if spalte.name not in vorhanden:
                typ = spalte.type.compile(engine.dialect)
                with engine.begin() as v:
                    v.execute(text(f"ALTER TABLE {tabelle.name} ADD COLUMN {spalte.name} {typ}"))


app = FastAPI(title=config.MARKE, lifespan=_lebenszyklus)
vorlagen = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
vorlagen.env.globals.update(marke=config.MARKE, dienstleister=config.DIENSTLEISTER,
                            status_text=STATUS_TEXT, maskiert=maskiert)
vorlagen.env.filters["zahl"] = lambda x, s=0: f"{x:,.{s}f}".replace(",", "X").replace(".", ",").replace("X", ".")
sicherheit = HTTPBasic(auto_error=True)


def sitzung():
    with Session(engine) as s:
        yield s


def verwaltung(cred: HTTPBasicCredentials = Depends(sicherheit)):
    ok = (config.ADMIN_PASSWORT
          and hmac.compare_digest(cred.username, config.ADMIN_BENUTZER)
          and hmac.compare_digest(cred.password, config.ADMIN_PASSWORT))
    if not ok:
        raise HTTPException(401, "Nicht angemeldet", headers={"WWW-Authenticate": "Basic"})
    return cred.username


def _signatur(text: str) -> str:
    return hmac.new(config.GEHEIMNIS.encode(), text.encode(), hashlib.sha256).hexdigest()[:24]


def unterschrift_token(gruppe_id: int, teilnehmer_id: int) -> str:
    return _signatur(f"{gruppe_id}:{teilnehmer_id}")


def kunden_token(teilnehmer_id: int) -> str:
    return _signatur(f"kunde:{teilnehmer_id}")


def kundenlink(teilnehmer_id: int) -> str:
    return f"{config.BASIS_URL}/mein/{teilnehmer_id}/{kunden_token(teilnehmer_id)}"


def unterschriftslink(gruppe_id: int, teilnehmer_id: int) -> str:
    return f"{config.BASIS_URL}/unterschreiben/{gruppe_id}/{teilnehmer_id}/{unterschrift_token(gruppe_id, teilnehmer_id)}"


def preis_fuer(g: Gruppe, tag: date) -> float:
    """Preis, der an einem Tag gilt. Eine angekündigte Änderung gilt ab ihrem Stichtag."""
    if g.preis_neu_ab and g.preis_neu_ct_kwh is not None and tag >= g.preis_neu_ab:
        return g.preis_neu_ct_kwh
    return g.preis_ct_kwh


def _seite(request: Request, name: str, **daten):
    return vorlagen.TemplateResponse(request, name, daten)


# ---------------------------------------------------------------- öffentlich

@app.get("/", response_class=HTMLResponse)
def start(request: Request):
    return _seite(request, "start.html")


@app.get("/anmelden", response_class=HTMLResponse)
def anmelden_formular(request: Request, rolle: Rolle = Rolle.ABNEHMER, einladung: str | None = None,
                      s: Session = Depends(sitzung)):
    erzeuger = None
    if einladung:
        e = s.exec(select(Einladung).where(Einladung.token == einladung)).first()
        if not e:
            raise HTTPException(404, "Einladung nicht gefunden")
        erzeuger = s.get(Teilnehmer, e.erzeuger_id)
        rolle = Rolle.ABNEHMER
    return _seite(request, "anmelden.html", rolle=rolle, einladung=einladung, erzeuger=erzeuger,
                  profiltypen=list(Profiltyp), fehler=None, werte={})


@app.post("/anmelden", response_class=HTMLResponse)
def anmelden(request: Request, s: Session = Depends(sitzung),
             rolle: Rolle = Form(...), vorname: str = Form(...), nachname: str = Form(...),
             firma: str = Form(""), strasse: str = Form(...), plz: str = Form(...), ort: str = Form(...),
             email: str = Form(...), telefon: str = Form(""), zaehlpunkt: str = Form(...),
             trafo_id: str = Form(""), umspannwerk_id: str = Form(""),
             profiltyp: Profiltyp = Form(Profiltyp.HAUSHALT), jahresverbrauch_kwh: float = Form(0),
             anlage_kwp: float = Form(0), einwilligung: bool = Form(False), einladung: str = Form("")):
    werte = dict(vorname=vorname, nachname=nachname, firma=firma, strasse=strasse, plz=plz, ort=ort,
                 email=email, telefon=telefon, zaehlpunkt=zaehlpunkt, trafo_id=trafo_id,
                 umspannwerk_id=umspannwerk_id, jahresverbrauch_kwh=jahresverbrauch_kwh, anlage_kwp=anlage_kwp)
    fehler = []
    zp = normalisieren(zaehlpunkt)
    if not ist_gueltig(zp):
        fehler.append("Die Zählpunktnummer hat 33 Stellen und beginnt mit AT. Sie steht auf der Netzrechnung.")
    elif s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == zp)).first():
        fehler.append("Dieser Zählpunkt ist schon angemeldet.")
    if not einwilligung:
        fehler.append("Ohne Einwilligung zur Datennutzung können wir keinen Partner suchen.")
    if rolle == Rolle.ERZEUGER and anlage_kwp <= 0:
        fehler.append("Bitte die Größe der PV-Anlage in kWp angeben.")
    if rolle == Rolle.ABNEHMER and jahresverbrauch_kwh <= 0:
        fehler.append("Bitte den Jahresverbrauch in kWh angeben (steht auf der Jahresabrechnung).")
    e = None
    if einladung:
        e = s.exec(select(Einladung).where(Einladung.token == einladung)).first()
    if fehler:
        erzeuger = s.get(Teilnehmer, e.erzeuger_id) if e else None
        return _seite(request, "anmelden.html", rolle=rolle, einladung=einladung or None, erzeuger=erzeuger,
                      profiltypen=list(Profiltyp), fehler=fehler, werte=werte)
    t = Teilnehmer(rolle=rolle, vorname=vorname.strip(), nachname=nachname.strip(), firma=firma.strip() or None,
                   strasse=strasse.strip(), plz=plz.strip(), ort=ort.strip(), email=email.strip(),
                   telefon=telefon.strip() or None, zaehlpunkt=zp, netzbetreiber=netzbetreiber_code(zp),
                   trafo_id=trafo_id.strip() or None, umspannwerk_id=umspannwerk_id.strip() or None,
                   profiltyp=profiltyp, jahresverbrauch_kwh=jahresverbrauch_kwh, anlage_kwp=anlage_kwp,
                   einladung_von=e.erzeuger_id if e else None,
                   einwilligung_daten=True, einwilligung_zeit=jetzt())
    s.add(t)
    s.commit()
    s.refresh(t)
    if e and not e.eingeloest_von:
        e.eingeloest_von = t.id
        s.add(e)
    s.add(nachrichten.willkommen(t, kundenlink(t.id)))
    s.commit()
    return RedirectResponse(f"/danke/{t.id}", status_code=303)


@app.get("/danke/{tid}", response_class=HTMLResponse)
def danke(request: Request, tid: int, s: Session = Depends(sitzung)):
    t = s.get(Teilnehmer, tid)
    if not t:
        raise HTTPException(404)
    link = None
    if t.rolle == Rolle.ERZEUGER:
        e = s.exec(select(Einladung).where(Einladung.erzeuger_id == t.id)).first()
        if not e:
            e = Einladung(token=secrets.token_urlsafe(12), erzeuger_id=t.id)
            s.add(e)
            s.commit()
        link = f"{config.BASIS_URL}/anmelden?einladung={e.token}"
    return _seite(request, "danke.html", t=t, link=link, bereich=kundenlink(t.id))


@app.get("/unterschreiben/{gid}/{tid}/{token}", response_class=HTMLResponse)
def unterschreiben_seite(request: Request, gid: int, tid: int, token: str, s: Session = Depends(sitzung)):
    g, t = _pruefe_unterschrift(s, gid, tid, token)
    daten = _gruppen_daten(s, g)
    erledigt = s.exec(select(Unterschrift).where(Unterschrift.gruppe_id == gid,
                                                 Unterschrift.teilnehmer_id == tid)).all()
    return _seite(request, "unterschreiben.html", g=g, t=t, token=token, erledigt=erledigt, **daten)


@app.post("/unterschreiben/{gid}/{tid}/{token}")
def unterschreiben(request: Request, gid: int, tid: int, token: str, s: Session = Depends(sitzung),
                   name_getippt: str = Form(...), gelesen: bool = Form(False)):
    g, t = _pruefe_unterschrift(s, gid, tid, token)
    if not gelesen or name_getippt.strip().lower() != f"{t.vorname} {t.nachname}".lower():
        raise HTTPException(400, "Bitte Häkchen setzen und Vor- und Nachnamen genau so eintippen.")
    ip = request.client.host if request.client else None
    for dok in ("vertrag", "vollmacht"):
        s.add(Unterschrift(gruppe_id=gid, teilnehmer_id=tid, dokument=dok, name_getippt=name_getippt.strip(), ip=ip))
    s.commit()
    if _alle_unterschrieben(s, g) and g.status in (GruppenStatus.VORSCHLAG, GruppenStatus.VERTRAG_OFFEN):
        g.status = GruppenStatus.UNTERSCHRIEBEN
        s.add(g)
        s.commit()
    return RedirectResponse(f"/unterschreiben/{gid}/{tid}/{token}", status_code=303)


def _pruefe_unterschrift(s: Session, gid: int, tid: int, token: str):
    if not hmac.compare_digest(token, unterschrift_token(gid, tid)):
        raise HTTPException(404)
    g, t = s.get(Gruppe, gid), s.get(Teilnehmer, tid)
    if not g or not t:
        raise HTTPException(404)
    return g, t


# ---------------------------------------------------------------- Hilfen

def _gruppen_daten(s: Session, g: Gruppe) -> dict:
    erzeuger = s.get(Teilnehmer, g.erzeuger_id)
    mitglieder = s.exec(select(Mitglied).where(Mitglied.gruppe_id == g.id)).all()
    abnehmer = [(m, s.get(Teilnehmer, m.teilnehmer_id)) for m in mitglieder]
    return {"erzeuger": erzeuger, "abnehmer": abnehmer, "heute": date.today()}


def _alle_unterschrieben(s: Session, g: Gruppe) -> bool:
    d = _gruppen_daten(s, g)
    ids = {d["erzeuger"].id} | {t.id for _, t in d["abnehmer"]}
    unterschrieben = {u.teilnehmer_id for u in s.exec(select(Unterschrift).where(
        Unterschrift.gruppe_id == g.id, Unterschrift.dokument == "vertrag")).all()}
    return ids <= unterschrieben


def _in_aktiver_gruppe(s: Session) -> set[int]:
    offen = [g for g in s.exec(select(Gruppe)).all() if g.status != GruppenStatus.BEENDET]
    ids = {g.erzeuger_id for g in offen}
    for g in offen:
        ids |= {m.teilnehmer_id for m in s.exec(select(Mitglied).where(Mitglied.gruppe_id == g.id)).all()}
    return ids


# ---------------------------------------------------------------- Verwaltung

@app.get("/verwaltung", response_class=HTMLResponse)
def uebersicht(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    gruppen = s.exec(select(Gruppe).order_by(Gruppe.erstellt.desc())).all()
    teilnehmer = s.exec(select(Teilnehmer)).all()
    erinnerungen = _erinnerungen(s)
    zeilen = [(g, s.get(Teilnehmer, g.erzeuger_id)) for g in gruppen]
    aktiv = [g for g in gruppen if g.status == GruppenStatus.AKTIV]
    rechnungen = s.exec(select(Rechnung)).all()
    kennzahlen = {
        "aktive_gruppen": len(aktiv),
        "zaehlpunkte_aktiv": len(aktiv) + sum(len(s.exec(select(Mitglied).where(Mitglied.gruppe_id == g.id)).all())
                                              for g in aktiv),
        "kwh_erwartet": sum(g.erwartete_kwh_jahr for g in aktiv),
        "kwh_abgerechnet": sum(r.kwh for r in rechnungen),
        "umsatz_abgerechnet": sum(r.brutto for r in rechnungen),
        "postausgang": len(s.exec(select(Nachricht).where(Nachricht.gesendet_am == None)).all()),  # noqa: E711
    }
    return _seite(request, "verwaltung.html", zeilen=zeilen, teilnehmer=teilnehmer, k=kennzahlen,
                  anzahl_erinnerungen=len(erinnerungen),
                  erzeuger=sum(1 for t in teilnehmer if t.rolle == Rolle.ERZEUGER),
                  abnehmer=sum(1 for t in teilnehmer if t.rolle == Rolle.ABNEHMER))


@app.get("/verwaltung/teilnehmer", response_class=HTMLResponse)
def teilnehmer_liste(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    return _seite(request, "teilnehmer.html", liste=s.exec(select(Teilnehmer).order_by(Teilnehmer.erstellt)).all(),
                  in_gruppe=_in_aktiver_gruppe(s))


@app.get("/verwaltung/matching", response_class=HTMLResponse)
def matching_seite(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    belegt = _in_aktiver_gruppe(s)
    alle = s.exec(select(Teilnehmer)).all()
    frei_abnehmer = [t for t in alle if t.rolle == Rolle.ABNEHMER and t.id not in belegt]
    vorschlaege, ohne = [], []
    for e in [t for t in alle if t.rolle == Rolle.ERZEUGER and t.id not in belegt]:
        # Eingeladene Wunschpartner zuerst berücksichtigen
        eingeladen = [a for a in frei_abnehmer if a.einladung_von == e.id]
        kandidaten = eingeladen or frei_abnehmer
        v = matching.gruppe_vorschlagen(e, kandidaten)
        (vorschlaege if v else ohne).append(v or e)
        if v:  # Ein Abnehmer wird nur einem Erzeuger vorgeschlagen
            frei_abnehmer = [a for a in frei_abnehmer if a not in v.abnehmer]
    return _seite(request, "matching.html", vorschlaege=vorschlaege, ohne=ohne)


@app.post("/verwaltung/gruppe")
def gruppe_anlegen(s: Session = Depends(sitzung), _=Depends(verwaltung),
                   erzeuger_id: int = Form(...), abnehmer_ids: str = Form(...)):
    e = s.get(Teilnehmer, erzeuger_id)
    abnehmer = [s.get(Teilnehmer, int(i)) for i in abnehmer_ids.split(",") if i]
    if not e or not abnehmer or None in abnehmer:
        raise HTTPException(400, "Unvollständige Gruppe")
    belegt = _in_aktiver_gruppe(s)
    if e.id in belegt or any(a.id in belegt for a in abnehmer):
        raise HTTPException(400, "Mindestens ein Teilnehmer ist schon in einer Gruppe.")
    zonen = [matching.zonelevel(e, a) for a in abnehmer]
    if None in zonen:
        raise HTTPException(400, "Alle müssen beim selben Netzbetreiber sein.")
    menge = matching.geteilte_menge(e, abnehmer)
    g = Gruppe(erzeuger_id=e.id, zonelevel=max(zonen), erwartete_kwh_jahr=round(sum(menge.values())),
               status=GruppenStatus.VERTRAG_OFFEN)
    s.add(g)
    s.commit()
    s.refresh(g)
    for a in abnehmer:
        s.add(Mitglied(gruppe_id=g.id, teilnehmer_id=a.id, erwartete_kwh_jahr=round(menge[a.id])))
    for t in [e] + abnehmer:
        s.add(nachrichten.unterschrift(t, g.id, unterschriftslink(g.id, t.id)))
    s.commit()
    return RedirectResponse(f"/verwaltung/gruppe/{g.id}", status_code=303)


@app.get("/verwaltung/gruppe/{gid}", response_class=HTMLResponse)
def gruppe_seite(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    d = _gruppen_daten(s, g)
    alle = [d["erzeuger"]] + [t for _, t in d["abnehmer"]]
    unterschriften = {u.teilnehmer_id for u in s.exec(select(Unterschrift).where(
        Unterschrift.gruppe_id == gid, Unterschrift.dokument == "vertrag")).all()}
    links = {t.id: unterschriftslink(gid, t.id) for t in alle}
    zustimmungen = {z.teilnehmer_id: z for z in s.exec(select(Zustimmung).where(Zustimmung.gruppe_id == gid)).all()}
    rechnungen = s.exec(select(Rechnung).where(Rechnung.gruppe_id == gid).order_by(Rechnung.bis.desc())).all()
    namen = {t.id: t.name for t in alle}
    return _seite(request, "gruppe.html", g=g, alle=alle, unterschriften=unterschriften, links=links,
                  zustimmungen=zustimmungen, rechnungen=rechnungen, stati=list(GruppenStatus), namen=namen,
                  frueheste_aenderung=date.today() + timedelta(days=31), **d)


@app.post("/verwaltung/gruppe/{gid}")
def gruppe_aendern(gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
                   status: GruppenStatus = Form(...), preis_ct_kwh: float = Form(0), ust_satz: float = Form(0),
                   rechnung_monatlich: bool = Form(False), marktpartner_id: str = Form(""),
                   gemeinschafts_id: str = Form(""), notiz: str = Form("")):
    g = s.get(Gruppe, gid) or _404()
    if status == GruppenStatus.REGISTRIERT and not (marktpartner_id.strip() and gemeinschafts_id.strip()):
        raise HTTPException(400, "Für „registriert“ bitte Marktpartner-ID und Gemeinschafts-ID eintragen.")
    if preis_ct_kwh != g.preis_ct_kwh and g.status == GruppenStatus.AKTIV:
        raise HTTPException(400, "In einer aktiven Gruppe ändert sich der Preis nur über „Preisänderung ankündigen“.")
    g.status, g.preis_ct_kwh, g.ust_satz = status, preis_ct_kwh, ust_satz
    g.rechnung_monatlich = rechnung_monatlich
    g.marktpartner_id = marktpartner_id.strip() or None
    g.gemeinschafts_id = normalisieren(gemeinschafts_id) or None
    g.notiz = notiz.strip() or None
    s.add(g)
    s.commit()
    return RedirectResponse(f"/verwaltung/gruppe/{gid}", status_code=303)


@app.post("/verwaltung/gruppe/{gid}/zustimmung")
def zustimmung_setzen(gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
                      teilnehmer_id: int = Form(...), freigegeben_am: str = Form(""), erteilt_am: str = Form("")):
    g = s.get(Gruppe, gid) or _404()
    z = s.exec(select(Zustimmung).where(Zustimmung.gruppe_id == gid,
                                        Zustimmung.teilnehmer_id == teilnehmer_id)).first()
    z = z or Zustimmung(gruppe_id=gid, teilnehmer_id=teilnehmer_id)
    z.freigegeben_am = date.fromisoformat(freigegeben_am) if freigegeben_am else z.freigegeben_am
    z.erteilt_am = date.fromisoformat(erteilt_am) if erteilt_am else z.erteilt_am
    s.add(z)
    s.commit()
    d = _gruppen_daten(s, g)
    ids = {d["erzeuger"].id} | {t.id for _, t in d["abnehmer"]}
    erteilt = {x.teilnehmer_id for x in s.exec(select(Zustimmung).where(Zustimmung.gruppe_id == gid)).all()
               if x.erteilt_am}
    if ids <= erteilt and g.status == GruppenStatus.EDA_ANGEMELDET:
        g.status = GruppenStatus.AKTIV
        s.add(g)
        s.commit()
    return RedirectResponse(f"/verwaltung/gruppe/{gid}", status_code=303)


@app.get("/verwaltung/gruppe/{gid}/vertrag", response_class=HTMLResponse)
def vertrag(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    return _seite(request, "vertrag.html", g=g, **_gruppen_daten(s, g))


@app.get("/verwaltung/gruppe/{gid}/vollmacht", response_class=HTMLResponse)
def vollmacht(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    return _seite(request, "vollmacht.html", g=g, **_gruppen_daten(s, g))


@app.get("/verwaltung/gruppe/{gid}/anmeldung", response_class=HTMLResponse)
def anmeldedaten(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    """Alles, was auf ebUtilities und im EDA-Portal einzutragen ist, auf einer Seite."""
    g = s.get(Gruppe, gid) or _404()
    return _seite(request, "anmeldung.html", g=g, **_gruppen_daten(s, g))


@app.get("/verwaltung/erinnerungen", response_class=HTMLResponse)
def erinnerungen(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    return _seite(request, "erinnerungen.html", liste=_erinnerungen(s))


@app.post("/verwaltung/erinnerungen/{zid}/erledigt")
def erinnerung_erledigt(zid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    z = s.get(Zustimmung, zid) or _404()
    z.erinnerungen += 1
    z.letzte_erinnerung = date.today()
    s.add(z)
    s.commit()
    return RedirectResponse("/verwaltung/erinnerungen", status_code=303)


def _erinnerungen(s: Session):
    paare = [(z, s.get(Teilnehmer, z.teilnehmer_id)) for z in s.exec(select(Zustimmung)).all()]
    return faellige_erinnerungen(paare)


@app.post("/verwaltung/gruppe/{gid}/abrechnung", response_class=HTMLResponse)
async def abrechnen(request: Request, gid: int, report: UploadFile, s: Session = Depends(sitzung),
                    _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    d = _gruppen_daten(s, g)
    try:
        zeilen = abrechnung.report_einlesen((await report.read()).decode("utf-8-sig"))
    except ValueError as e:
        raise HTTPException(400, f"Report nicht lesbar: {e}")
    nach_zp = {t.zaehlpunkt: t for _, t in d["abnehmer"]}
    neu, uebersprungen = 0, []
    for z in zeilen:
        t = nach_zp.get(z.zaehlpunkt)
        if not t:
            uebersprungen.append(maskiert(z.zaehlpunkt))
            continue
        nummer = abrechnung.rechnungsnummer(g.id, t.id, z.bis)
        if s.exec(select(Rechnung).where(Rechnung.nummer == nummer)).first():
            uebersprungen.append(f"{nummer} (gibt es schon)")
            continue
        preis = preis_fuer(g, z.von)
        b = abrechnung.berechnen(z.kwh, preis, g.ust_satz)
        heute = date.today()
        r = Rechnung(nummer=nummer, gruppe_id=g.id, abnehmer_id=t.id, von=z.von, bis=z.bis, kwh=z.kwh,
                     preis_ct_kwh=preis, ust_satz=g.ust_satz, netto=b.netto, ust=b.ust,
                     brutto=b.brutto, datum=heute, faellig=abrechnung.faelligkeit(heute))
        s.add(r)
        s.flush()
        s.add(nachrichten.rechnung(t, g.id, nummer, _euro(b.brutto), r.faellig,
                                   f"{kundenlink(t.id)}/rechnung/{r.id}"))
        neu += 1
    s.commit()
    return _seite(request, "abrechnung_ergebnis.html", g=g, neu=neu, uebersprungen=uebersprungen)


@app.get("/verwaltung/rechnung/{rid}", response_class=HTMLResponse)
def rechnung(request: Request, rid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    r = s.get(Rechnung, rid) or _404()
    return _seite(request, "rechnung.html", **_rechnung_daten(s, r))


def _rechnung_daten(s: Session, r: Rechnung) -> dict:
    g = s.get(Gruppe, r.gruppe_id)
    erzeuger = s.get(Teilnehmer, g.erzeuger_id)
    qr = None
    if erzeuger.iban and r.brutto > 0:
        qr = zahlung.qr_svg(erzeuger.name, erzeuger.iban, r.brutto, f"Rechnung {r.nummer}")
    return {"r": r, "g": g, "abnehmer": s.get(Teilnehmer, r.abnehmer_id), "erzeuger": erzeuger, "qr": qr,
            "iban": zahlung.iban_lesbar(erzeuger.iban) if erzeuger.iban else None}


@app.get("/verwaltung/gruppe/{gid}/infoblatt", response_class=HTMLResponse)
def infoblatt(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    return _seite(request, "infoblatt.html", g=g, **_gruppen_daten(s, g))


@app.post("/verwaltung/gruppe/{gid}/preisaenderung")
def preisaenderung(gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
                   neuer_preis: float = Form(...), ab: date = Form(...), grund: str = Form(...)):
    """Preisänderung nach § 69 ElWG: mindestens einen Monat vorher, mit Begründung, an jeden Abnehmer."""
    g = s.get(Gruppe, gid) or _404()
    heute = date.today()
    if ab < heute + timedelta(days=31):
        raise HTTPException(400, "Eine Preisänderung muss mindestens einen Monat vorher angekündigt werden.")
    if not grund.strip():
        raise HTTPException(400, "Bitte einen Grund angeben, er steht in der Mitteilung.")
    if g.preis_neu_ab and g.preis_neu_ab <= heute:  # frühere Änderung gilt schon
        g.preis_ct_kwh = g.preis_neu_ct_kwh
    alt = preis_fuer(g, heute)
    g.preis_neu_ct_kwh, g.preis_neu_ab = neuer_preis, ab
    s.add(g)
    for _m, t in _gruppen_daten(s, g)["abnehmer"]:
        s.add(nachrichten.preisaenderung(t, g.id, _euro(alt), _euro(neuer_preis), ab, grund.strip(),
                                         kundenlink(t.id)))
    s.commit()
    return RedirectResponse("/verwaltung/postausgang", status_code=303)


@app.get("/verwaltung/postausgang", response_class=HTMLResponse)
def postausgang(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    offen = s.exec(select(Nachricht).where(Nachricht.gesendet_am == None).order_by(Nachricht.erstellt)).all()  # noqa: E711
    gesendet = s.exec(select(Nachricht).where(Nachricht.gesendet_am != None)  # noqa: E711
                      .order_by(Nachricht.gesendet_am.desc()).limit(50)).all()
    empfaenger = {n.teilnehmer_id: s.get(Teilnehmer, n.teilnehmer_id) for n in offen + gesendet}
    links = {n.id: nachrichten.mailto(empfaenger[n.teilnehmer_id].email, n.betreff, n.text) for n in offen}
    return _seite(request, "postausgang.html", offen=offen, gesendet=gesendet, empfaenger=empfaenger, links=links)


@app.post("/verwaltung/postausgang/{nid}/gesendet")
def nachricht_gesendet(nid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    n = s.get(Nachricht, nid) or _404()
    n.gesendet_am = n.gesendet_am or jetzt()
    s.add(n)
    s.commit()
    return RedirectResponse("/verwaltung/postausgang", status_code=303)


# ---------------------------------------------------------------- Kundenbereich

def _kunde(s: Session, tid: int, token: str) -> Teilnehmer:
    if not hmac.compare_digest(token, kunden_token(tid)):
        raise HTTPException(404)
    return s.get(Teilnehmer, tid) or _404()


def _gruppe_von(s: Session, t: Teilnehmer) -> Gruppe | None:
    if t.rolle == Rolle.ERZEUGER:
        gruppen = s.exec(select(Gruppe).where(Gruppe.erzeuger_id == t.id)).all()
    else:
        ids = [m.gruppe_id for m in s.exec(select(Mitglied).where(Mitglied.teilnehmer_id == t.id)).all()]
        gruppen = [s.get(Gruppe, i) for i in ids]
    offen = [g for g in gruppen if g.status != GruppenStatus.BEENDET]
    return max(offen or gruppen, key=lambda g: g.id, default=None)


SCHRITTE = [GruppenStatus.VERTRAG_OFFEN, GruppenStatus.UNTERSCHRIEBEN, GruppenStatus.REGISTRIERT,
            GruppenStatus.EDA_ANGEMELDET, GruppenStatus.AKTIV]


@app.get("/mein/{tid}/{token}", response_class=HTMLResponse)
def mein_bereich(request: Request, tid: int, token: str, s: Session = Depends(sitzung), iban_fehler: str = ""):
    t = _kunde(s, tid, token)
    g = _gruppe_von(s, t)
    daten = {"t": t, "g": g, "token": token, "iban_fehler": iban_fehler, "rechnungen": [], "aufgaben": [],
             "schritte": SCHRITTE, "erreicht": -1, "einladung": None, "partner": []}
    if t.rolle == Rolle.ERZEUGER:
        e = s.exec(select(Einladung).where(Einladung.erzeuger_id == t.id)).first()
        daten["einladung"] = f"{config.BASIS_URL}/anmelden?einladung={e.token}" if e else None
        daten["iban"] = zahlung.iban_lesbar(t.iban) if t.iban else None
    if g:
        d = _gruppen_daten(s, g)
        daten["partner"] = [d["erzeuger"]] + [a for _, a in d["abnehmer"]]
        daten["erreicht"] = SCHRITTE.index(g.status) if g.status in SCHRITTE else -1
        daten["preis_heute"] = preis_fuer(g, date.today())
        unterschrieben = s.exec(select(Unterschrift).where(Unterschrift.gruppe_id == g.id,
                                                           Unterschrift.teilnehmer_id == t.id)).first()
        if not unterschrieben and g.status != GruppenStatus.BEENDET:
            daten["aufgaben"].append(("Vertrag und Vollmacht unterschreiben", unterschriftslink(g.id, t.id)))
        z = s.exec(select(Zustimmung).where(Zustimmung.gruppe_id == g.id, Zustimmung.teilnehmer_id == t.id)).first()
        if z and z.freigegeben_am and not z.erteilt_am:
            frist = z.freigegeben_am + timedelta(days=config.ZUSTIMMUNG_FRIST_TAGE)
            daten["aufgaben"].append((f"Im Kundenportal von Vorarlberg Netz zustimmen, bis {frist:%d.%m.%Y}", None))
        if t.rolle == Rolle.ERZEUGER and g.preis_ct_kwh > 0 and not t.iban:
            daten["aufgaben"].append(("Konto (IBAN) für die Zahlungen eintragen, siehe unten", None))
        abfrage = select(Rechnung).where(Rechnung.gruppe_id == g.id)
        if t.rolle == Rolle.ABNEHMER:
            abfrage = abfrage.where(Rechnung.abnehmer_id == t.id)
        daten["rechnungen"] = s.exec(abfrage.order_by(Rechnung.bis.desc())).all()
    return _seite(request, "mein.html", **daten)


@app.post("/mein/{tid}/{token}/iban")
def mein_iban(tid: int, token: str, s: Session = Depends(sitzung), iban: str = Form(...)):
    t = _kunde(s, tid, token)
    if t.rolle != Rolle.ERZEUGER:
        raise HTTPException(400)
    if not zahlung.iban_gueltig(iban):
        return RedirectResponse(f"/mein/{tid}/{token}?iban_fehler=1", status_code=303)
    t.iban = zahlung.iban_normalisieren(iban)
    s.add(t)
    s.commit()
    return RedirectResponse(f"/mein/{tid}/{token}", status_code=303)


@app.get("/mein/{tid}/{token}/rechnung/{rid}", response_class=HTMLResponse)
def mein_rechnung(request: Request, tid: int, token: str, rid: int, s: Session = Depends(sitzung)):
    t = _kunde(s, tid, token)
    r = s.get(Rechnung, rid) or _404()
    g = s.get(Gruppe, r.gruppe_id)
    if t.id not in (r.abnehmer_id, g.erzeuger_id):
        raise HTTPException(404)
    return _seite(request, "mein_rechnung.html", zurueck=f"/mein/{tid}/{token}", **_rechnung_daten(s, r))


@app.get("/mein/{tid}/{token}/infoblatt", response_class=HTMLResponse)
def mein_infoblatt(request: Request, tid: int, token: str, s: Session = Depends(sitzung)):
    t = _kunde(s, tid, token)
    g = _gruppe_von(s, t) or _404()
    return _seite(request, "mein_infoblatt.html", g=g, zurueck=f"/mein/{tid}/{token}", **_gruppen_daten(s, g))


def _euro(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _404():
    raise HTTPException(404)
