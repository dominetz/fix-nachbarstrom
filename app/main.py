"""Fix Nachbarstrom: Web-Anwendung.

Zwei Modelle auf einer Plattform:
* P2P: Nachbarn teilen Strom (§ 68 ElWG). FIX findet Partner (Wunschnachbar über
  Einladungslink oder Matching), erstellt Vertrag und Vollmacht, meldet an und rechnet ab.
* GEA: gemeinschaftliche Erzeugungsanlage im Mehrparteienhaus. Der Betreiber
  registriert selbst über ebUtilities, lädt monatlich den EDA-Export hoch und
  bekommt die Zahlen je Wohnung. Auf Wunsch rechnet FIX ab.
Für beide Modelle kann eine Servicegebühr je geteilter kWh vereinbart werden.

Öffentlich: Startseite, Anfragen, Anmeldung, Unterschrift, persönlicher Bereich.
Verwaltung (Passwort): alles andere.
"""
import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, timedelta
from enum import Enum
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from . import sicherheit as schutz
from . import (abrechnung, auswertung, config, diagramme, eda_import, energiedaten, gea, gebuehren, importe,
               matching, nachrichten, zahlung)
from .erinnerungen import faellige_erinnerungen
from .models import (GEA_STATUS, GEA_STATUS_TEXT, STATUS_TEXT, Anfrage, Art, Einladung, Gebuehr, Gruppe,
                     GruppenStatus, Messwert, Mitglied, Modell, Nachricht, Profiltyp, Rechnung, Rolle, Teilnehmer,
                     Unterschrift, Upload, Zustimmung, jetzt)
from .zaehlpunkt import ist_gueltig, maskiert, netzbetreiber_code, normalisieren

engine = create_engine(config.DATENBANK, connect_args={"check_same_thread": False})


@asynccontextmanager
async def _lebenszyklus(_app):
    SQLModel.metadata.create_all(engine)
    _spalten_ergaenzen()
    importe.alte_werte_zuordnen(engine)
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
                standard = spalte.default.arg if spalte.default is not None else None
                zusatz = ""
                if isinstance(standard, Enum):
                    zusatz = f" DEFAULT '{standard.name}'"
                elif isinstance(standard, bool):
                    zusatz = f" DEFAULT {int(standard)}"
                elif isinstance(standard, (int, float)):
                    zusatz = f" DEFAULT {standard}"
                elif isinstance(standard, str):
                    zusatz = " DEFAULT '" + standard.replace("'", "''") + "'"
                with engine.begin() as v:
                    v.execute(text(f"ALTER TABLE {tabelle.name} ADD COLUMN {spalte.name} {typ}{zusatz}"))


app = FastAPI(title=config.MARKE, lifespan=_lebenszyklus, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

_OEFFENTLICH = ("/", "/gea", "/anfrage", "/anmelden", "/impressum", "/datenschutz")


@app.middleware("http")
async def _kopfzeilen(request: Request, call_next):
    """Sicherheits-Kopfzeilen. Persönliche Links tragen ein Token in der Adresse: die
    Adresse geht deshalb nicht an fremde Seiten weiter, und nur die öffentlichen
    Seiten dürfen in Suchmaschinen."""
    antwort = await call_next(request)
    antwort.headers.setdefault("X-Content-Type-Options", "nosniff")
    antwort.headers.setdefault("X-Frame-Options", "DENY")
    antwort.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path not in _OEFFENTLICH and not request.url.path.startswith("/static/"):
        antwort.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return antwort
vorlagen = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def rechnung_status(r: Rechnung, heute: date | None = None) -> str:
    if r.bezahlt_am:
        return "bezahlt"
    return "überfällig" if (heute or date.today()) > r.faellig else "offen"


def zp_anzeige(nummer: str) -> str:
    return "noch nicht eingetragen" if (nummer or "").startswith(importe.OHNE_ZP) else maskiert(nummer)


vorlagen.env.globals.update(marke=config.MARKE, dienstleister=config.DIENSTLEISTER, status_text=STATUS_TEXT,
                            gea_status_text=GEA_STATUS_TEXT, maskiert=zp_anzeige, rechnung_status=rechnung_status,
                            monatsname=diagramme.monatsname, feld_text=eda_import.FELD_TEXT)
vorlagen.env.filters["zahl"] = lambda x, s=0: f"{x:,.{s}f}".replace(",", "X").replace(".", ",").replace("X", ".")
sicherheit = HTTPBasic(auto_error=True)


def sitzung():
    with Session(engine) as s:
        yield s


sperre = schutz.Sperre()


def _adresse(request: Request) -> str:
    """Adresse des Besuchers. Hinter dem Webserver (Caddy) setzt uvicorn sie aus X-Forwarded-For."""
    return request.client.host if request.client else "unbekannt"


def _passwort_ok(passwort: str) -> bool:
    if config.ADMIN_PASSWORT_HASH:
        return schutz.passwort_pruefen(passwort, config.ADMIN_PASSWORT_HASH)
    return bool(config.ADMIN_PASSWORT) and hmac.compare_digest(passwort, config.ADMIN_PASSWORT)


def verwaltung(request: Request, cred: HTTPBasicCredentials = Depends(sicherheit)):
    adresse = _adresse(request)
    if sperre.gesperrt(adresse):
        raise HTTPException(429, "Zu viele falsche Anmeldungen. Bitte in 15 Minuten nochmal versuchen.")
    ok = hmac.compare_digest(cred.username, config.ADMIN_BENUTZER) & _passwort_ok(cred.password)
    if not ok:
        sperre.fehlversuch(adresse)
        raise HTTPException(401, "Nicht angemeldet", headers={"WWW-Authenticate": "Basic"})
    sperre.erfolg(adresse)
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


preis_fuer = abrechnung.preis_fuer


def gruppenlink(g: Gruppe) -> str:
    return f"/verwaltung/gea/{g.id}" if g.art == Art.GEA else f"/verwaltung/gruppe/{g.id}"


vorlagen.env.globals["gruppenlink"] = gruppenlink


def _seite(request: Request, name: str, **daten):
    return vorlagen.TemplateResponse(request, name, daten)


# ---------------------------------------------------------------- öffentlich

@app.get("/", response_class=HTMLResponse)
def start(request: Request):
    return _seite(request, "start.html")


@app.get("/anmelden", response_class=HTMLResponse)
def anmelden_formular(request: Request, rolle: Rolle = Rolle.ABNEHMER, einladung: str | None = None,
                      s: Session = Depends(sitzung)):
    erzeuger = None  # wer eingeladen hat (Erzeuger oder Abnehmer)
    if einladung:
        e = s.exec(select(Einladung).where(Einladung.token == einladung)).first()
        if not e:
            raise HTTPException(404, "Einladung nicht gefunden")
        erzeuger = s.get(Teilnehmer, e.erzeuger_id)
        rolle = Rolle.ABNEHMER if erzeuger.rolle == Rolle.ERZEUGER else Rolle.ERZEUGER
    return _seite(request, "anmelden.html", rolle=rolle, einladung=einladung, erzeuger=erzeuger,
                  profiltypen=list(Profiltyp), fehler=None, werte={})


@app.post("/anmelden", response_class=HTMLResponse)
def anmelden(request: Request, s: Session = Depends(sitzung),
             rolle: Rolle = Form(...), vorname: str = Form(...), nachname: str = Form(...),
             firma: str = Form(""), strasse: str = Form(...), plz: str = Form(...), ort: str = Form(...),
             email: str = Form(...), telefon: str = Form(""), zaehlpunkt: str = Form(...),
             zaehlpunkt_bezug: str = Form(""), zaehlpunkt_einspeisung: str = Form(""),
             trafo_id: str = Form(""), umspannwerk_id: str = Form(""),
             profiltyp: Profiltyp = Form(Profiltyp.HAUSHALT), jahresverbrauch_kwh: float = Form(0),
             anlage_kwp: float = Form(0), einwilligung: bool = Form(False), einladung: str = Form(""),
             wunschpartner: str = Form("")):
    werte = dict(vorname=vorname, nachname=nachname, firma=firma, strasse=strasse, plz=plz, ort=ort,
                 email=email, telefon=telefon, zaehlpunkt=zaehlpunkt, zaehlpunkt_bezug=zaehlpunkt_bezug,
                 zaehlpunkt_einspeisung=zaehlpunkt_einspeisung, trafo_id=trafo_id,
                 umspannwerk_id=umspannwerk_id, jahresverbrauch_kwh=jahresverbrauch_kwh, anlage_kwp=anlage_kwp,
                 wunschpartner=wunschpartner)
    fehler = []
    zp = normalisieren(zaehlpunkt)
    if not ist_gueltig(zp):
        fehler.append("Die Zählpunktnummer hat 33 Stellen und beginnt mit AT. Sie steht auf der Netzrechnung.")
    elif s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == zp)).first():
        fehler.append("Dieser Zählpunkt ist schon angemeldet.")
    # zweiter Zählpunkt: beim Erzeuger der Bezug, beim Abnehmer die Einspeisung einer eigenen PV-Anlage
    zweit_text = "Bezug" if rolle == Rolle.ERZEUGER else "Einspeisung"
    zweit = normalisieren(zaehlpunkt_bezug if rolle == Rolle.ERZEUGER else zaehlpunkt_einspeisung)
    if zweit and not ist_gueltig(zweit):
        fehler.append(f"Die Zählpunktnummer für die {zweit_text} stimmt nicht. Sie hat 33 Stellen und beginnt mit AT.")
    elif zweit and zweit == zp:
        fehler.append("Bezug und Einspeisung haben verschiedene Zählpunktnummern. Bitte beide prüfen.")
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
                   zaehlpunkt_bezug=(zweit or None) if rolle == Rolle.ERZEUGER else None,
                   zaehlpunkt_einspeisung=(zweit or None) if rolle == Rolle.ABNEHMER else None,
                   trafo_id=trafo_id.strip() or None, umspannwerk_id=umspannwerk_id.strip() or None,
                   profiltyp=profiltyp, jahresverbrauch_kwh=jahresverbrauch_kwh, anlage_kwp=anlage_kwp,
                   einladung_von=e.erzeuger_id if e else None, wunschpartner=wunschpartner.strip() or None,
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
    return _seite(request, "danke.html", t=t, link=einladungslink(s, t), bereich=kundenlink(t.id))


