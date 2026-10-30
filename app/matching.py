"""Matching: Welche Abnehmer passen zu welchem Erzeuger?

Gerechnet wird mit 96 Viertelstunden je Tag für drei Jahreszeiten.
Geteilt wird nur, was in derselben Viertelstunde erzeugt und verbraucht wird.
Die Profile sind vereinfachte Standardprofile; sobald echte Messwerte
vorliegen, ersetzen sie die Profile.
"""
import math
from dataclasses import dataclass

from .models import Profiltyp, Teilnehmer

VIERTELSTUNDEN = 96
# (Name, Tage im Jahr, Sonnenaufgang h, Sonnenuntergang h, Anteil am PV-Jahresertrag)
JAHRESZEITEN = [
    ("sommer", 122, 5.5, 21.0, 0.48),
    ("uebergang", 122, 7.0, 19.0, 0.36),
    ("winter", 121, 8.0, 16.5, 0.16),
]
# Annahme: spezifischer Jahresertrag in Vorarlberg, kWh je kWp. Durch echte
# Anlagendaten ersetzen, sobald vorhanden.
PV_ERTRAG_KWH_JE_KWP = 1050
MAX_ABNEHMER = 5


def _glocke(stunde: float, auf: float, unter: float) -> float:
    if stunde <= auf or stunde >= unter:
        return 0.0
    return math.sin(math.pi * (stunde - auf) / (unter - auf)) ** 2


def pv_profil(kwp: float) -> dict[str, list[float]]:
    """kWh je Viertelstunde für einen typischen Tag je Jahreszeit."""
    jahr = kwp * PV_ERTRAG_KWH_JE_KWP
    profile = {}
    for name, tage, auf, unter, anteil in JAHRESZEITEN:
        form = [_glocke((q + 0.5) / 4, auf, unter) for q in range(VIERTELSTUNDEN)]
        summe = sum(form) or 1
        tag = jahr * anteil / tage
        profile[name] = [tag * f / summe for f in form]
    return profile


def _form_verbrauch(typ: Profiltyp, jahreszeit: str) -> list[float]:
    """Relative Verbrauchsform über den Tag (wird auf den Jahresverbrauch skaliert)."""
    def h(q):
        return (q + 0.5) / 4

    def spitze(stunde, mitte, breite):
        return math.exp(-((stunde - mitte) / breite) ** 2)

    werte = []
    for q in range(VIERTELSTUNDEN):
        s = h(q)
        grund = 0.35
        if typ == Profiltyp.HAUSHALT:
            v = grund + 0.8 * spitze(s, 7.5, 1.2) + 0.5 * spitze(s, 12.5, 1.5) + 1.3 * spitze(s, 19.0, 2.0)
        elif typ == Profiltyp.HAUSHALT_TAGSUEBER:
            v = grund + 0.6 * spitze(s, 8, 1.5) + 0.9 * spitze(s, 12.5, 3.0) + 1.0 * spitze(s, 19, 2.0)
        elif typ == Profiltyp.WAERMEPUMPE:
            stark = {"winter": 1.6, "uebergang": 1.0, "sommer": 0.4}[jahreszeit]
            v = grund + stark * (0.6 + 0.6 * spitze(s, 13, 3.5) + 0.4 * spitze(s, 6.5, 1.5))
        elif typ == Profiltyp.EAUTO:
            v = grund + 0.6 * spitze(s, 7.5, 1.2) + 1.2 * spitze(s, 12.5, 2.0) + 1.0 * spitze(s, 19.0, 2.0)
        elif typ == Profiltyp.GEWERBE:
            v = 0.25 + (1.6 if 7.5 <= s <= 17.5 else 0.0) * (5 / 7)  # Mo–Fr gemittelt
        else:
            v = 1.0
        werte.append(v)
    return werte


def verbrauch_profil(typ: Profiltyp, jahres_kwh: float) -> dict[str, list[float]]:
    formen = {name: _form_verbrauch(typ, name) for name, *_ in JAHRESZEITEN}
    gewicht = sum(tage * sum(formen[name]) for name, tage, *_ in JAHRESZEITEN) or 1
    faktor = jahres_kwh / gewicht
    return {name: [v * faktor for v in formen[name]] for name in formen}


