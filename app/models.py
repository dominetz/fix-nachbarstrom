"""Datenmodell der Plattform."""
from datetime import date, datetime
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Field, SQLModel

WIEN = ZoneInfo("Europe/Vienna")


def jetzt() -> datetime:
    return datetime.now(WIEN)


class Rolle(str, Enum):
    ERZEUGER = "erzeuger"
    ABNEHMER = "abnehmer"


class Profiltyp(str, Enum):
    HAUSHALT = "haushalt"
    HAUSHALT_TAGSUEBER = "haushalt_tagsueber"  # Homeoffice, Pension
    WAERMEPUMPE = "waermepumpe"
    EAUTO = "eauto"  # lädt tagsüber zu Hause
    GEWERBE = "gewerbe"  # Büro, Praxis, Betrieb Mo–Fr


class GruppenStatus(str, Enum):
    VORSCHLAG = "vorschlag"
    VERTRAG_OFFEN = "vertrag_offen"
    UNTERSCHRIEBEN = "unterschrieben"
    REGISTRIERT = "registriert"  # auf ebUtilities angelegt
    EDA_ANGEMELDET = "eda_angemeldet"  # Zählpunkte im EDA-Portal angelegt
    AKTIV = "aktiv"  # alle Zustimmungen da, Daten fließen
    BEENDET = "beendet"


STATUS_TEXT = {
    GruppenStatus.VORSCHLAG: "Vorschlag",
    GruppenStatus.VERTRAG_OFFEN: "Vertrag zur Unterschrift",
    GruppenStatus.UNTERSCHRIEBEN: "Alle haben unterschrieben",
    GruppenStatus.REGISTRIERT: "Auf ebUtilities registriert",
    GruppenStatus.EDA_ANGEMELDET: "Zählpunkte angemeldet, Zustimmung offen",
    GruppenStatus.AKTIV: "Aktiv",
    GruppenStatus.BEENDET: "Beendet",
}


class Teilnehmer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    rolle: Rolle
    vorname: str
    nachname: str
    firma: Optional[str] = None
    strasse: str
    plz: str
    ort: str
    email: str
    telefon: Optional[str] = None
    zaehlpunkt: str = Field(index=True, unique=True)
    netzbetreiber: str  # 6-stellige Kennung aus dem Zählpunkt
    trafo_id: Optional[str] = None  # Nahebereichs-ID Trafostation
    umspannwerk_id: Optional[str] = None  # Nahebereichs-ID Umspannwerk
    profiltyp: Profiltyp = Profiltyp.HAUSHALT
    jahresverbrauch_kwh: float = 0  # Abnehmer, beim Erzeuger der Eigenverbrauch
    anlage_kwp: float = 0  # nur Erzeuger
    iban: Optional[str] = None  # nur Erzeuger: Konto für die Zahlungen der Abnehmer
    einladung_von: Optional[int] = Field(default=None, foreign_key="teilnehmer.id")
    einwilligung_daten: bool = False
    einwilligung_zeit: Optional[datetime] = None
    erstellt: datetime = Field(default_factory=jetzt)

    @property
    def name(self) -> str:
        person = f"{self.vorname} {self.nachname}"
        return f"{self.firma} ({person})" if self.firma else person


class Einladung(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True)
    erzeuger_id: int = Field(foreign_key="teilnehmer.id")
    erstellt: datetime = Field(default_factory=jetzt)
    eingeloest_von: Optional[int] = Field(default=None, foreign_key="teilnehmer.id")