def einladungslink(s: Session, t: Teilnehmer) -> str:
    """Persönlicher Link für den Wunschnachbarn. Wer sich darüber anmeldet, wird zuerst zugeordnet."""
    e = s.exec(select(Einladung).where(Einladung.erzeuger_id == t.id)).first()
    if not e:
        e = Einladung(token=secrets.token_urlsafe(12), erzeuger_id=t.id)
        s.add(e)
        s.commit()
    return f"{config.BASIS_URL}/anmelden?einladung={e.token}"


# ---------------------------------------------------------------- Anfragen

@app.get("/gea", response_class=HTMLResponse)
def gea_info(request: Request):
    return _seite(request, "gea_info.html", fehler=None, werte={})


@app.post("/gea", response_class=HTMLResponse)
def gea_anfrage(request: Request, s: Session = Depends(sitzung), vorname: str = Form(...), nachname: str = Form(...),
                firma: str = Form(""), email: str = Form(...), telefon: str = Form(""), strasse: str = Form(...),
                plz: str = Form(...), ort: str = Form(...), wohnungen: int = Form(0), anlage_kwp: float = Form(0),
                anlage_zp: str = Form(""), registriert: str = Form("nein"), gemeinschafts_id: str = Form(""),
                modell: str = Form("selbst"), nachricht: str = Form(""), einwilligung: bool = Form(False)):
    werte = dict(vorname=vorname, nachname=nachname, firma=firma, email=email, telefon=telefon, strasse=strasse,
                 plz=plz, ort=ort, wohnungen=wohnungen, anlage_kwp=anlage_kwp, anlage_zp=anlage_zp,
                 registriert=registriert, gemeinschafts_id=gemeinschafts_id, modell=modell, nachricht=nachricht)
    anlage_zp = normalisieren(anlage_zp)
    if anlage_zp and not ist_gueltig(anlage_zp):
        return _seite(request, "gea_info.html", werte=werte,
                      fehler="Die Zählpunktnummer der Anlage stimmt nicht. Sie hat 33 Stellen und beginnt mit AT. "
                             "Wenn Sie sie nicht zur Hand haben, lassen Sie das Feld leer.")
    if not einwilligung:
        return _seite(request, "gea_info.html", werte=werte,
                      fehler="Bitte bestätigen Sie, dass wir Ihre Angaben für die Anfrage nutzen dürfen.")
    a = Anfrage(art="gea", vorname=vorname.strip(), nachname=nachname.strip(), firma=firma.strip() or None,
                email=email.strip(), telefon=telefon.strip() or None, strasse=strasse.strip(), plz=plz.strip(),
                ort=ort.strip(), wohnungen=wohnungen, anlage_kwp=anlage_kwp, anlage_zp=anlage_zp or None,
                registriert=registriert,
                gemeinschafts_id=normalisieren(gemeinschafts_id) or None,
                modell=modell if modell in ("selbst", "verwaltet") else "selbst",
                nachricht=nachricht.strip() or None, einwilligung_zeit=jetzt())
    s.add(a)
    s.commit()
    return _seite(request, "anfrage_danke.html", a=a)


@app.get("/impressum", response_class=HTMLResponse)
def impressum(request: Request):
    return _seite(request, "impressum.html", gepr=config.RECHTSTEXTE_GEPRUEFT)


@app.get("/datenschutz", response_class=HTMLResponse)
def datenschutz(request: Request):
    return _seite(request, "datenschutz.html", gepr=config.RECHTSTEXTE_GEPRUEFT)


@app.get("/anfrage", response_class=HTMLResponse)
def anfrage_formular(request: Request, art: str = "frage"):
    return _seite(request, "anfrage.html", art=art if art in ("p2p", "frage") else "frage", fehler=None, werte={})


@app.post("/anfrage", response_class=HTMLResponse)
def anfrage(request: Request, s: Session = Depends(sitzung), art: str = Form("frage"), vorname: str = Form(...),
            nachname: str = Form(...), email: str = Form(...), telefon: str = Form(""), ort: str = Form(""),
            nachricht: str = Form(...), einwilligung: bool = Form(False)):
    art = art if art in ("p2p", "frage") else "frage"
    if not einwilligung:
        return _seite(request, "anfrage.html", art=art, werte=dict(vorname=vorname, nachname=nachname, email=email,
                                                                  telefon=telefon, ort=ort, nachricht=nachricht),
                      fehler="Bitte bestätigen Sie, dass wir Ihre Angaben für die Antwort nutzen dürfen.")
    a = Anfrage(art=art, vorname=vorname.strip(), nachname=nachname.strip(), email=email.strip(),
                telefon=telefon.strip() or None, ort=ort.strip() or None, nachricht=nachricht.strip(),
                einwilligung_zeit=jetzt())
    s.add(a)
    s.commit()
    return _seite(request, "anfrage_danke.html", a=a)


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
    ip = _adresse(request)
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
    """Teilnehmer in einer laufenden P2P-Gruppe. Wohnungen einer GEA dürfen zusätzlich P2P machen."""
    offen = [g for g in s.exec(select(Gruppe).where(Gruppe.art == Art.P2P)).all() if g.status != GruppenStatus.BEENDET]
    ids = {g.erzeuger_id for g in offen}
    for g in offen:
        ids |= {m.teilnehmer_id for m in s.exec(select(Mitglied).where(Mitglied.gruppe_id == g.id)).all()}
    return ids


# ---------------------------------------------------------------- Verwaltung

