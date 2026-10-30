"""Legt erfundene Beispieldaten an, zum Ausprobieren. Keine echten Kunden.

    python demo_daten.py
"""
from sqlmodel import Session, SQLModel

from app.main import engine
from app.models import Profiltyp, Rolle, Teilnehmer, jetzt

NB = "008100"  # Testkennung, kein echter Netzbetreiber


def zp(n: int) -> str:
    return f"AT{NB}06800{n:020d}"


BEISPIELE = [
    ("Anna", "Sonnig", Rolle.ERZEUGER, Profiltyp.HAUSHALT, 3200, 12.0, "T-101"),
    ("Bernd", "Dach", Rolle.ERZEUGER, Profiltyp.HAUSHALT_TAGSUEBER, 4000, 8.5, "T-102"),
    ("Clara", "Nachbar", Rolle.ABNEHMER, Profiltyp.HAUSHALT, 3500, 0, "T-101"),
    ("Tischlerei", "Holz", Rolle.ABNEHMER, Profiltyp.GEWERBE, 18000, 0, "T-101"),
    ("Doris", "Wärme", Rolle.ABNEHMER, Profiltyp.WAERMEPUMPE, 7500, 0, "T-101"),
    ("Emil", "Auto", Rolle.ABNEHMER, Profiltyp.EAUTO, 5000, 0, "T-102"),
    ("Praxis", "Gesund", Rolle.ABNEHMER, Profiltyp.GEWERBE, 9000, 0, "T-103"),
]

if __name__ == "__main__":
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        for i, (vor, nach, rolle, profil, kwh, kwp, trafo) in enumerate(BEISPIELE, start=1):
            s.add(Teilnehmer(rolle=rolle, vorname=vor, nachname=nach, strasse="Beispielweg 1", plz="6800",
                             ort="Feldkirch", email=f"demo{i}@example.com", zaehlpunkt=zp(i), netzbetreiber=NB,
                             trafo_id=trafo, umspannwerk_id="UW-1", profiltyp=profil, jahresverbrauch_kwh=kwh,
                             anlage_kwp=kwp, einwilligung_daten=True, einwilligung_zeit=jetzt()))
        s.commit()
    print(f"{len(BEISPIELE)} Beispiel-Teilnehmer angelegt.")