def ueberschuss_profil(erzeuger: Teilnehmer) -> dict[str, list[float]]:
    """PV-Erzeugung minus Eigenverbrauch des Erzeugers."""
    pv = pv_profil(erzeuger.anlage_kwp)
    eigen = verbrauch_profil(erzeuger.profiltyp, erzeuger.jahresverbrauch_kwh)
    return {n: [max(0.0, p - e) for p, e in zip(pv[n], eigen[n])] for n in pv}


def geteilte_menge(erzeuger: Teilnehmer, abnehmer: list[Teilnehmer]) -> dict[int, float]:
    """kWh pro Jahr je Abnehmer bei dynamischer Aufteilung je Viertelstunde."""
    ueber = ueberschuss_profil(erzeuger)
    bedarf = {a.id: verbrauch_profil(a.profiltyp, a.jahresverbrauch_kwh) for a in abnehmer}
    ergebnis = {a.id: 0.0 for a in abnehmer}
    for name, tage, *_ in JAHRESZEITEN:
        for q in range(VIERTELSTUNDEN):
            angebot = ueber[name][q]
            summe = sum(bedarf[a.id][name][q] for a in abnehmer)
            if angebot <= 0 or summe <= 0:
                continue
            verteilt = min(angebot, summe)
            for a in abnehmer:
                ergebnis[a.id] += tage * verteilt * bedarf[a.id][name][q] / summe
    return ergebnis


def zonelevel(a: Teilnehmer, b: Teilnehmer) -> int | None:
    """3 = gleiche Trafostation, 4 = gleiches Umspannwerk, 6 = nur gleicher
    Netzbetreiber, None = nicht möglich (verschiedene Netzbetreiber, bis
    voraussichtlich April 2027)."""
    if a.netzbetreiber != b.netzbetreiber:
        return None
    if a.trafo_id and a.trafo_id == b.trafo_id:
        return 3
    if a.umspannwerk_id and a.umspannwerk_id == b.umspannwerk_id:
        return 4
    return 6


@dataclass
class Vorschlag:
    erzeuger: Teilnehmer
    abnehmer: list[Teilnehmer]
    zonelevel: int
    kwh_je_abnehmer: dict[int, float]

    @property
    def kwh_gesamt(self) -> float:
        return sum(self.kwh_je_abnehmer.values())

    @property
    def ueberschuss_kwh(self) -> float:
        ue = ueberschuss_profil(self.erzeuger)
        return sum(tage * sum(ue[n]) for n, tage, *_ in JAHRESZEITEN)

    @property
    def auslastung(self) -> float:
        u = self.ueberschuss_kwh
        return self.kwh_gesamt / u if u else 0.0


def gruppe_vorschlagen(erzeuger: Teilnehmer, kandidaten: list[Teilnehmer],
                       max_abnehmer: int = MAX_ABNEHMER) -> Vorschlag | None:
    """Gierig: nimmt nacheinander den Abnehmer mit dem größten Zusatznutzen,
    nur innerhalb der besten gemeinsamen Zone."""
    nach_zone: dict[int, list[Teilnehmer]] = {}
    for k in kandidaten:
        z = zonelevel(erzeuger, k)
        if z is not None and k.einwilligung_daten:
            nach_zone.setdefault(z, []).append(k)
    if not nach_zone:
        return None

    bester: Vorschlag | None = None
    for zone in sorted(nach_zone):
        gewaehlt: list[Teilnehmer] = []
        rest = list(nach_zone[zone])
        aktuell = 0.0
        while rest and len(gewaehlt) < max_abnehmer:
            def zuwachs(k):
                return sum(geteilte_menge(erzeuger, gewaehlt + [k]).values()) - aktuell
            kandidat = max(rest, key=zuwachs)
            plus = zuwachs(kandidat)
            if plus < 50:  # weniger als 50 kWh Zusatznutzen pro Jahr lohnt nicht
                break
            gewaehlt.append(kandidat)
            rest.remove(kandidat)
            aktuell += plus
        if gewaehlt:
            v = Vorschlag(erzeuger, gewaehlt, zone, geteilte_menge(erzeuger, gewaehlt))
            # Nähere Zone bevorzugen, außer eine fernere bringt deutlich mehr
            if bester is None or v.kwh_gesamt > bester.kwh_gesamt * 1.5:
                bester = v
    return bester


def fairer_preis(einspeisetarif_ct: float, bezugspreis_ct: float) -> float:
    """Vorschlag: Mitte zwischen Einspeisetarif und Energiepreis des Abnehmers."""
    return round((einspeisetarif_ct + bezugspreis_ct) / 2, 2)