class Gruppe(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    erzeuger_id: int = Field(foreign_key="teilnehmer.id")
    status: GruppenStatus = GruppenStatus.VORSCHLAG
    zonelevel: int = 6  # 3 lokal (Trafo), 4 regional (Umspannwerk), 6 ohne Rabatt
    preis_ct_kwh: float = 0.0  # netto; 0 = Schenkung
    preis_neu_ct_kwh: Optional[float] = None  # angekündigte Preisänderung
    preis_neu_ab: Optional[date] = None  # frühestens ein Monat nach Ankündigung (§ 69 ElWG)
    ust_satz: float = 0.0  # 0.0 Kleinunternehmer, 0.20 sonst; mit Steuerberater klären
    rechnung_monatlich: bool = False  # sonst jährlich
    aktiv_seit: Optional[date] = None  # erster Tag mit geteilten Daten
    erwartete_kwh_jahr: float = 0.0
    marktpartner_id: Optional[str] = None  # PP…, nach ebUtilities-Registrierung
    gemeinschafts_id: Optional[str] = None  # AT… 33-stellig
    erstellt: datetime = Field(default_factory=jetzt)
    notiz: Optional[str] = None


class Mitglied(SQLModel, table=True):
    """Ein Abnehmer in einer Gruppe."""
    id: Optional[int] = Field(default=None, primary_key=True)
    gruppe_id: int = Field(foreign_key="gruppe.id")
    teilnehmer_id: int = Field(foreign_key="teilnehmer.id")
    erwartete_kwh_jahr: float = 0.0


class Unterschrift(SQLModel, table=True):
    """Einfache elektronische Unterschrift (Name, Zeit, IP)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    gruppe_id: int = Field(foreign_key="gruppe.id")
    teilnehmer_id: int = Field(foreign_key="teilnehmer.id")
    dokument: str  # "vertrag" oder "vollmacht"
    name_getippt: str
    zeit: datetime = Field(default_factory=jetzt)
    ip: Optional[str] = None


class Zustimmung(SQLModel, table=True):
    """Zustimmung im Netzportal, je Zählpunkt und Gruppe."""
    id: Optional[int] = Field(default=None, primary_key=True)
    gruppe_id: int = Field(foreign_key="gruppe.id")
    teilnehmer_id: int = Field(foreign_key="teilnehmer.id")
    freigegeben_am: Optional[date] = None  # Netzbetreiber hat freigegeben
    erteilt_am: Optional[date] = None  # Kunde hat zugestimmt
    letzte_erinnerung: Optional[date] = None
    erinnerungen: int = 0


class Rechnung(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nummer: str = Field(index=True, unique=True)
    gruppe_id: int = Field(foreign_key="gruppe.id")
    abnehmer_id: int = Field(foreign_key="teilnehmer.id")
    von: date
    bis: date
    kwh: float
    preis_ct_kwh: float
    ust_satz: float  # 0.0 oder 0.20
    netto: float
    ust: float
    brutto: float
    datum: date = Field(default_factory=date.today)
    faellig: date
    bezahlt_am: Optional[date] = None
    gemahnt_am: Optional[date] = None


class Nachricht(SQLModel, table=True):
    """Mail-Entwurf im Postausgang. Gesendet wird nur von Hand."""
    id: Optional[int] = Field(default=None, primary_key=True)
    teilnehmer_id: int = Field(foreign_key="teilnehmer.id")
    gruppe_id: Optional[int] = Field(default=None, foreign_key="gruppe.id")
    art: str  # willkommen, unterschrift, rechnung, preisaenderung, kuendigung
    betreff: str
    text: str
    erstellt: datetime = Field(default_factory=jetzt)
    gesendet_am: Optional[datetime] = None


class Messwert(SQLModel, table=True):
    """Viertelstundenwert eines Zählpunkts aus dem EDA-Export (kWh).

    beginn ist der Beginn der Viertelstunde als Text "JJJJ-MM-TT HH:MM" (Ortszeit),
    damit nach Monat und Tag einfach summiert werden kann.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    zaehlpunkt: str = Field(index=True)
    beginn: str = Field(index=True)
    verbrauch: float = 0.0  # Gesamtverbrauch lt. Messung
    anteil: float = 0.0  # zugeteilter Anteil an der gemeinsamen Erzeugung
    eigendeckung: float = 0.0  # davon tatsächlich verbraucht (wird verrechnet)
    erzeugung: float = 0.0  # Erzeuger: in die Gemeinschaft eingespeist
    restueberschuss: float = 0.0  # Erzeuger: nicht geteilt, normal ins Netz
