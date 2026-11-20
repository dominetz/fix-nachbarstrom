"""Schutz für den Betrieb im Internet.

* Das Verwaltungspasswort kann als Hash hinterlegt werden (PBKDF2-SHA256), dann
  steht es nirgends im Klartext. Hash erzeugen: ``python -m app.sicherheit``.
* Nach zu vielen falschen Anmeldungen von einer Adresse ist die Verwaltung für
  diese Adresse eine Weile gesperrt.
"""
import getpass
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

ITERATIONEN = 600_000


def passwort_hash(passwort: str, salz: str | None = None, iterationen: int = ITERATIONEN) -> str:
    salz = salz or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", passwort.encode(), bytes.fromhex(salz), iterationen).hex()
    # Doppelpunkte statt $, damit der Hash in Shell und systemd-Umgebungsdateien nichts auslöst
    return f"pbkdf2_sha256:{iterationen}:{salz}:{h}"


_bestaetigt: dict[str, str] = {}  # gespeicherter Hash -> Prüfsumme des zuletzt bestätigten Passworts


def passwort_pruefen(passwort: str, gespeichert: str) -> bool:
    """Passwort gegen den Hash prüfen. Ein bestätigtes Passwort wird kurz gemerkt (nur als
    Prüfsumme), sonst würde jede Seite der Verwaltung spürbar langsamer."""
    try:
        verfahren, iterationen, salz, soll = gespeichert.split(":")
        if verfahren != "pbkdf2_sha256":
            return False
        schnell = hashlib.sha256((salz + passwort).encode()).hexdigest()
        if gespeichert in _bestaetigt and hmac.compare_digest(_bestaetigt[gespeichert], schnell):
            return True
        ist = hashlib.pbkdf2_hmac("sha256", passwort.encode(), bytes.fromhex(salz), int(iterationen)).hex()
    except ValueError:
        return False
    if hmac.compare_digest(ist, soll):
        _bestaetigt[gespeichert] = schnell
        return True
    return False


class Sperre:
    """Zählt Fehlversuche je Adresse. Ab `versuche` Fehlversuchen innerhalb von `fenster`
    Sekunden ist die Adresse bis `fenster` Sekunden nach dem letzten Fehlversuch gesperrt."""

    def __init__(self, versuche: int = 10, fenster: int = 900):
        self.versuche, self.fenster = versuche, fenster
        self._fehler: dict[str, deque] = defaultdict(deque)

    def _aufraeumen(self, adresse: str, jetzt: float) -> deque:
        liste = self._fehler[adresse]
        while liste and liste[0] < jetzt - self.fenster:
            liste.popleft()
        return liste

    def gesperrt(self, adresse: str) -> bool:
        return len(self._aufraeumen(adresse, time.monotonic())) >= self.versuche

    def fehlversuch(self, adresse: str) -> None:
        self._aufraeumen(adresse, time.monotonic()).append(time.monotonic())
        if len(self._fehler) > 10_000:  # Speicher begrenzen
            self._fehler.clear()

    def erfolg(self, adresse: str) -> None:
        self._fehler.pop(adresse, None)


if __name__ == "__main__":
    erstes = getpass.getpass("Neues Passwort für die Verwaltung: ")
    if len(erstes) < 12:
        raise SystemExit("Bitte mindestens 12 Zeichen verwenden.")
    if getpass.getpass("Nochmal zur Kontrolle: ") != erstes:
        raise SystemExit("Die beiden Eingaben stimmen nicht überein.")
    print(passwort_hash(erstes))
