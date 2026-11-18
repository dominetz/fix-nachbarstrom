"""Energiedaten-Exporte aus dem EDA-Portal einlesen, Excel oder CSV.

Der genaue Aufbau des Exports ist nicht öffentlich dokumentiert. Belegt sind nur
einzelne Spaltennamen (siehe energiedaten.py). Deshalb liest dieses Modul nicht
aus festen Zellen, sondern sucht:

* das Blatt mit den meisten Zeilen, die mit einem Zeitpunkt beginnen,
* den Zeitpunkt als Excel-Datum, als Text (deutsch oder ISO, auch mit Zeitzone)
  oder als Datum und Uhrzeit in zwei Spalten; eine Spalte „Ende“ daneben wird übersprungen,
* den Aufbau „breit“ (eine Spalte je Zählpunkt und Messgröße, Zählpunkt in einer
  Kopfzeile, auch über verbundene Zellen, oder im Spaltentitel) oder „lang“ (eine
  Zeile je Zählpunkt und Zeitpunkt, Messgrößen als Spalten oder als eigene Spalte
  mit einer Wertespalte),
* die Messgröße über Stichworte im Titel, die Einheit (kWh, Wh, MWh) ebenso.

Was sich nicht eindeutig zuordnen lässt, wird nicht geraten, sondern unter
`unbekannt` gemeldet. Die Plattform fragt dann einmal nach und merkt sich die
Zuordnung für alle weiteren Exporte.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

FELDER = ("verbrauch", "anteil", "eigendeckung", "erzeugung", "restueberschuss")
IGNORIEREN = "ignorieren"
ZEITFORMAT = "%Y-%m-%d %H:%M"
WIEN = ZoneInfo("Europe/Vienna")

FELD_TEXT = {
    "verbrauch": "Gesamtverbrauch des Zählpunkts",
    "anteil": "Zugeteilter Anteil an der gemeinsamen Erzeugung",
    "eigendeckung": "Eigendeckung (tatsächlich genutzt, wird verrechnet)",
    "erzeugung": "Erzeugung, die der Gemeinschaft zur Verfügung steht",
    "restueberschuss": "Restüberschuss, geht normal ins Netz",
    IGNORIEREN: "Nicht verwenden",
}

_ZP = re.compile(r"AT[0-9]{6}[0-9A-Z]{25}")
_ID = re.compile(r"AT[0-9A-Z]{18,40}")
_RICHTUNG = {"CONSUMPTION": "verbrauch", "VERBRAUCH": "verbrauch", "BEZUG": "verbrauch",
             "GENERATION": "erzeugung", "ERZEUGUNG": "erzeugung", "EINSPEISUNG": "erzeugung",
             "PRODUCTION": "erzeugung"}
_CODE_SPALTE = re.compile(r"mess(gr|groe|größe|art)|metercode|meter code|obis|kennzahl|register|messgröße|typ\b|art\b")
_WERT_SPALTE = re.compile(r"^(wert|value|menge|energie|energy|kwh|wh)\b")
_ZP_SPALTE = re.compile(r"z(ä|ae)hlpunkt|meteringpoint|metering point|messpunkt|zp\b")


@dataclass
class Spalte:
    index: int
    titel: str
    zaehlpunkt: str | None = None
    richtung: str | None = None
    feld: str | None = None
    faktor: float = 1.0


@dataclass
class Import:
    werte: list[dict]  # je Zeitpunkt und Zählpunkt: zaehlpunkt, beginn und die FELDER
    zaehlpunkte: set[str]
    von: datetime | None
    bis: datetime | None
    gemeinschafts_id: str | None = None
    richtung: dict[str, str] = field(default_factory=dict)  # Zählpunkt -> "verbrauch" / "erzeugung"
    unbekannt: dict[str, list[str]] = field(default_factory=dict)  # Titel -> Beispielwerte
    aufbau: str = ""
    intervall: int | None = None  # Minuten
    hinweise: list[str] = field(default_factory=list)

    @property
    def erzeuger(self) -> set[str]:
        """Zählpunkte, die Erzeugung liefern."""
        mit_erzeugung = {w["zaehlpunkt"] for w in self.werte if w["erzeugung"] or w["restueberschuss"]}
        markiert = {zp for zp, r in self.richtung.items() if r == "erzeugung"}
        return (mit_erzeugung | markiert) & self.zaehlpunkte


# ---------------------------------------------------------------- Hilfen

def schluessel(titel: str) -> str:
    """Titel vereinheitlichen, damit eine gemerkte Zuordnung wiedererkannt wird."""
    t = (titel or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def feld_aus_titel(titel: str, zuordnung: dict[str, str] | None = None) -> str | None:
    t = schluessel(titel)
    if zuordnung and t in zuordnung:
        return zuordnung[t]
    if not t:
        return None
    if "qualit" in t or t in ("status", "flag", "einheit", "unit") or "ersatzwert" in t:
        return IGNORIEREN
    if "eigendeckung" in t:
        return "eigendeckung"
    if "restueberschuss" in t or ("rest" in t and "ueberschuss" in t):
        return "restueberschuss"
    if "anteil" in t:
        return "anteil"
    if "verbrauch" in t:
        return "verbrauch"
    if "erzeugung" in t:
        return "erzeugung"
    if re.search(r"\b(summe|total)\b", t):  # Summenspalte ohne Messgröße
        return IGNORIEREN
    return None


def _faktor(titel: str) -> float:
    t = schluessel(titel)
    if re.search(r"\bmwh\b|\[mwh\]", t):
        return 1000.0
    if re.search(r"(?<!k)\bwh\b|\[wh\]", t) and "kwh" not in t:
        return 0.001
    return 1.0


def _text(wert) -> str:
    if wert is None:
        return ""
    if isinstance(wert, float) and wert.is_integer():
        return str(int(wert))
    return str(wert).strip()


def _zahl(wert) -> float | None:
    """Zahl oder None (leer). Text, der keine Zahl ist, ergibt ValueError."""
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    text = str(wert).strip().replace(" ", "").replace(" ", "")
    if text in ("", "-", "–"):
        return None
    if "," in text and "." in text:
        text = text.replace(".", "") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    return float(text.replace(",", "."))


def _ist_zahl(wert) -> bool:
    try:
        return _zahl(wert) is not None
    except ValueError:
        return False


def _ortszeit(z: datetime) -> datetime:
    if z.tzinfo is not None:
        z = z.astimezone(WIEN).replace(tzinfo=None)
    return z.replace(second=0, microsecond=0)


_FORMATE = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%d/%m/%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d")


def _zeit(wert) -> datetime | None:
    if isinstance(wert, datetime):
        return _ortszeit(wert)
    if isinstance(wert, date):
        return datetime(wert.year, wert.month, wert.day)
    if not isinstance(wert, str):
        return None
    text = wert.strip()
    if not (8 <= len(text) <= 32) or not text[:2].isdigit():
        return None
    try:
        return _ortszeit(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    for fmt in _FORMATE:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _uhrzeit(wert) -> time | None:
    if isinstance(wert, time):
        return wert
    if isinstance(wert, timedelta):
        return (datetime.min + wert).time()
    if isinstance(wert, str) and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", wert.strip()):
        teile = [int(x) for x in wert.strip().split(":")]
        return time(teile[0] % 24, teile[1])
    return None


def _zeitpunkt(zeile: list) -> tuple[datetime, set[int]] | None:
    """Zeitpunkt in den ersten Spalten einer Zeile, mit den dafür verwendeten Spalten."""
    for i in range(min(4, len(zeile))):
        z = _zeit(zeile[i])
        if not z:
            continue
        benutzt = {i}
        if i + 1 < len(zeile) and z.time() == time(0) and (u := _uhrzeit(zeile[i + 1])) is not None:
            z = datetime.combine(z.date(), u)
            benutzt.add(i + 1)
        j = max(benutzt) + 1  # Ende der Messperiode gleich daneben
        if j < len(zeile) and _zeit(zeile[j]):
            benutzt.add(j)
            if j + 1 < len(zeile) and _uhrzeit(zeile[j + 1]) is not None:
                benutzt.add(j + 1)
        return z, benutzt
    return None


# ---------------------------------------------------------------- Datei lesen

def tabellen(inhalt: bytes, dateiname: str = "") -> list[tuple[str, list[list]]]:
    """Alle Blätter als Zeilenlisten. Excel (.xlsx) oder CSV."""
    if inhalt[:2] == b"PK":
        try:
            wb = load_workbook(io.BytesIO(inhalt), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - beliebige kaputte Datei
            raise ValueError("Die Excel-Datei lässt sich nicht öffnen.") from e
        ergebnis = []
        for ws in wb.worksheets:
            zeilen = [_kuerzen(list(z)) for z in ws.iter_rows(values_only=True)]
            ergebnis.append((ws.title, zeilen))
        wb.close()
        return ergebnis
    if inhalt[:4] == b"\xd0\xcf\x11\xe0":
        raise ValueError("Das ist eine alte Excel-Datei (.xls). Bitte in Excel öffnen und als .xlsx speichern.")
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            text = inhalt.decode(kodierung)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Die Datei ist weder Excel (.xlsx) noch CSV.")
    if "\x00" in text[:1000]:
        raise ValueError("Die Datei ist weder Excel (.xlsx) noch CSV.")
    probe = text[:5000]
    trenner = max((";", "\t", ","), key=probe.count)
    zeilen = [_kuerzen([z if z != "" else None for z in r]) for r in csv.reader(io.StringIO(text), delimiter=trenner)]
    return [(dateiname or "CSV", zeilen)]


def _kuerzen(zeile: list) -> list:
    while zeile and (zeile[-1] is None or zeile[-1] == ""):
        zeile.pop()
    return zeile


# ---------------------------------------------------------------- Einlesen

def einlesen(inhalt: bytes, zuordnung: dict[str, str] | None = None, dateiname: str = "") -> Import:
    """Export lesen. zuordnung: gemerkte Titel -> Feld (oder IGNORIEREN)."""
    alle = tabellen(inhalt, dateiname)
    beste = None
    for name, zeilen in alle:
        start, anzahl = None, 0
        for nr, z in enumerate(zeilen):
            zp = _zeitpunkt(z)
            if zp and any(_ist_zahl(w) for i, w in enumerate(z) if i not in zp[1]):
                start = nr if start is None else start
                anzahl += 1
        if start is not None and (beste is None or anzahl > beste[3]):
            beste = (name, zeilen, start, anzahl)
    if not beste:
        raise ValueError("In der Datei wurde keine Tabelle mit Zeitpunkten und Werten gefunden.")
    name, zeilen, start, _ = beste
    kopf = [z for z in zeilen[max(0, start - 25):start] if z]
    daten = [z for z in zeilen[start:] if z]
    erste = daten[0]
    _, zeitspalten = _zeitpunkt(erste)
    lang_zp = next((i for i, w in enumerate(erste) if i not in zeitspalten and isinstance(w, str)
                    and _ZP.fullmatch(_norm_zp(w))), None)
    if lang_zp is not None:
        imp = _lang(kopf, daten, zeitspalten, lang_zp, zuordnung)
    else:
        imp = _breit(kopf, daten, zeitspalten, zuordnung)
    imp.aufbau = f"Blatt „{name}“, {'eine Zeile je Zählpunkt' if lang_zp is not None else 'eine Spalte je Zählpunkt'}"
    imp.gemeinschafts_id = _gemeinschafts_id(alle, imp.zaehlpunkte, dateiname)
    zeiten = sorted({w["beginn"] for w in imp.werte})
    if len(zeiten) > 1:
        schritte = [(datetime.strptime(b, ZEITFORMAT) - datetime.strptime(a, ZEITFORMAT)).seconds // 60
                    for a, b in zip(zeiten[:50], zeiten[1:51])]
        imp.intervall = min(s for s in schritte if s > 0) if any(schritte) else None
        if imp.intervall and imp.intervall != 15:
            imp.hinweise.append(f"Die Werte sind {imp.intervall}-Minuten-Werte, nicht Viertelstunden.")
    if not imp.zaehlpunkte:
        raise ValueError("Keine Spalte mit Zählpunkt und Werten gefunden.")
    return imp


def _norm_zp(text: str) -> str:
    return re.sub(r"[\s.\-]", "", text or "").upper()


def _zp_in(text: str) -> str | None:
    t = _norm_zp(_text(text))
    m = _ZP.search(t)
    return m.group(0) if m else None


def _breit(kopf: list[list], daten: list[list], zeitspalten: set[int], zuordnung) -> Import:
    breite = max(len(z) for z in kopf + daten[:5])
    treffer = [sum(1 for w in z if _zp_in(w)) for z in kopf]
    zp_zeile = kopf[treffer.index(max(treffer))] if treffer and max(treffer) else None
    spalten: list[Spalte] = []
    vorige: Spalte | None = None
    for c in range(breite):
        if c in zeitspalten or c < min(zeitspalten):
            continue
        zellen = [_text(z[c]) if c < len(z) else "" for z in kopf]
        zp = next((x for x in (_zp_in(t) for t in zellen) if x), None)
        richtung = next((_RICHTUNG[t.upper()] for t in zellen if t.upper() in _RICHTUNG), None)
        kandidaten = [t for t in zellen if t and t.upper() not in _RICHTUNG and _norm_zp(t) != zp]
        if zp:  # Zählpunkt im Titel selbst, z. B. "AT…: Eigendeckung"
            kandidaten = [re.sub(r"[:|\-–]\s*$|^\s*[:|\-–]", "", t.replace(zp, "")).strip() or t for t in kandidaten]
        titel, feld = "", None
        for t in reversed(kandidaten):
            f = feld_aus_titel(t, zuordnung)
            if f:
                titel, feld = t, f
                break
        if not titel:
            titel = kandidaten[-1] if kandidaten else ""
        leer = zp_zeile is not None and (c >= len(zp_zeile) or zp_zeile[c] in (None, ""))
        if not zp and leer and vorige and vorige.zaehlpunkt and titel:  # verbundene Zelle
            zp, richtung = vorige.zaehlpunkt, richtung or vorige.richtung
        if not zp and titel:  # ohne Zählpunkt nicht zuzuordnen, z. B. eine Summenspalte
            feld = IGNORIEREN
        s = Spalte(c, titel, zp, richtung, feld, _faktor(titel))
        if not zp and not titel and not any(_ist_zahl(z[c]) for z in daten[:20] if c < len(z)):
            continue
        spalten.append(s)
        vorige = s
    # Spalten mit Text statt Zahlen (z. B. Qualitätskennzeichen) nicht verwenden
    for s in spalten:
        probe = [z[s.index] for z in daten[:96] if s.index < len(z) and z[s.index] not in (None, "")]
        if probe and sum(_ist_zahl(w) for w in probe) < len(probe) / 2:
            s.feld = IGNORIEREN
    return _sammeln(daten, zeitspalten, [(s, None) for s in spalten])


def _lang(kopf: list[list], daten: list[list], zeitspalten: set[int], zp_spalte: int, zuordnung) -> Import:
    titel = [_text(w) for w in (kopf[-1] if kopf else [])]
    breite = max(len(z) for z in daten[:20])
    titel += [""] * (breite - len(titel))
    zahlen = [c for c in range(breite) if c not in zeitspalten and c != zp_spalte
              and sum(_ist_zahl(z[c]) for z in daten[:50] if c < len(z)) > min(len(daten), 50) / 2]
    code = next((c for c in range(breite) if c not in zeitspalten and c != zp_spalte and c not in zahlen
                 and _CODE_SPALTE.search(schluessel(titel[c]))), None)
    richtung_spalte = next((c for c in range(breite) if c not in zahlen and c != zp_spalte and any(
        _text(z[c]).upper() in _RICHTUNG for z in daten[:20] if c < len(z))), None)
    if code is not None:
        wert = next((c for c in zahlen if _WERT_SPALTE.search(schluessel(titel[c]))), zahlen[0] if zahlen else None)
        if wert is None:
            raise ValueError("Neben der Spalte mit der Messgröße wurde keine Spalte mit Werten gefunden.")
        faktor = _faktor(titel[wert])
        eintraege = []
        for z in daten:
            t = _text(z[code]) if code < len(z) else ""
            s = Spalte(wert, t, None, None, feld_aus_titel(t, zuordnung), _faktor(t) if _faktor(t) != 1 else faktor)
            eintraege.append((s, z))
        return _sammeln(daten, zeitspalten, eintraege, zp_spalte=zp_spalte, richtung_spalte=richtung_spalte,
                        je_zeile=True)
    spalten = [Spalte(c, titel[c], None, None, feld_aus_titel(titel[c], zuordnung), _faktor(titel[c]))
               for c in zahlen]
    return _sammeln(daten, zeitspalten, [(s, None) for s in spalten], zp_spalte=zp_spalte,
                    richtung_spalte=richtung_spalte)


def _sammeln(daten: list[list], zeitspalten: set[int], spalten: list[tuple[Spalte, list | None]],
             zp_spalte: int | None = None, richtung_spalte: int | None = None, je_zeile: bool = False) -> Import:
    ergebnis: dict[tuple[str, str], dict] = {}
    unbekannt: dict[str, list[str]] = {}
    richtung: dict[str, str] = {}
    gesehen: set[str] = set()  # alle Zählpunkte, auch mit noch unbekannten Spalten
    von = bis = None

    def merken(s: Spalte, beispiel):
        """Beispielwerte für die Nachfrage, möglichst keine Nullen (Nacht)."""
        if s.feld is None and s.titel is not None:
            liste = unbekannt.setdefault(s.titel or "(ohne Titel)", [])
            if len(liste) < 3 and beispiel not in (None, ""):
                try:
                    null = _zahl(beispiel) == 0
                except ValueError:
                    null = False
                if not null:
                    liste.append(_text(beispiel))

    zeilen_je_eintrag = [(s, z) for s, z in spalten] if je_zeile else None
    for nr, z in enumerate(daten):
        zp_zeit = _zeitpunkt(z)
        if not zp_zeit:
            continue
        zeit = zp_zeit[0]
        beginn = zeit.strftime(ZEITFORMAT)
        zeilen_zp = _zp_in(z[zp_spalte]) if zp_spalte is not None and zp_spalte < len(z) else None
        if zp_spalte is not None and not zeilen_zp:
            continue
        if richtung_spalte is not None and zeilen_zp and richtung_spalte < len(z):
            r = _RICHTUNG.get(_text(z[richtung_spalte]).upper())
            if r:
                richtung[zeilen_zp] = r
        auswahl = [zeilen_je_eintrag[nr]] if je_zeile else spalten
        for s, _ in auswahl:
            zp = zeilen_zp or s.zaehlpunkt
            roh = z[s.index] if s.index < len(z) else None
            if zp and s.feld != IGNORIEREN:
                gesehen.add(zp)
                von = zeit if von is None or zeit < von else von
                bis = zeit if bis is None or zeit > bis else bis
            if s.feld is None:
                merken(s, roh)
                continue
            if s.feld == IGNORIEREN or not zp:
                continue
            try:
                wert = _zahl(roh)
            except ValueError:
                continue
            if wert is None:
                continue
            ziel = ergebnis.setdefault((zp, beginn), {"zaehlpunkt": zp, "beginn": beginn, **dict.fromkeys(FELDER, 0.0)})
            ziel[s.feld] += wert * s.faktor
            if s.richtung:
                richtung[zp] = s.richtung
    for titel, liste in unbekannt.items():
        if not liste:
            liste.append("0")
    werte = list(ergebnis.values())
    for w in werte:
        for f in FELDER:
            w[f] = round(w[f], 6)
    return Import(werte, gesehen, von, bis, richtung=richtung, unbekannt=unbekannt)


_ID_TEXT = re.compile(r"\bAT[0-9]{2}[0-9A-Z]{16,40}\b")
_ID_LABEL = re.compile(r"gemeinschaft|community|ec-?id|\bGEA\b|\bEEG\b|\bBEG\b", re.I)


def _gemeinschafts_id(alle: list[tuple[str, list[list]]], zaehlpunkte: set[str], dateiname: str) -> str | None:
    """Kennung der Gemeinschaft: zuerst neben einer Beschriftung wie „Gemeinschaft“ gesucht,
    sonst ein einzelner AT-Code im Kopf, der kein Zählpunkt ist."""
    alle_zp = set(zaehlpunkte)
    for _, zeilen in alle:
        for z in zeilen[:60]:
            for w in z:
                if (x := _zp_in(w)) and len(_text(w)) < 45:
                    alle_zp.add(x)

    def ids(text: str, ausser: set[str]) -> list[str]:
        return [m.group(0) for m in _ID_TEXT.finditer(_text(text).upper()) if m.group(0) not in ausser]

    for _, zeilen in alle:  # zuerst neben einer Beschriftung, die Kennung kann wie ein Zählpunkt aussehen
        for z in zeilen[:30]:
            for i, w in enumerate(z[:12]):
                if isinstance(w, str) and _ID_LABEL.search(w):
                    for nachbar in z[i:i + 3]:
                        if gefunden := ids(nachbar, zaehlpunkte):
                            return gefunden[0]
    kandidaten: list[str] = []
    for _, zeilen in alle:
        for z in zeilen[:30]:
            for w in z[:12]:
                for x in ids(w, alle_zp):
                    if x not in kandidaten:
                        kandidaten.append(x)
    for x in ids(dateiname.replace("_", " "), alle_zp):
        if x not in kandidaten:
            kandidaten.append(x)
    return kandidaten[0] if len(kandidaten) == 1 else None


# ---------------------------------------------------------------- Aufbau-Bericht

_TECHNISCH = re.compile(
    r"\d|\[|kwh|wh\b|z(ä|ae)hl|meter|energ|richtung|direction|consumption|generation|verbrauch|erzeug|anteil|"
    r"eigendeck|rest|(ü|ue)berschuss|einspeis|beginn|ende|zeit|datum|mess|qualit|gemeinschaft|community|summe|"
    r"gesamt|wert|value|einheit|unit|obis|code|period|start|end|quality|zeitraum|bezug|ID\b", re.I)


def aufbau_bericht(inhalt: bytes, dateiname: str = "", zeilen: int = 14, spalten: int = 8) -> str:
    """Aufbau der Datei zum Weitergeben, wenn das Einlesen nicht klappt.
    Zählpunkt- und Gemeinschaftsnummern werden durch [Nummer n] ersetzt, sonstiger
    Text (z. B. Namen) durch [Text]. Zahlen und Spaltentitel bleiben stehen."""
    try:
        alle = tabellen(inhalt, dateiname)
    except ValueError as e:
        return str(e)
    nummern: dict[str, str] = {}

    def ersetzen(m) -> str:
        return nummern.setdefault(m.group(0), f"[Nummer {len(nummern) + 1}]")

    def anonym(wert) -> str:
        if isinstance(wert, datetime):
            return wert.strftime("%d.%m.%Y %H:%M")
        t = _text(wert)
        if isinstance(wert, str):
            t = re.sub(r"AT[0-9][0-9A-Z]{9,45}", ersetzen, re.sub(r"(?<=[0-9A-Z])[ .](?=[0-9A-Z]{3})", "", t)
                       if _ID_TEXT.search(_norm_zp(t)) else t)
            if not _TECHNISCH.search(t.replace("[Nummer", "")) and "[Nummer" not in t:
                t = "[Text]"
        return t[:38] + "…" if len(t) > 40 else t

    teile = []
    for name, liste in alle:
        breite = max((len(z) for z in liste), default=0)
        teile.append(f"Blatt „{anonym(name) if name != dateiname else 'CSV'}“: {len(liste)} Zeilen, {breite} Spalten")
        for nr, z in enumerate(liste[:zeilen], start=1):
            zellen = [anonym(w) for w in z[:spalten]]
            if any(zellen):
                teile.append(f"  {nr:>2}: " + " | ".join(zellen) + (" | …" if len(z) > spalten else ""))
    return "\n".join(teile)
