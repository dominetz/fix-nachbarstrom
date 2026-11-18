# Fix Nachbarstrom

Plattform für zwei Modelle:

- **P2P (§ 68 ElWG):** Erzeuger und Abnehmer melden sich an, mit einem Wunschnachbarn oder ohne. Die Plattform schlägt passende Gruppen vor (Überschuss des einen, Netzbezug des anderen), erstellt Vertrag und Vollmacht zur digitalen Unterschrift, bereitet die Anmeldung auf ebUtilities und im EDA-Portal vor, erinnert an die Zustimmung im Netzportal und erstellt Rechnungen im Namen des Erzeugers.
- **GEA (gemeinschaftliche Erzeugungsanlage):** Ein Haus mit PV-Anlage und mehreren Wohnungen. Die Kunden registrieren sich wie gewohnt über EDA/ebUtilities. Jeden Monat wird der EDA-Export hochgeladen (vom Betreiber selbst im Kundenbereich oder von FIX), die Plattform rechnet die Zahlen je Wohnung aus und liefert die Abrechnungsgrundlage. Wahlweise rechnet FIX im Namen des Betreibers ab.

In beiden Modellen kann FIX je geteilter kWh eine Servicegebühr verrechnen (Rechnung von FIX PV GmbH an den Erzeuger bzw. Betreiber).

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
| Energiedaten: Import des EDA-Monatsexports (Excel), Viertelstundenwerte, Übersicht was fehlt | fertig, Aufbau an ersten echten Export anpassen |
| Abrechnung auf Knopfdruck aus den Energiedaten (Monat/Jahr), Zahlungsstatus, Zahlungserinnerung | fertig |
| Diagramme je Monat und Tagesverlauf in Verwaltung und Kundenbereich | fertig |
| Testbetrieb: erfundene Kunden, fiktives Jahr 10/2025–09/2026 als EDA-Exporte (`python demo_daten.py --neu`) | fertig |
| Anmeldung mit Wunschnachbar, Einladung in beide Richtungen (auch Abnehmer lädt Erzeuger ein) | fertig (Stufe 3) |
| Import robust: verbundene Zellen, Wh/kWh/MWh, lange Tabellen mit Messcodes, CSV, Datum und Uhrzeit getrennt, UTC | fertig (Stufe 3) |
| Unbekannte Spalten einmal zuordnen, die Plattform merkt sich das für jeden weiteren Export | fertig (Stufe 3) |
| Export ohne erkennbares Ziel bleibt „geparkt“ und wird später zugeordnet, Aufbau-Bericht ohne Nummern und Namen | fertig (Stufe 3) |
| Werte je Gemeinschaft getrennt (ein Zählpunkt in GEA und P2P gleichzeitig) | fertig (Stufe 3) |
| GEA aus dem ersten Export anlegen: Anlage, Wohnungen und Gemeinschafts-ID werden erkannt | fertig (Stufe 3) |
| GEA-Seite: Zahlen je Wohnung und Monat, Tagesverlauf, Abrechnungsgrundlage als Excel | fertig (Stufe 3) |
| GEA selbst abrechnend (FIX liefert Zahlen + Gebühr) oder von FIX abgerechnet, Aufteilung dynamisch oder statisch | fertig (Stufe 3) |
| Kundenbereich Betreiber: Export hochladen, Zahlen ansehen, Grundlage herunterladen, Gebühren | fertig (Stufe 3) |
| Servicegebühr ct/kWh je Gruppe/GEA, eigene Rechnungsnummer FIX-…, Zahlungsstatus | fertig (Stufe 3), USt-Satz prüfen |
| Öffentliche Seiten `/gea` (Anfrage für ein Haus) und `/anfrage` (allgemeine Frage), Liste unter Verwaltung → Anfragen | fertig (Stufe 3) |
| Automatische EDA-Anbindung (E-Mail/KEP) | braucht Marktpartner-ID und EDA-Zugang |
| Automatischer Mailversand (SMTP), SEPA-Lastschrift | später, nach Freigabe |

## Echten GEA-Export testen

1. Plattform starten, Verwaltung öffnen, oben **GEA** → **Neue GEA**.
2. Namen eintragen (z. B. „Test GEA“), Modell und Preis wählen, den EDA-Export (Excel `.xlsx` oder CSV) auswählen und **GEA anlegen**.
3. Kennt die Plattform eine Spalte nicht, zeigt sie die Spalte mit Beispielwerten und fragt einmal, was sie bedeutet. Die Antwort gilt für alle weiteren Exporte.
4. Danach legt sie Anlage und Wohnungen an („Wohnung 1“, „Wohnung 2“ …, Namen später eintragen) und zeigt die Zahlen. Mit **Excel herunterladen** gibt es die Abrechnungsgrundlage.
5. Klappt das Einlesen nicht, zeigt die Plattform den Aufbau der Datei: Zählpunkte, IDs und Namen sind darin durch Platzhalter ersetzt. Diesen Bericht kann man weitergeben, ohne Kundendaten preiszugeben.

Alte Excel-Dateien (`.xls`) bitte vorher in Excel als `.xlsx` speichern. Die Plattform verschickt nichts und meldet nichts beim Netzbetreiber an.

Die Test-GEA lässt sich jederzeit auf „beendet“ stellen. Ganz neu beginnen: beim Start „Testbetrieb neu laden“ mit j beantworten, das ersetzt alle Daten durch den erfundenen Testbetrieb.

## Starten unter Windows

Doppelklick auf `Nachbarstrom starten.bat`. Beim ersten Start wird alles eingerichtet (Python muss installiert sein). Das Passwort für die Verwaltung wird bei jedem Start abgefragt und nicht gespeichert. Die Daten liegen in `nachbarstrom.db` im selben Ordner.

## Starten (Linux/macOS)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export NACHBARSTROM_ADMIN_PASSWORD='…'   # ohne Passwort ist die Verwaltung gesperrt
export NACHBARSTROM_SECRET='…'           # optional; sonst wird beim ersten Start nachbarstrom.geheimnis angelegt
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
- Aufbau des EDA-Exports: gebaut nach dem bekannten Aufbau (eine Spalte je Zählpunkt und Messgröße) plus lange Tabellen. Mit dem ersten echten Export prüfen.
- Servicegebühr: 20 % USt angenommen (FIX PV GmbH an Erzeuger/Betreiber). Mit dem Steuerberater prüfen.
- GEA, von FIX abgerechnet: Rechnung im Namen des Betreibers (Vollmacht). Rolle und Rechnungstext rechtlich prüfen.
- Statische Aufteilung in der GEA: Anteil je Wohnung in %, Rest der Wohnung bleibt beim Lieferanten. Bei dynamischer Aufteilung kommen die Anteile aus dem Export.
- Rechnungstext und Kleinunternehmer-Hinweis mit dem Steuerberater prüfen.
- Einfache elektronische Unterschrift für Vertrag und Vollmacht juristisch bestätigen lassen.

Keine Rechtsberatung.
