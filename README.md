# Fix Nachbarstrom

Plattform für Peer-to-Peer-Stromverträge (§ 68 ElWG): Erzeuger und Abnehmer melden sich an, die Plattform schlägt passende Gruppen vor, erstellt Vertrag und Vollmacht zur digitalen Unterschrift, bereitet die Anmeldung auf ebUtilities und im EDA-Portal vor, erinnert an die Zustimmung im Netzportal und erstellt Rechnungen im Namen des Erzeugers.

FIX PV handelt als bevollmächtigter Dienstleister, nicht als Organisator.

## Stand (Stufe 1 und 2)

| Funktion | Stand |
|---|---|
| Anmeldung Erzeuger/Abnehmer mit Einwilligung | fertig |
| Einladungslink („Erzeuger lädt Nachbarn ein“) | fertig |
| Matching nach Netzbetreiber, Nahebereich und Lastprofil (96 Viertelstunden, 3 Jahreszeiten) | fertig, mit Standardprofilen |
| Vertrag und Vollmacht, digitale Unterschrift (Name tippen, Zeit, IP) | fertig, Texte juristisch prüfen |
| Seite mit allen Daten für ebUtilities und EDA-Portal | fertig, Eintragen per Hand |
| Status je Gruppe, Zustimmung je Teilnehmer | fertig |
| Erinnerungsliste mit fertigem Text | fertig, Versand per Hand |
| Abrechnung aus CSV-Report, Rechnung mit Mindestinhalt nach § 69 | fertig |
| Persönlicher Bereich je Teilnehmer (Stand, offene Schritte, Rechnungen, Infoblatt) | fertig (Stufe 2) |
| Postausgang: alle Mails als Entwurf, Öffnen im Mailprogramm, „gesendet“ markieren | fertig (Stufe 2), die Plattform verschickt nichts selbst |
| Zahlung: IBAN des Erzeugers, QR-Code (EPC/GiroCode) auf jeder Rechnung | fertig (Stufe 2) |
| Informationsblatt und Preisänderung mit 1 Monat Vorlauf (§ 69 ElWG) | fertig (Stufe 2), Texte juristisch prüfen |
| Kennzahlen in der Übersicht | fertig (Stufe 2) |
| Automatische EDA-Anbindung (E-Mail/KEP) | braucht Marktpartner-ID und EDA-Zugang |
| Automatischer Mailversand (SMTP), SEPA-Lastschrift | später, nach Freigabe |

## Starten unter Windows

Doppelklick auf `Nachbarstrom starten.bat`. Beim ersten Start wird alles eingerichtet (Python muss installiert sein). Das Passwort für die Verwaltung wird bei jedem Start abgefragt und nicht gespeichert. Die Daten liegen in `nachbarstrom.db` im selben Ordner.

## Starten (Linux/macOS)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export NACHBARSTROM_ADMIN_PASSWORD='…'   # ohne Passwort ist die Verwaltung gesperrt
export NACHBARSTROM_SECRET='…'           # langer Zufallswert für die Unterschrifts-Links
python demo_daten.py                     # optional: erfundene Beispieldaten
uvicorn app.main:app --port 8000
```

Öffentlich: `http://localhost:8000/`. Verwaltung: `http://localhost:8000/verwaltung` (Benutzer `admin`).

Weitere Einstellungen in `app/config.py` (Firmendaten, Frist für die Zustimmung, Basis-URL).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Annahmen, die noch bestätigt werden müssen

- Frist für die Zustimmung im Netzportal: 7 Tage (Handbuch Vorarlberg Netz), Koordinationsstelle nennt 30 Tage.
- PV-Ertrag 1.050 kWh je kWp und Jahr, Lastprofile vereinfacht. Durch echte Messwerte ersetzen.
- Format des EDA-Datenreports: erwartet wird `zaehlpunkt;von;bis;kwh`. An den echten Export anpassen.
- Rechnungstext und Kleinunternehmer-Hinweis mit dem Steuerberater prüfen.
- Einfache elektronische Unterschrift für Vertrag und Vollmacht juristisch bestätigen lassen.

Keine Rechtsberatung.