@app.get("/verwaltung", response_class=HTMLResponse)
def uebersicht(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    gruppen = s.exec(select(Gruppe).order_by(Gruppe.erstellt.desc())).all()
    p2p = [g for g in gruppen if g.art == Art.P2P]
    geas = [g for g in gruppen if g.art == Art.GEA]
    teilnehmer = s.exec(select(Teilnehmer)).all()
    erinnerungen = _erinnerungen(s)
    aktiv = [g for g in gruppen if g.status == GruppenStatus.AKTIV]
    rechnungen = s.exec(select(Rechnung)).all()
    alle_gebuehren = s.exec(select(Gebuehr)).all()
    heute = date.today()
    gesamt = _auswertung_mehrere(s, aktiv)
    offen = [r for r in rechnungen if not r.bezahlt_am]
    belegt = _in_aktiver_gruppe(s)
    in_gea = {t.id for g in geas for t in importe.zaehlpunkte(s, g).values()}
    seit = (gesamt["bis"] if gesamt else heute) - timedelta(days=365)
    kennzahlen = {
        "aktive_gruppen": sum(1 for g in aktiv if g.art == Art.P2P),
        "aktive_gea": sum(1 for g in aktiv if g.art == Art.GEA),
        "zaehlpunkte_aktiv": sum(len(importe.zaehlpunkte(s, g)) for g in aktiv),
        "geteilt": gesamt["geteilt"] if gesamt else 0,
        "quote": gesamt["geteilt"] / gesamt["einspeisung"] * 100 if gesamt and gesamt["einspeisung"] else 0,
        "umsatz": sum(r.brutto for r in rechnungen if r.bis > seit),
        "offen": sum(r.brutto for r in offen),
        "ueberfaellig": sum(1 for r in offen if rechnung_status(r, heute) == "überfällig"),
        "postausgang": len(s.exec(select(Nachricht).where(Nachricht.gesendet_am == None)).all()),  # noqa: E711
        "frei": sum(1 for t in teilnehmer if t.id not in belegt and t.id not in in_gea),
        "anfragen": len(s.exec(select(Anfrage).where(Anfrage.status == "neu")).all()),
        "gebuehren": sum(x.netto for x in alle_gebuehren if x.bis > seit),
        "gebuehren_offen": sum(x.brutto for x in alle_gebuehren if not x.bezahlt_am),
        "uploads": len(s.exec(select(Upload)).all()),
    }
    faellig = []  # Gruppen und GEAs, für die eine Abrechnung möglich ist
    for g in aktiv:
        if g.art == Art.GEA and g.modell == Modell.SELBST and g.gebuehr_ct_kwh <= 0:
            continue  # der Betreiber rechnet selbst ab, für FIX ist nichts zu tun
        von, bis = abrechnungs_vorschlag(s, g)
        letzter = auswertung.letzter_tag(s, list(importe.zaehlpunkte(s, g)), g.id)
        was = "Servicegebühr" if g.art == Art.GEA and g.modell == Modell.SELBST else "Abrechnung"
        faellig.append((g, s.get(Teilnehmer, g.erzeuger_id), von, bis, bool(letzter and letzter >= bis), was))
    testmodus = any(t.email.endswith("@example.com") for t in teilnehmer)
    return _seite(request, "verwaltung.html", zeilen=[(g, s.get(Teilnehmer, g.erzeuger_id)) for g in p2p],
                  gea_zeilen=[(g, s.get(Teilnehmer, g.erzeuger_id), len(importe.mitglieder(s, g))) for g in geas],
                  teilnehmer=teilnehmer, k=kennzahlen, anzahl_erinnerungen=len(erinnerungen), gesamt=gesamt,
                  faellig=faellig, testmodus=testmodus,
                  erzeuger=sum(1 for t in teilnehmer if t.rolle == Rolle.ERZEUGER and t.id not in in_gea),
                  abnehmer=sum(1 for t in teilnehmer if t.rolle == Rolle.ABNEHMER and t.id not in in_gea))


def _auswertung_mehrere(s: Session, gruppen: list[Gruppe]) -> dict | None:
    """Geteilte Menge je Monat über mehrere Gruppen und GEAs, jede mit ihren eigenen Werten."""
    summe: dict[str, dict[str, float]] = {}
    bis = None
    for g in gruppen:
        e = s.get(Teilnehmer, g.erzeuger_id)
        ab = [t.zaehlpunkt for _, t in importe.mitglieder(s, g)]
        letzter = auswertung.letzter_tag(s, ab + [e.zaehlpunkt], g.id)
        if not letzter:
            continue
        bis = max(bis, letzter) if bis else letzter
        m = auswertung.monatswerte(s, ab + [e.zaehlpunkt], g.id)
        for k in {k for werte in m.values() for k in werte}:
            geteilt = sum(m.get(zp, {}).get(k, {}).get("eigendeckung", 0.0) for zp in ab)
            erzeugt = m.get(e.zaehlpunkt, {}).get(k, {}).get("erzeugung", 0.0)
            if not erzeugt:  # Anlage nicht im Export: aus den zugeteilten Anteilen
                erzeugt = max(sum(m.get(zp, {}).get(k, {}).get("anteil", 0.0) for zp in ab), geteilt)
            z = summe.setdefault(k, {"einspeisung": 0.0, "geteilt": 0.0})
            z["einspeisung"] += erzeugt
            z["geteilt"] += geteilt
    if not bis:
        return None
    schluessel = auswertung.mit_daten(auswertung.monatsschluessel(bis), {"alle": summe})
    geteilt = [summe.get(k, {}).get("geteilt", 0.0) for k in schluessel]
    rest = [max(0.0, summe.get(k, {}).get("einspeisung", 0.0) - g) for k, g in zip(schluessel, geteilt)]
    return {"bis": bis, "einspeisung": sum(geteilt) + sum(rest), "geteilt": sum(geteilt),
            "diagramm": diagramme.balken([diagramme.monatsname(k) for k in schluessel], [
                ("Geteilt bzw. im Haus genutzt", "--reihe-1", geteilt), ("Rest ins Netz eingespeist", "--reihe-grau", rest)])}


@app.get("/verwaltung/p2p", response_class=HTMLResponse)
def p2p_liste(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    """Alle P2P-Gruppen mit Zahlen und alle Angemeldeten, die noch keine Gruppe haben."""
    heute = date.today()
    zeilen = []
    for g in s.exec(select(Gruppe).where(Gruppe.art == Art.P2P).order_by(Gruppe.id)).all():
        e = s.get(Teilnehmer, g.erzeuger_id)
        abnehmer = [t for _, t in importe.mitglieder(s, g)]
        a = _auswertung(s, e, abnehmer, g.id)
        offen = [r for r in s.exec(select(Rechnung).where(Rechnung.gruppe_id == g.id)).all() if not r.bezahlt_am]
        naechste = abrechnungs_vorschlag(s, g) if g.status == GruppenStatus.AKTIV else None
        zeilen.append({"g": g, "e": e, "abnehmer": abnehmer, "a": a, "naechste": naechste,
                       "offen": sum(r.brutto for r in offen),
                       "ueberfaellig": sum(1 for r in offen if rechnung_status(r, heute) == "überfällig")})
    belegt = _in_aktiver_gruppe(s)
    in_gea = {t.id for g in s.exec(select(Gruppe).where(Gruppe.art == Art.GEA)).all()
              for t in importe.zaehlpunkte(s, g).values()}
    alle = s.exec(select(Teilnehmer).order_by(Teilnehmer.erstellt)).all()
    p2p_teilnehmer = [t for t in alle if t.id not in in_gea or t.id in belegt]
    frei = [t for t in p2p_teilnehmer if t.id not in belegt]
    namen = {t.id: t.name for t in alle}
    aktiv = [z for z in zeilen if z["g"].status == GruppenStatus.AKTIV]
    k = {"aktiv": len(aktiv), "in_arbeit": sum(1 for z in zeilen if z["g"].status not in
                                               (GruppenStatus.AKTIV, GruppenStatus.BEENDET)),
         "erzeuger": sum(1 for t in p2p_teilnehmer if t.rolle == Rolle.ERZEUGER),
         "abnehmer": sum(1 for t in p2p_teilnehmer if t.rolle == Rolle.ABNEHMER),
         "frei": len(frei), "geteilt": sum(z["a"]["geteilt"] for z in aktiv if z["a"]),
         "offen": sum(z["offen"] for z in zeilen), "ueberfaellig": sum(z["ueberfaellig"] for z in zeilen)}
    return _seite(request, "p2p_liste.html", zeilen=zeilen, frei=frei, k=k, namen=namen,
                  bereiche={t.id: kundenlink(t.id) for t in frei})


@app.get("/verwaltung/teilnehmer", response_class=HTMLResponse)
def teilnehmer_liste(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    liste = s.exec(select(Teilnehmer).order_by(Teilnehmer.erstellt)).all()
    return _seite(request, "teilnehmer.html", liste=liste, in_gruppe=_in_aktiver_gruppe(s),
                  bereiche={t.id: kundenlink(t.id) for t in liste})


@app.get("/verwaltung/matching", response_class=HTMLResponse)
def matching_seite(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    belegt = _in_aktiver_gruppe(s)
    alle = s.exec(select(Teilnehmer)).all()
    frei_abnehmer = [t for t in alle if t.rolle == Rolle.ABNEHMER and t.id not in belegt]
    vorschlaege, ohne = [], []
    for e in [t for t in alle if t.rolle == Rolle.ERZEUGER and t.id not in belegt]:
        # Eingeladene Wunschpartner zuerst berücksichtigen
        eingeladen = [a for a in frei_abnehmer if a.einladung_von == e.id or e.einladung_von == a.id]
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
               status=GruppenStatus.VERTRAG_OFFEN, gebuehr_ct_kwh=config.GEBUEHR_STANDARD_CT_KWH)
    s.add(g)
    s.commit()
    s.refresh(g)
    for a in abnehmer:
        s.add(Mitglied(gruppe_id=g.id, teilnehmer_id=a.id, erwartete_kwh_jahr=round(menge[a.id])))
    for t in [e] + abnehmer:
        s.add(nachrichten.unterschrift(t, g.id, unterschriftslink(g.id, t.id), kundenlink(t.id)))
    s.commit()
    return RedirectResponse(f"/verwaltung/gruppe/{g.id}", status_code=303)


def _auswertung(s: Session, erzeuger: Teilnehmer | None, abnehmer: list[Teilnehmer], gruppe_id: int | None = None,
                bis: date | None = None) -> dict | None:
    """Kennzahlen und Monatsdiagramm der letzten zwölf Monate mit Daten."""
    zps = ([erzeuger.zaehlpunkt] if erzeuger else []) + [a.zaehlpunkt for a in abnehmer]
    bis = bis or auswertung.letzter_tag(s, zps, gruppe_id)
    if not bis:
        return None
    m = auswertung.monatswerte(s, zps, gruppe_id)
    schluessel = auswertung.mit_daten(auswertung.monatsschluessel(bis), m)

    def reihe(zp_liste, feld):
        return [sum(m.get(zp, {}).get(k, {}).get(feld, 0.0) for zp in zp_liste) for k in schluessel]

    ab = [a.zaehlpunkt for a in abnehmer]
    verbrauch, geteilt = reihe(ab, "verbrauch"), reihe(ab, "eigendeckung")
    ergebnis = {"bis": bis, "monate": schluessel, "verbrauch": sum(verbrauch), "geteilt": sum(geteilt),
                "netzbezug": sum(verbrauch) - sum(geteilt)}
    beschriftung = [diagramme.monatsname(k) for k in schluessel]
    if erzeuger:
        einspeisung, rest = reihe([erzeuger.zaehlpunkt], "erzeugung"), reihe([erzeuger.zaehlpunkt], "restueberschuss")
        ergebnis.update(einspeisung=sum(einspeisung), rest=sum(rest))
        ergebnis["diagramm"] = diagramme.balken(beschriftung, [
            ("An Abnehmer geteilt", "--reihe-1", [e - r for e, r in zip(einspeisung, rest)]),
            ("Rest ins Netz eingespeist", "--reihe-grau", rest)])
    else:
        ergebnis["diagramm"] = diagramme.balken(beschriftung, [
            ("Sonnenstrom vom Nachbarn", "--reihe-1", geteilt),
            ("Strom vom eigenen Lieferanten", "--reihe-grau", [v - g for v, g in zip(verbrauch, geteilt)])])
    ergebnis["je_zp"] = {zp: {f: sum(m.get(zp, {}).get(k, {}).get(f, 0.0) for k in schluessel)
                              for f in energiedaten.FELDER} for zp in zps}
    return ergebnis


def _tagesdiagramm(s: Session, erzeuger: Teilnehmer, abnehmer: list[Teilnehmer], tag: date,
                   gruppe_id: int | None = None) -> str:
    werte = auswertung.tageswerte(s, [erzeuger.zaehlpunkt] + [a.zaehlpunkt for a in abnehmer], tag, gruppe_id)
    summe = lambda feld: [sum(werte[a.zaehlpunkt][feld][q] for a in abnehmer) for q in range(96)]  # noqa: E731
    return diagramme.tageskurve([
        ("Einspeisung Erzeuger", "--reihe-3", werte[erzeuger.zaehlpunkt]["erzeugung"], False),
        ("Verbrauch der Abnehmer", "--reihe-2", summe("verbrauch"), False),
        ("Geteilt", "--reihe-1", summe("eigendeckung"), True)])


@app.get("/verwaltung/gruppe/{gid}", response_class=HTMLResponse)
def gruppe_seite(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
                 tag: date | None = None):
    g = s.get(Gruppe, gid) or _404()
    if g.art == Art.GEA:
        return RedirectResponse(f"/verwaltung/gea/{gid}", status_code=303)
    d = _gruppen_daten(s, g)
    abnehmer = [t for _, t in d["abnehmer"]]
    a = _auswertung(s, d["erzeuger"], abnehmer, g.id)
    tagesdiagramm = None
    if a:
        tag = tag or auswertung.bester_tag(s, d["erzeuger"].zaehlpunkt, a["monate"][max(0, len(a["monate"]) - 4)],
                                           g.id) or a["bis"]
        if auswertung.hat_viertelstunden(s, [d["erzeuger"].zaehlpunkt] + [t.zaehlpunkt for t in abnehmer], g.id):
            tagesdiagramm = _tagesdiagramm(s, d["erzeuger"], abnehmer, tag, g.id)
    vorschlag = abrechnungs_vorschlag(s, g)
    alle = [d["erzeuger"]] + [t for _, t in d["abnehmer"]]
    unterschriften = {u.teilnehmer_id for u in s.exec(select(Unterschrift).where(
        Unterschrift.gruppe_id == gid, Unterschrift.dokument == "vertrag")).all()}
    links = {t.id: unterschriftslink(gid, t.id) for t in alle}
    bereiche = {t.id: kundenlink(t.id) for t in alle}
    zustimmungen = {z.teilnehmer_id: z for z in s.exec(select(Zustimmung).where(Zustimmung.gruppe_id == gid)).all()}
    rechnungen = s.exec(select(Rechnung).where(Rechnung.gruppe_id == gid).order_by(Rechnung.bis.desc())).all()
    namen = {t.id: t.name for t in alle}
    gebuehr_liste = s.exec(select(Gebuehr).where(Gebuehr.gruppe_id == gid).order_by(Gebuehr.bis.desc())).all()
    return _seite(request, "gruppe.html", g=g, alle=alle, unterschriften=unterschriften, links=links,
                  zustimmungen=zustimmungen, rechnungen=rechnungen, stati=list(GruppenStatus), namen=namen,
                  bereiche=bereiche, a=a, tag=tag, tagesdiagramm=tagesdiagramm, vorschlag=vorschlag,
                  gebuehren=gebuehr_liste, frueheste_aenderung=date.today() + timedelta(days=31), **d)


@app.post("/verwaltung/gruppe/{gid}")
def gruppe_aendern(gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
                   status: GruppenStatus = Form(...), preis_ct_kwh: float = Form(0), ust_satz: float = Form(0),
                   rechnung_monatlich: bool = Form(False), marktpartner_id: str = Form(""),
                   gemeinschafts_id: str = Form(""), notiz: str = Form(""), gebuehr_ct_kwh: float = Form(0)):
    g = s.get(Gruppe, gid) or _404()
    if status == GruppenStatus.REGISTRIERT and not (marktpartner_id.strip() and gemeinschafts_id.strip()):
        raise HTTPException(400, "Für „registriert“ bitte Marktpartner-ID und Gemeinschafts-ID eintragen.")
    if preis_ct_kwh != g.preis_ct_kwh and g.status == GruppenStatus.AKTIV:
        raise HTTPException(400, "In einer aktiven Gruppe ändert sich der Preis nur über „Preisänderung ankündigen“.")
    g.status, g.preis_ct_kwh, g.ust_satz = status, preis_ct_kwh, ust_satz
    g.rechnung_monatlich = rechnung_monatlich
    g.gebuehr_ct_kwh = max(0.0, gebuehr_ct_kwh)
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
        r = rechnung_anlegen(s, g, t, z.von, z.bis, z.kwh)
        if not r:
            uebersprungen.append(f"{abrechnung.rechnungsnummer(g.id, t.id, z.bis)} (gibt es schon)")
            continue
        neu += 1
    s.commit()
    return _seite(request, "abrechnung_ergebnis.html", g=g, neu=neu, uebersprungen=uebersprungen, rechnungen=[])


def rechnung_anlegen(s: Session, g: Gruppe, t: Teilnehmer, von: date, bis: date, kwh: float,
                     datum: date | None = None, mail: bool = True) -> Rechnung | None:
    """Eine Rechnung im Namen des Erzeugers. None, wenn es sie für den Zeitraum schon gibt."""
    nummer = abrechnung.rechnungsnummer(g.id, t.id, bis)
    if s.exec(select(Rechnung).where(Rechnung.nummer == nummer)).first():
        return None
    preis = preis_fuer(g, von)
    b = abrechnung.berechnen(kwh, preis, g.ust_satz)
    datum = datum or date.today()
    r = Rechnung(nummer=nummer, gruppe_id=g.id, abnehmer_id=t.id, von=von, bis=bis, kwh=round(kwh, 3),
                 preis_ct_kwh=preis, ust_satz=g.ust_satz, netto=b.netto, ust=b.ust,
                 brutto=b.brutto, datum=datum, faellig=abrechnung.faelligkeit(datum))
    s.add(r)
    s.flush()
    if mail:
        s.add(nachrichten.rechnung(t, g.id, nummer, _euro(b.brutto), r.faellig,
                                   f"{kundenlink(t.id)}/rechnung/{r.id}"))
    return r


def gebuehr_anlegen(s: Session, g: Gruppe, von: date, bis: date, kwh: float, datum: date | None = None,
                    mail: bool = True) -> Gebuehr | None:
    x = gebuehren.anlegen(s, g, von, bis, kwh, datum)
    if x and mail:
        e = s.get(Teilnehmer, g.erzeuger_id)
        s.add(nachrichten.gebuehr(e, g.id, x.nummer, _euro(x.brutto), x.faellig,
                                  f"{kundenlink(e.id)}/gebuehr/{x.id}", g.titel))
    return x


def abrechnungs_vorschlag(s: Session, g: Gruppe) -> tuple[date, date]:
    """Nächster offener Zeitraum: ab dem Tag nach der letzten Rechnung, ein Monat oder ein Jahr."""
    letzte = s.exec(select(Rechnung.bis).where(Rechnung.gruppe_id == g.id).order_by(Rechnung.bis.desc())).first()
    if g.art == Art.GEA and g.modell == Modell.SELBST:  # FIX stellt nur die Servicegebühr in Rechnung
        letzte = s.exec(select(Gebuehr.bis).where(Gebuehr.gruppe_id == g.id).order_by(Gebuehr.bis.desc())).first()
    erster = auswertung.erster_tag(s, list(importe.zaehlpunkte(s, g)), g.id)
    von = letzte + timedelta(days=1) if letzte else (g.aktiv_seit or erster or date.today().replace(day=1))
    if g.rechnung_monatlich:
        bis = energiedaten.monate(von, von)[0][1]
    else:
        bis = von.replace(year=von.year + 1) - timedelta(days=1)
    return von, bis


@app.post("/verwaltung/gruppe/{gid}/abrechnungslauf", response_class=HTMLResponse)
def abrechnungslauf(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
                    von: date = Form(...), bis: date = Form(...), nur_gebuehr: bool = Form(False)):
    """Rechnungen für alle Abnehmer bzw. Wohnungen direkt aus den gespeicherten Energiedaten,
    dazu die Servicegebühr von FIX. Bei einer GEA in Selbstverwaltung nur die Servicegebühr."""
    g = s.get(Gruppe, gid) or _404()
    d = _gruppen_daten(s, g)
    if bis < von:
        raise HTTPException(400, "Das Ende liegt vor dem Beginn.")
    abnehmer = [t for _, t in d["abnehmer"]]
    werte = auswertung.summen(s, [t.zaehlpunkt for t in abnehmer], von, bis, g.id)
    soll = (bis - von).days + 1
    fehlend = [t for t in abnehmer if werte[t.zaehlpunkt]["tage"] < soll]
    if g.art == Art.GEA:  # Wohnungen ganz ohne Werte (z. B. abgemeldet) blockieren die Abrechnung nicht
        ohne = [t for t in fehlend if werte[t.zaehlpunkt]["tage"] == 0]
        if len(ohne) < len(abnehmer):
            fehlend = [t for t in fehlend if t not in ohne]
            abnehmer = [t for t in abnehmer if t not in ohne]
    if fehlend:
        luecken = _fehlende_monate(s, [t.zaehlpunkt for t in fehlend], von, bis, g.id)
        return _seite(request, "abrechnung_ergebnis.html", g=g, neu=0, uebersprungen=[], rechnungen=[],
                      fehler=f"Für den Zeitraum fehlen Energiedaten ({', '.join(luecken)}). Bitte zuerst den "
                             f"EDA-Export dafür unter „Energiedaten“ hochladen.")
    neu, uebersprungen = [], []
    nur_gebuehr = nur_gebuehr or (g.art == Art.GEA and g.modell == Modell.SELBST)
    if not nur_gebuehr:
        for t in abnehmer:
            r = rechnung_anlegen(s, g, t, von, bis, werte[t.zaehlpunkt]["eigendeckung"])
            if r:
                neu.append(r)
            else:
                uebersprungen.append(f"{t.name}: Rechnung für diesen Zeitraum gibt es schon")
    geteilt = sum(werte[t.zaehlpunkt]["eigendeckung"] for t in abnehmer)
    gebuehr = gebuehr_anlegen(s, g, von, bis, geteilt)
    if not gebuehr and g.gebuehr_ct_kwh > 0 and geteilt > 0:
        uebersprungen.append(f"Servicegebühr {gebuehren.nummer(g, bis)} gibt es schon")
    s.commit()
    namen = {t.id: t.name for t in abnehmer}
    return _seite(request, "abrechnung_ergebnis.html", g=g, neu=len(neu), uebersprungen=uebersprungen,
                  rechnungen=neu, namen=namen, gebuehr=gebuehr)


def _fehlende_monate(s: Session, zaehlpunkte: list[str], von: date, bis: date, gruppe_id: int | None = None) -> list[str]:
    luecken = []
    for erster, letzter in energiedaten.monate(von, bis):
        a, b = max(erster, von), min(letzter, bis)
        werte = auswertung.summen(s, zaehlpunkte, a, b, gruppe_id)
        if any(w["tage"] < (b - a).days + 1 for w in werte.values()):
            luecken.append(diagramme.monatsname(f"{erster:%Y-%m}"))
    return luecken


@app.get("/verwaltung/rechnung/{rid}", response_class=HTMLResponse)
def rechnung(request: Request, rid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    r = s.get(Rechnung, rid) or _404()
    return _seite(request, "rechnung.html", **_rechnung_daten(s, r))


def _rechnung_daten(s: Session, r: Rechnung) -> dict:
    g = s.get(Gruppe, r.gruppe_id)  # bei einer GEA ist der Erzeuger der Betreiber
    erzeuger = s.get(Teilnehmer, g.erzeuger_id)
    qr = None
    if erzeuger.iban and r.brutto > 0:
        qr = zahlung.qr_svg(erzeuger.name, erzeuger.iban, r.brutto, f"Rechnung {r.nummer}")
    return {"r": r, "g": g, "abnehmer": s.get(Teilnehmer, r.abnehmer_id), "erzeuger": erzeuger, "qr": qr,
            "iban": zahlung.iban_lesbar(erzeuger.iban) if erzeuger.iban else None}


@app.get("/verwaltung/rechnungen", response_class=HTMLResponse)
def rechnungen_seite(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung), filter: str = ""):
    liste = s.exec(select(Rechnung).order_by(Rechnung.datum.desc(), Rechnung.nummer)).all()
    heute = date.today()
    if filter:
        liste = [r for r in liste if rechnung_status(r, heute) == filter]
    namen = {t.id: t.name for t in s.exec(select(Teilnehmer)).all()}
    alle = s.exec(select(Rechnung)).all()
    summen = {st: sum(r.brutto for r in alle if rechnung_status(r, heute) == st)
              for st in ("offen", "überfällig", "bezahlt")}
    gruppen = {g.id: g for g in s.exec(select(Gruppe)).all()}
    gebuehr_liste = s.exec(select(Gebuehr).order_by(Gebuehr.datum.desc(), Gebuehr.nummer)).all()
    if filter:
        gebuehr_liste = [x for x in gebuehr_liste if rechnung_status(x, heute) == filter]
    return _seite(request, "rechnungen.html", liste=liste, namen=namen, filter=filter, summen=summen,
                  gruppen=gruppen, gebuehren=gebuehr_liste)


@app.get("/verwaltung/gebuehr/{gid}", response_class=HTMLResponse)
def gebuehr_seite(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    x = s.get(Gebuehr, gid) or _404()
    return _seite(request, "gebuehr.html", zurueck=None, **_gebuehr_daten(s, x))


def _gebuehr_daten(s: Session, x: Gebuehr) -> dict:
    g = s.get(Gruppe, x.gruppe_id)
    qr = None
    iban = config.DIENSTLEISTER.get("iban")
    if iban and zahlung.iban_gueltig(iban) and x.brutto > 0:
        qr = zahlung.qr_svg(config.DIENSTLEISTER["firma"], iban, x.brutto, f"Rechnung {x.nummer}")
    return {"x": x, "g": g, "empfaenger": s.get(Teilnehmer, x.empfaenger_id), "qr": qr,
            "iban": zahlung.iban_lesbar(iban) if iban and zahlung.iban_gueltig(iban) else None}


@app.post("/verwaltung/gebuehr/{gid}/bezahlt")
def gebuehr_bezahlt(gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung), zurueck: str = Form("")):
    x = s.get(Gebuehr, gid) or _404()
    x.bezahlt_am = None if x.bezahlt_am else date.today()
    s.add(x)
    s.commit()
    return RedirectResponse(_sicher_zurueck(zurueck), status_code=303)


@app.post("/verwaltung/rechnung/{rid}/bezahlt")
def rechnung_bezahlt(rid: int, s: Session = Depends(sitzung), _=Depends(verwaltung), zurueck: str = Form("")):
    r = s.get(Rechnung, rid) or _404()
    r.bezahlt_am = None if r.bezahlt_am else date.today()
    s.add(r)
    s.commit()
    return RedirectResponse(_sicher_zurueck(zurueck), status_code=303)


@app.post("/verwaltung/rechnung/{rid}/mahnung")
def rechnung_mahnung(rid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    r = s.get(Rechnung, rid) or _404()
    t = s.get(Teilnehmer, r.abnehmer_id)
    s.add(nachrichten.zahlungserinnerung(t, r.gruppe_id, r.nummer, _euro(r.brutto), r.faellig,
                                         f"{kundenlink(t.id)}/rechnung/{r.id}"))
    r.gemahnt_am = date.today()
    s.add(r)
    s.commit()
    return RedirectResponse("/verwaltung/postausgang", status_code=303)


def _sicher_zurueck(ziel: str) -> str:
    return ziel if ziel.startswith("/verwaltung") else "/verwaltung/rechnungen"


@app.get("/verwaltung/energiedaten", response_class=HTMLResponse)
def energiedaten_seite(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    return _seite(request, "energiedaten.html", ergebnisse=None, **_energiedaten_stand(s))


@app.post("/verwaltung/energiedaten", response_class=HTMLResponse)
async def energiedaten_hochladen(request: Request, dateien: list[UploadFile], s: Session = Depends(sitzung),
                                 _=Depends(verwaltung), ziel: int = Form(0)):
    ergebnisse = []
    for datei in dateien:
        ergebnisse.append(import_verarbeiten(s, await datei.read(), datei.filename or "Datei", ziel or None))
        s.commit()
    return _seite(request, "energiedaten.html", ergebnisse=ergebnisse, **_energiedaten_stand(s))


def import_verarbeiten(s: Session, inhalt: bytes, dateiname: str, ziel_gid: int | None = None,
                       aktion: str = "import", angaben: dict | None = None, von_tid: int | None = None) -> dict:
    """Einen Export lesen und speichern. Unbekannte Spalten oder eine unklare Gemeinschaft:
    Datei parken und nachfragen, statt zu raten."""
    try:
        imp = importe.einlesen(s, inhalt, dateiname)
    except ValueError as e:
        return {"datei": dateiname, "fehler": str(e), "bericht": eda_import.aufbau_bericht(inhalt, dateiname)}

    def parken() -> Upload:
        u = Upload(token=secrets.token_urlsafe(12), dateiname=dateiname, inhalt=inhalt, aktion=aktion,
                   ziel_gruppe_id=ziel_gid, angaben=json.dumps(angaben) if angaben else None,
                   von_teilnehmer_id=von_tid)
        s.add(u)
        s.flush()
        return u

    basis = {"datei": dateiname, "von": imp.von, "bis": imp.bis, "aufbau": imp.aufbau,
             "gemeinschafts_id": imp.gemeinschafts_id, "anzahl_zp": len(imp.zaehlpunkte)}
    if imp.unbekannt:
        return {**basis, "zuordnen": parken().token, "unbekannt": list(imp.unbekannt)}
    if aktion == "gea_neu":
        g, ergebnis = importe.gea_anlegen(s, imp, angaben or {})
        betreiber = s.get(Teilnehmer, g.erzeuger_id)
        if betreiber.email:
            s.add(nachrichten.willkommen_gea(betreiber, g.id, g.titel, kundenlink(betreiber.id)))
        return {**basis, **ergebnis, "gea_neu": g, "erkannt": "neu angelegt"}
    g = s.get(Gruppe, ziel_gid) if ziel_gid else None
    erkannt = "ausgewählt"
    if not g:
        g, erkannt = importe.ziel_finden(s, imp)
    if not g:
        return {**basis, "ohne_ziel": parken().token, "grund": erkannt}
    return {**basis, **importe.speichern(s, g, imp), "erkannt": erkannt}


@app.get("/verwaltung/import/{token}", response_class=HTMLResponse)
def import_zuordnen_seite(request: Request, token: str, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    u = s.exec(select(Upload).where(Upload.token == token)).first() or _404()
    imp = importe.einlesen(s, u.inhalt, u.dateiname)
    bekannt = sorted({(sp, feld) for sp, feld in _erkannte_spalten(imp)})
    gruppen = s.exec(select(Gruppe).where(Gruppe.status != GruppenStatus.BEENDET)).all()
    return _seite(request, "import_zuordnen.html", u=u, imp=imp, bekannt=bekannt, gruppen=gruppen,
                  felder=list(eda_import.FELD_TEXT.items()))


def _erkannte_spalten(imp) -> list[tuple[str, str]]:
    felder = [f for f in eda_import.FELDER if any(w[f] for w in imp.werte)]
    return [(eda_import.FELD_TEXT[f], f) for f in felder]


@app.post("/verwaltung/import/{token}", response_class=HTMLResponse)
async def import_zuordnen(request: Request, token: str, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    u = s.exec(select(Upload).where(Upload.token == token)).first() or _404()
    formular = await request.form()
    imp = importe.einlesen(s, u.inhalt, u.dateiname)
    for i, titel in enumerate(imp.unbekannt):
        feld = str(formular.get(f"feld_{i}", ""))
        if feld in eda_import.FELD_TEXT:
            importe.zuordnung_merken(s, titel, feld)
    s.flush()
    ziel = int(formular.get("ziel") or 0) or u.ziel_gruppe_id
    if formular.get("neue_gea"):
        return RedirectResponse(f"/verwaltung/gea/neu?upload={u.token}", status_code=303)
    ergebnis = import_verarbeiten(s, u.inhalt, u.dateiname, ziel, u.aktion,
                                  json.loads(u.angaben) if u.angaben else None, u.von_teilnehmer_id)
    s.delete(u)
    s.commit()
    if ergebnis.get("gea_neu"):
        return RedirectResponse(f"/verwaltung/gea/{ergebnis['gea_neu'].id}", status_code=303)
    return _seite(request, "energiedaten.html", ergebnisse=[ergebnis], **_energiedaten_stand(s))


@app.post("/verwaltung/import/{token}/verwerfen")
def import_verwerfen(token: str, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    u = s.exec(select(Upload).where(Upload.token == token)).first() or _404()
    s.delete(u)
    s.commit()
    return RedirectResponse("/verwaltung/energiedaten", status_code=303)


def _energiedaten_stand(s: Session) -> dict:
    stand = auswertung.abdeckung(s)
    letzter = max((m for werte in stand.values() for m in werte), default=f"{date.today():%Y-%m}")
    monate = auswertung.monatsschluessel(date.fromisoformat(letzter + "-01"))
    soll = {k: auswertung.tage_im_monat(k) for k in monate}
    zeilen = []  # je Gemeinschaft: wie viele Zählpunkte je Monat vollständig bzw. teilweise da sind
    for g in s.exec(select(Gruppe).where(Gruppe.status != GruppenStatus.BEENDET).order_by(Gruppe.id)).all():
        zps = [zp for zp in importe.zaehlpunkte(s, g) if not zp.startswith(importe.OHNE_ZP)]
        je_monat = {}
        for m in monate:
            tage = [stand.get((g.id, zp), {}).get(m, 0) for zp in zps]
            je_monat[m] = (sum(1 for n in tage if n >= soll[m]), sum(1 for n in tage if 0 < n < soll[m]))
        zeilen.append((g, len(zps), je_monat))
    testdateien = sorted(p.name for p in config.TESTDATEN.glob("*.xlsx")) if config.TESTDATEN.exists() else []
    uploads = s.exec(select(Upload).order_by(Upload.erstellt)).all()
    gruppen = s.exec(select(Gruppe).where(Gruppe.status != GruppenStatus.BEENDET).order_by(Gruppe.id)).all()
    return {"zeilen": zeilen, "monate": monate, "soll": soll, "testdateien": testdateien, "uploads": uploads,
            "gruppen": gruppen,
            "von_kunde": {u.id: s.get(Teilnehmer, u.von_teilnehmer_id) for u in uploads if u.von_teilnehmer_id}}


@app.get("/verwaltung/testdaten/{name}")
def testdatei(name: str, _=Depends(verwaltung)):
    erlaubt = {p.name: p for p in config.TESTDATEN.glob("*.xlsx")} if config.TESTDATEN.exists() else {}
    if name not in erlaubt:
        raise HTTPException(404)
    return FileResponse(erlaubt[name], filename=name)


# ---------------------------------------------------------------- GEA

@app.get("/verwaltung/gea", response_class=HTMLResponse)
def gea_liste(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    zeilen = []
    for g in s.exec(select(Gruppe).where(Gruppe.art == Art.GEA).order_by(Gruppe.id)).all():
        zeilen.append((g, s.get(Teilnehmer, g.erzeuger_id), len(importe.mitglieder(s, g)), gea.auswerten(s, g)))
    return _seite(request, "gea_liste.html", zeilen=zeilen)


_GEA_FELDER = ("name", "vorname", "nachname", "firma", "email", "telefon", "strasse", "plz", "ort", "anlage_kwp",
               "anlage_zp", "gemeinschafts_id", "modell", "aufteilung", "preis_ct_kwh", "ust_satz",
               "rechnung_monatlich", "gebuehr_ct_kwh", "status", "notiz")


@app.get("/verwaltung/gea/neu", response_class=HTMLResponse)
def gea_neu_seite(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung), upload: str = "",
                  anfrage: int = 0):
    werte = {"modell": "selbst", "aufteilung": "dynamisch", "gebuehr_ct_kwh": config.GEBUEHR_STANDARD_CT_KWH,
             "preis_ct_kwh": 0, "status": "aktiv", "rechnung_monatlich": True}
    a = s.get(Anfrage, anfrage) if anfrage else None
    if a:
        werte.update(vorname=a.vorname, nachname=a.nachname, firma=a.firma or "", email=a.email,
                     telefon=a.telefon or "", strasse=a.strasse or "", plz=a.plz or "", ort=a.ort or "",
                     anlage_kwp=a.anlage_kwp, anlage_zp=a.anlage_zp or "", gemeinschafts_id=a.gemeinschafts_id or "",
                     modell=a.modell or "selbst",
                     name=f"GEA {a.strasse}, {a.ort}" if a.strasse else "", status="vorschlag")
    erkannt = None
    u = s.exec(select(Upload).where(Upload.token == upload)).first() if upload else None
    if u:
        try:
            imp = importe.einlesen(s, u.inhalt, u.dateiname)
            erkannt = {"imp": imp, "anlage": sorted(imp.erzeuger)}
            werte["gemeinschafts_id"] = imp.gemeinschafts_id or werte.get("gemeinschafts_id", "")
        except ValueError:
            u = None
    return _seite(request, "gea_neu.html", werte=werte, upload=u, erkannt=erkannt, anfrage=a, fehler=None,
                  stati=GEA_STATUS)


@app.post("/verwaltung/gea/neu", response_class=HTMLResponse)
async def gea_neu(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    formular = await request.form()
    angaben = {f: str(formular.get(f, "") or "") for f in _GEA_FELDER}
    angaben["rechnung_monatlich"] = bool(formular.get("rechnung_monatlich"))
    anfrage = s.get(Anfrage, int(formular.get("anfrage_id") or 0)) if formular.get("anfrage_id") else None
    datei = formular.get("datei")
    u = s.exec(select(Upload).where(Upload.token == str(formular.get("upload", "")))).first() \
        if formular.get("upload") else None
    fehler = []
    if not angaben["name"].strip():
        fehler.append("Bitte einen Namen für die GEA angeben, z. B. die Adresse des Hauses.")
    if angaben["anlage_zp"] and not ist_gueltig(angaben["anlage_zp"]):
        fehler.append("Der Zählpunkt der Anlage hat 33 Stellen und beginnt mit AT.")
    inhalt, dateiname = None, ""
    if hasattr(datei, "read") and getattr(datei, "filename", ""):
        inhalt, dateiname = await datei.read(), datei.filename
    elif u:
        inhalt, dateiname = u.inhalt, u.dateiname
    if fehler:
        return _seite(request, "gea_neu.html", werte=angaben, upload=u, erkannt=None, anfrage=anfrage,
                      fehler=fehler, stati=GEA_STATUS)
    if inhalt is None:
        g, _e = importe.gea_anlegen(s, None, angaben)
        betreiber = s.get(Teilnehmer, g.erzeuger_id)
        if betreiber.email:
            s.add(nachrichten.willkommen_gea(betreiber, g.id, g.titel, kundenlink(betreiber.id)))
    else:
        ergebnis = import_verarbeiten(s, inhalt, dateiname, aktion="gea_neu", angaben=angaben)
        if u:
            s.delete(u)
        if ergebnis.get("fehler"):
            s.rollback()
            return _seite(request, "gea_neu.html", werte=angaben, upload=None, erkannt=None, anfrage=anfrage,
                          fehler=[f"{dateiname}: {ergebnis['fehler']}"], bericht=ergebnis["bericht"], stati=GEA_STATUS)
        if ergebnis.get("zuordnen"):
            s.commit()
            return RedirectResponse(f"/verwaltung/import/{ergebnis['zuordnen']}", status_code=303)
        g = ergebnis["gea_neu"]
    if anfrage:
        anfrage.gruppe_id, anfrage.status = g.id, "in_bearbeitung"
        s.add(anfrage)
    s.commit()
    return RedirectResponse(f"/verwaltung/gea/{g.id}", status_code=303)


@app.get("/verwaltung/gea/{gid}", response_class=HTMLResponse)
def gea_seite(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung),
              tag: date | None = None):
    g = s.get(Gruppe, gid) or _404()
    if g.art != Art.GEA:
        return RedirectResponse(f"/verwaltung/gruppe/{gid}", status_code=303)
    betreiber = s.get(Teilnehmer, g.erzeuger_id)
    a = gea.auswerten(s, g)
    tagesdiagramm = None
    if a:
        if not tag:
            monat = a["monate"][max(0, len(a["monate"]) - 4)]
            tag = auswertung.bester_tag(s, betreiber.zaehlpunkt, monat, g.id) or _bester_gea_tag(s, g, monat) or a["bis"]
        if auswertung.hat_viertelstunden(s, list(importe.zaehlpunkte(s, g)), g.id):
            tagesdiagramm = gea.tagesdiagramm(s, g, tag)
    wohnungen = importe.mitglieder(s, g)
    rechnungen = s.exec(select(Rechnung).where(Rechnung.gruppe_id == gid).order_by(Rechnung.bis.desc())).all()
    gebuehr_liste = s.exec(select(Gebuehr).where(Gebuehr.gruppe_id == gid).order_by(Gebuehr.bis.desc())).all()
    anfrage = s.exec(select(Anfrage).where(Anfrage.gruppe_id == gid)).first()
    return _seite(request, "gea.html", g=g, betreiber=betreiber, a=a, tag=tag, tagesdiagramm=tagesdiagramm,
                  wohnungen=wohnungen, rechnungen=rechnungen, gebuehren=gebuehr_liste,
                  namen={t.id: t.name for _, t in wohnungen}, vorschlag=abrechnungs_vorschlag(s, g),
                  zeitraeume=gea.zeitraeume(s, g), bereich=kundenlink(betreiber.id),
                  bereiche={t.id: kundenlink(t.id) for _, t in wohnungen}, stati=GEA_STATUS, anfrage=anfrage,
                  ohne_zp=betreiber.zaehlpunkt.startswith(importe.OHNE_ZP),
                  frueheste_aenderung=date.today() + timedelta(days=31))


def _bester_gea_tag(s: Session, g: Gruppe, monat: str) -> date | None:
    from sqlalchemy import func
    tag = func.substr(Messwert.beginn, 1, 10)
    zeile = s.exec(select(tag, func.sum(Messwert.eigendeckung)).where(Messwert.gruppe_id == g.id,
                                                                      Messwert.ende == None,  # noqa: E711
                                                                      Messwert.beginn.like(f"{monat}%"))
                   .group_by(tag).order_by(func.sum(Messwert.eigendeckung).desc()).limit(1)).first()
    return date.fromisoformat(zeile[0]) if zeile else None


@app.post("/verwaltung/gea/{gid}")
async def gea_aendern(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    f = await request.form()
    betreiber = s.get(Teilnehmer, g.erzeuger_id)
    preis = float(f.get("preis_ct_kwh") or 0)
    modell = Modell(str(f.get("modell") or g.modell.value))
    if preis != g.preis_ct_kwh and g.status == GruppenStatus.AKTIV and modell == Modell.VERWALTET \
            and s.exec(select(Rechnung).where(Rechnung.gruppe_id == gid)).first():
        raise HTTPException(400, "Nach der ersten Rechnung ändert sich der Preis nur über „Preisänderung ankündigen“.")
    anlage_zp = normalisieren(str(f.get("anlage_zp", "")))
    if anlage_zp and anlage_zp != betreiber.zaehlpunkt:
        if not ist_gueltig(anlage_zp):
            raise HTTPException(400, "Der Zählpunkt der Anlage hat 33 Stellen und beginnt mit AT.")
        if s.exec(select(Teilnehmer).where(Teilnehmer.zaehlpunkt == anlage_zp)).first():
            raise HTTPException(400, "Dieser Zählpunkt ist schon bei einem anderen Teilnehmer eingetragen.")
        s.exec(text("UPDATE messwert SET zaehlpunkt = :neu WHERE zaehlpunkt = :alt AND gruppe_id = :g")
               .bindparams(neu=anlage_zp, alt=betreiber.zaehlpunkt, g=g.id))
        betreiber.zaehlpunkt, betreiber.netzbetreiber = anlage_zp, anlage_zp[2:8]
    g.name = str(f.get("name", "")).strip() or g.name
    g.status = GruppenStatus(str(f.get("status") or g.status.value))
    g.modell, g.aufteilung = modell, str(f.get("aufteilung") or g.aufteilung)
    g.preis_ct_kwh, g.ust_satz = preis, float(f.get("ust_satz") or 0)
    g.rechnung_monatlich = bool(f.get("rechnung_monatlich"))
    g.gebuehr_ct_kwh = max(0.0, float(f.get("gebuehr_ct_kwh") or 0))
    g.gemeinschafts_id = normalisieren(str(f.get("gemeinschafts_id", ""))) or None
    g.marktpartner_id = str(f.get("marktpartner_id", "")).strip() or None
    g.notiz = str(f.get("notiz", "")).strip() or None
    for feld in ("vorname", "nachname", "firma", "email", "telefon", "strasse", "plz", "ort"):
        if feld in f:
            wert = str(f.get(feld, "")).strip()
            setattr(betreiber, feld, wert or (None if feld in ("firma", "telefon") else ""))
    betreiber.anlage_kwp = float(f.get("anlage_kwp") or betreiber.anlage_kwp or 0)
    if g.status == GruppenStatus.AKTIV and not g.aktiv_seit:
        g.aktiv_seit = date.today()
    s.add(g)
    s.add(betreiber)
    s.commit()
    return RedirectResponse(f"/verwaltung/gea/{gid}", status_code=303)


@app.post("/verwaltung/gea/{gid}/wohnungen")
async def gea_wohnungen(request: Request, gid: int, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    f = await request.form()
    for m, t in importe.mitglieder(s, g):
        if f"wohnung_{t.id}" not in f:
            continue
        t.wohnung = str(f.get(f"wohnung_{t.id}", "")).strip() or None
        t.vorname = str(f.get(f"vorname_{t.id}", "")).strip()
        t.nachname = str(f.get(f"nachname_{t.id}", "")).strip()
        t.email = str(f.get(f"email_{t.id}", "")).strip()
        anteil = str(f.get(f"anteil_{t.id}", "")).replace(",", ".").strip()
        m.anteil_prozent = float(anteil) if anteil else None
        s.add(t)
        s.add(m)
    neu_zp = normalisieren(str(f.get("neu_zaehlpunkt", "")))
    if neu_zp:
        if not ist_gueltig(neu_zp):
            raise HTTPException(400, "Der Zählpunkt hat 33 Stellen und beginnt mit AT.")
        importe.wohnung_anlegen(s, g, neu_zp, str(f.get("neu_wohnung", "")).strip() or None)
    s.commit()
    return RedirectResponse(f"/verwaltung/gea/{gid}#wohnungen", status_code=303)


@app.get("/verwaltung/gea/{gid}/grundlage")
def gea_grundlage(gid: int, von: date, bis: date, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    return _excel(gea.grundlage_xlsx(s, g, von, bis), f"Abrechnungsgrundlage_GEA{g.id}_{von:%Y-%m}_{bis:%Y-%m}.xlsx")


def _excel(inhalt: bytes, name: str) -> Response:
    return Response(inhalt, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/verwaltung/gea/{gid}/energiedaten", response_class=HTMLResponse)
async def gea_energiedaten(request: Request, gid: int, dateien: list[UploadFile], s: Session = Depends(sitzung),
                           _=Depends(verwaltung)):
    g = s.get(Gruppe, gid) or _404()
    ergebnisse = []
    for datei in dateien:
        ergebnisse.append(import_verarbeiten(s, await datei.read(), datei.filename or "Datei", g.id))
        s.commit()
    return _seite(request, "energiedaten.html", ergebnisse=ergebnisse, **_energiedaten_stand(s))


# ---------------------------------------------------------------- Anfragen (Verwaltung)

@app.get("/verwaltung/anfragen", response_class=HTMLResponse)
def anfragen_seite(request: Request, s: Session = Depends(sitzung), _=Depends(verwaltung)):
    liste = s.exec(select(Anfrage).order_by(Anfrage.erstellt.desc())).all()
    return _seite(request, "anfragen.html", liste=liste,
                  gruppen={g.id: g for g in s.exec(select(Gruppe)).all()},
                  mailto={a.id: nachrichten.mailto(a.email, f"{config.MARKE}: Ihre Anfrage", f"Hallo {a.vorname},\n\n")
                          for a in liste})


@app.post("/verwaltung/anfrage/{aid}")
def anfrage_status(aid: int, s: Session = Depends(sitzung), _=Depends(verwaltung), status: str = Form(...)):
    a = s.get(Anfrage, aid) or _404()
    if status in ("neu", "in_bearbeitung", "erledigt"):
        a.status = status
        s.add(a)
        s.commit()
    return RedirectResponse("/verwaltung/anfragen", status_code=303)


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


GEA_SCHRITTE = [GruppenStatus.REGISTRIERT, GruppenStatus.EDA_ANGEMELDET, GruppenStatus.AKTIV]


@app.get("/mein/{tid}/{token}", response_class=HTMLResponse)
def mein_bereich(request: Request, tid: int, token: str, s: Session = Depends(sitzung), iban_fehler: str = ""):
    t = _kunde(s, tid, token)
    daten = _mein_daten(s, t, token)
    daten["iban_fehler"] = iban_fehler
    return _seite(request, "mein_gea.html" if daten.get("betreiber_gea") else "mein.html", **daten)


def _mein_daten(s: Session, t: Teilnehmer, token: str) -> dict:
    g = _gruppe_von(s, t)
    daten = {"t": t, "g": g, "token": token, "iban_fehler": "", "rechnungen": [], "aufgaben": [],
             "schritte": SCHRITTE, "erreicht": -1, "einladung": None, "partner": [], "gebuehren": [],
             "gea": bool(g and g.art == Art.GEA), "betreiber_gea": False, "ergebnisse": None}
    if t.rolle == Rolle.ERZEUGER:
        daten["iban"] = zahlung.iban_lesbar(t.iban) if t.iban else None
    if not daten["gea"]:  # Wunschnachbar einladen gibt es bei P2P
        daten["einladung"] = einladungslink(s, t)
    if not g:
        return daten
    d = _gruppen_daten(s, g)
    gea_art = daten["gea"]
    daten["betreiber_gea"] = gea_art and t.id == g.erzeuger_id
    daten["partner"] = [d["erzeuger"]] + [a for _, a in d["abnehmer"]]
    schritte = GEA_SCHRITTE if gea_art else SCHRITTE
    daten["schritte"] = schritte
    daten["erreicht"] = schritte.index(g.status) if g.status in schritte else -1
    daten["preis_heute"] = preis_fuer(g, date.today())
    if not gea_art:
        unterschrieben = s.exec(select(Unterschrift).where(Unterschrift.gruppe_id == g.id,
                                                           Unterschrift.teilnehmer_id == t.id)).first()
        if not unterschrieben and g.status != GruppenStatus.BEENDET:
            daten["aufgaben"].append(("Vertrag und Vollmacht unterschreiben", unterschriftslink(g.id, t.id)))
        z = s.exec(select(Zustimmung).where(Zustimmung.gruppe_id == g.id, Zustimmung.teilnehmer_id == t.id)).first()
        if z and z.freigegeben_am and not z.erteilt_am:
            frist = z.freigegeben_am + timedelta(days=config.ZUSTIMMUNG_FRIST_TAGE)
            daten["aufgaben"].append((f"Im Kundenportal von Vorarlberg Netz zustimmen, bis {frist:%d.%m.%Y}", None))
    rechnet_fix = not gea_art or g.modell == Modell.VERWALTET
    if t.rolle == Rolle.ERZEUGER and g.preis_ct_kwh > 0 and rechnet_fix and not t.iban:
        daten["aufgaben"].append(("Konto (IBAN) für die Zahlungen eintragen, siehe unten", None))
    abfrage = select(Rechnung).where(Rechnung.gruppe_id == g.id)
    if t.id != g.erzeuger_id:
        abfrage = abfrage.where(Rechnung.abnehmer_id == t.id)
    daten["rechnungen"] = s.exec(abfrage.order_by(Rechnung.bis.desc())).all()
    if t.id == g.erzeuger_id:
        daten["gebuehren"] = s.exec(select(Gebuehr).where(Gebuehr.gruppe_id == g.id)
                                    .order_by(Gebuehr.bis.desc())).all()
    if daten["betreiber_gea"]:
        daten["a"] = gea.auswerten(s, g)
        daten["zeitraeume"] = gea.zeitraeume(s, g)
        letzter = daten["a"]["bis"] if daten["a"] else None
        if letzter and (date.today() - letzter).days > 45:
            daten["aufgaben"].append((f"EDA-Export ab {letzter + timedelta(days=1):%d.%m.%Y} hochladen, siehe unten", None))
        elif not letzter and g.status in (GruppenStatus.EDA_ANGEMELDET, GruppenStatus.AKTIV):
            daten["aufgaben"].append(("Ersten EDA-Export hochladen, siehe unten", None))
    elif t.rolle == Rolle.ERZEUGER:
        daten["a"] = _auswertung(s, t, [a for _, a in d["abnehmer"]], g.id)
    else:
        daten["a"] = _auswertung(s, None, [t], g.id)
    offen = [r for r in daten["rechnungen"] if not r.bezahlt_am]
    if t.id != g.erzeuger_id and offen:
        daten["aufgaben"].append((f"{len(offen)} offene Rechnung(en) bezahlen, siehe unten", None))
    jahre: dict[int, dict] = {}
    for r in daten["rechnungen"]:
        j = jahre.setdefault(r.bis.year, {"kwh": 0.0, "brutto": 0.0, "bezahlt": 0.0})
        j["kwh"] += r.kwh
        j["brutto"] += r.brutto
        j["bezahlt"] += r.brutto if r.bezahlt_am else 0.0
    daten["jahre"] = sorted(jahre.items(), reverse=True)
    return daten


@app.post("/mein/{tid}/{token}/energiedaten", response_class=HTMLResponse)
async def mein_energiedaten(request: Request, tid: int, token: str, dateien: list[UploadFile],
                            s: Session = Depends(sitzung)):
    """GEA-Betreiber lädt den Monatsexport selbst hoch. Nur Daten der eigenen GEA werden übernommen."""
    t = _kunde(s, tid, token)
    g = _gruppe_von(s, t)
    if not g or g.art != Art.GEA or g.erzeuger_id != t.id:
        raise HTTPException(404)
    ergebnisse = []
    eigene = set(importe.zaehlpunkte(s, g))
    for datei in dateien:
        inhalt, name = await datei.read(), datei.filename or "Datei"
        try:
            imp = importe.einlesen(s, inhalt, name)
        except ValueError as e:
            s.add(Upload(token=secrets.token_urlsafe(12), dateiname=name, inhalt=inhalt, ziel_gruppe_id=g.id,
                         von_teilnehmer_id=t.id))
            ergebnisse.append({"datei": name, "fehler": f"{e} Wir schauen uns die Datei an und melden uns."})
            continue
        fremd = imp.gemeinschafts_id and g.gemeinschafts_id and imp.gemeinschafts_id != normalisieren(g.gemeinschafts_id)
        passt = imp.zaehlpunkte & eigene or (imp.gemeinschafts_id and imp.gemeinschafts_id == g.gemeinschafts_id)
        if fremd or not passt:
            ergebnisse.append({"datei": name, "fehler": "Diese Datei gehört nicht zu Ihrer GEA. Bitte den Export "
                                                        "Ihrer Gemeinschaft aus dem EDA-Portal hochladen."})
            continue
        e = import_verarbeiten(s, inhalt, name, g.id, von_tid=t.id)
        if e.get("zuordnen"):
            e = {"datei": name, "hinweis": "Die Datei hat Spalten, die wir noch nicht kennen. Wir ordnen sie zu, "
                                           "danach erscheinen die Zahlen hier."}
        ergebnisse.append(e)
    s.commit()
    daten = _mein_daten(s, t, token)
    daten["ergebnisse"] = ergebnisse
    return _seite(request, "mein_gea.html", **daten)


@app.get("/mein/{tid}/{token}/grundlage")
def mein_grundlage(tid: int, token: str, von: date, bis: date, s: Session = Depends(sitzung)):
    t = _kunde(s, tid, token)
    g = _gruppe_von(s, t)
    if not g or g.art != Art.GEA or g.erzeuger_id != t.id:
        raise HTTPException(404)
    return _excel(gea.grundlage_xlsx(s, g, von, bis), f"Abrechnungsgrundlage_{von:%Y-%m}_{bis:%Y-%m}.xlsx")


@app.get("/mein/{tid}/{token}/gebuehr/{xid}", response_class=HTMLResponse)
def mein_gebuehr(request: Request, tid: int, token: str, xid: int, s: Session = Depends(sitzung)):
    t = _kunde(s, tid, token)
    x = s.get(Gebuehr, xid) or _404()
    if x.empfaenger_id != t.id:
        raise HTTPException(404)
    return _seite(request, "mein_gebuehr.html", zurueck=f"/mein/{tid}/{token}", **_gebuehr_daten(s, x))


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
