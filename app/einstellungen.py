"""Einstellungsdatei im Format NAME=WERT, gleich am PC (betrieb\\nachbarstrom.env) und auf dem
Server (/etc/nachbarstrom/nachbarstrom.env). Leere Zeilen und Zeilen mit # werden übersprungen,
Werte dürfen in Anführungszeichen stehen.

Am PC liest config.py die Datei, wenn NACHBARSTROM_EINSTELLUNGEN auf sie zeigt. Beim Umzug auf
den Server übernimmt ``python -m app.einstellungen uebernehmen <vom PC> <Server>`` die Firmendaten.
"""
import os
import sys
from pathlib import Path

# Bleiben beim Umzug, wie der Server sie festlegt: Pfade, Adresse, Zugang. Den Hosting-Anbieter
# legt der Server neu fest, er steht in der Datenschutzerklärung und ändert sich beim Umzug.
NICHT_UEBERNEHMEN = {
    "NACHBARSTROM_DB", "NACHBARSTROM_SECRET", "NACHBARSTROM_SECRET_FILE", "NACHBARSTROM_TESTDATEN",
    "NACHBARSTROM_BASE_URL", "NACHBARSTROM_ADMIN_USER", "NACHBARSTROM_ADMIN_PASSWORD",
    "NACHBARSTROM_ADMIN_PASSWORD_HASH", "NACHBARSTROM_EINSTELLUNGEN", "NACHBARSTROM_HOSTER",
}


def lesen(pfad) -> dict[str, str]:
    werte = {}
    for zeile in Path(pfad).read_text(encoding="utf-8-sig").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = (teil.strip() for teil in zeile.split("=", 1))
        if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
            wert = wert[1:-1]
        werte[name] = wert
    return werte


def laden(pfad) -> None:
    """Werte in die Umgebung übernehmen. Was dort schon gesetzt ist, gewinnt."""
    for name, wert in lesen(pfad).items():
        os.environ.setdefault(name, wert)


def _name(zeile: str) -> str | None:
    rest = zeile.strip().lstrip("#").strip()
    return rest.split("=", 1)[0].strip() if "=" in rest else None


def uebernehmen(von, nach) -> tuple[list[str], list[str]]:
    """Firmendaten und andere Einstellungen von `von` nach `nach` übertragen. Eine vorhandene oder
    auskommentierte Zeile wird ersetzt, weitere Zeilen mit demselben Namen fallen weg, der Rest
    kommt ans Ende. Gibt (übernommen, übersprungen) zurück."""
    neu, uebersprungen = {}, []
    for name, wert in lesen(von).items():
        if not name.startswith("NACHBARSTROM_") or name in NICHT_UEBERNEHMEN:
            continue
        if any(z in wert for z in '"$\\`\n'):
            uebersprungen.append(name)  # in systemd-Dateien heikel, lieber von Hand eintragen
            continue
        neu[name] = wert
    ziel = Path(nach)
    zeilen, gesetzt = [], set()
    for zeile in ziel.read_text(encoding="utf-8").splitlines():
        name = _name(zeile)
        if name in neu:
            if name not in gesetzt:
                zeilen.append(f'{name}="{neu[name]}"')
                gesetzt.add(name)
            continue
        zeilen.append(zeile)
    zeilen += [f'{name}="{wert}"' for name, wert in neu.items() if name not in gesetzt]
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return sorted(neu), uebersprungen


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "uebernehmen":
        raise SystemExit("Aufruf: python -m app.einstellungen uebernehmen <Datei vom PC> <Datei am Server>")
    uebernommen, uebersprungen = uebernehmen(sys.argv[2], sys.argv[3])
    print("Übernommen:", ", ".join(uebernommen) or "nichts")
    if uebersprungen:
        print("Bitte von Hand eintragen (Sonderzeichen):", ", ".join(uebersprungen))
