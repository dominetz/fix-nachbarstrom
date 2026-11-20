"""Sicherung von Datenbank und Schlüssel für die persönlichen Links, für den Betrieb am PC.
Auf dem Server macht das deploy/sicherung.sh.

    python -m app.sicherung                 # nach <Datenordner>\\sicherung, 30 Tage aufheben
    python -m app.sicherung --umzug ORDNER  # Kopie für den Umzug auf den Server

Die Einstellungen (welche Datenbank) kommen aus config.py, am PC also aus NACHBARSTROM_EINSTELLUNGEN.
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


def db_pfad(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError("Nur SQLite-Datenbanken lassen sich so sichern.")
    return Path(url.removeprefix("sqlite:///"))


def kopieren(db: Path, ziel: Path) -> None:
    """Sichere Kopie, auch während die Plattform läuft (SQLite-Backup statt Dateikopie)."""
    quelle, kopie = sqlite3.connect(db), sqlite3.connect(ziel)
    try:
        quelle.backup(kopie)
    finally:
        kopie.close()
        quelle.close()


def sichern(db: Path, geheimnis: Path, ordner: Path, tage: int = 30) -> Path | None:
    if not db.exists():
        return None
    ordner.mkdir(parents=True, exist_ok=True)
    stand = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    ziel = ordner / f"nachbarstrom-{stand}.db"
    kopieren(db, ziel)
    if geheimnis.exists():
        (ordner / f"nachbarstrom-{stand}.geheimnis").write_bytes(geheimnis.read_bytes())
    grenze = time.time() - tage * 86400
    for alt in ordner.glob("nachbarstrom-*"):
        if alt.is_file() and alt.stat().st_mtime < grenze:
            alt.unlink()
    return ziel


def fuer_umzug(db: Path, geheimnis: Path, ordner: Path) -> None:
    if not db.exists() or not geheimnis.exists():
        raise SystemExit(f"Datenbank oder Schlüssel fehlt ({db}, {geheimnis}).")
    ordner.mkdir(parents=True, exist_ok=True)
    kopieren(db, ordner / "nachbarstrom.db")
    (ordner / "nachbarstrom.geheimnis").write_bytes(geheimnis.read_bytes())


if __name__ == "__main__":
    from . import config
    db = db_pfad(config.DATENBANK)
    if len(sys.argv) == 3 and sys.argv[1] == "--umzug":
        fuer_umzug(db, config.GEHEIMNIS_DATEI, Path(sys.argv[2]))
        print(f"Kopie für den Umzug in {sys.argv[2]}")
    elif len(sys.argv) == 1:
        ziel = sichern(db, config.GEHEIMNIS_DATEI, db.parent / "sicherung")
        print(f"Gesichert: {ziel}" if ziel else "Keine Datenbank gefunden, nichts zu sichern.")
    else:
        raise SystemExit(__doc__)
