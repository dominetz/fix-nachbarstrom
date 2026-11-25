# Fix Nachbarstrom

Plattform für zwei Modelle:

- **P2P (§ 68 ElWG):** Erzeuger und Abnehmer melden sich an, mit einem Wunschnachbarn oder ohne. Die Plattform schlägt passende Gruppen vor (Überschuss des einen, Netzbezug des anderen), erstellt Vertrag und Vollmacht zur digitalen Unterschrift, bereitet die Anmeldung auf ebUtilities und im EDA-Portal vor, erinnert an die Zustimmung im Netzportal. FIX verrechnet dem Erzeuger einmal die Einrichtung. Will er Auslesung, Monitoring und Rechnungen über FIX, rechnet die Plattform in seinem Namen ab und FIX verrechnet dazu je geteilter kWh. Bei einer Schenkung verrechnet FIX nichts.
- **GEA (gemeinschaftliche Erzeugungsanlage):** Ein Haus mit PV-Anlage und mehreren Wohnungen. Die Kunden registrieren sich wie gewohnt über EDA/ebUtilities. Jeden Monat wird der EDA-Export hochgeladen (vom Betreiber selbst im Kundenbereich oder von FIX), die Plattform rechnet die Zahlen je Wohnung aus und liefert die Abrechnungsgrundlage. Wahlweise rechnet FIX im Namen des Betreibers ab.

In beiden Modellen kann FIX je geteilter kWh eine Servicegebühr verrechnen (Rechnung von FIX PV GmbH an den Erzeuger bzw. Betreiber).

**P2P-Gebühren:** FIX verrechnet dem Erzeuger immer die Einrichtungsgebühr (einmal, nach der Unterschrift) und eine Servicegebühr je geteilter kWh aus den EDA-Daten. Je Gruppe unter Einstellungen → „Rechnungen an die Abnehmer“ wird nur gewählt, wer die Abnehmer abrechnet (bis zur ersten Unterschrift änderbar, Vertrag, Vollmacht und Infoblatt richten sich danach):

| Rechnungen an die Abnehmer | Was FIX verrechnet |
|---|---|
| Über uns | Einrichtung plus Servicegebühr. FIX stellt die Rechnungen an die Abnehmer im Namen des Erzeugers. |
| Stellt der Erzeuger selbst | Einrichtung plus Servicegebühr. Der Erzeuger sieht in seinem Bereich die geteilten kWh je Abnehmer und Monat. |
| Schenkung (Preis 0) | Nur die Einrichtung, keine Rechnungen an die Abnehmer. Mit `NACHBARSTROM_SCHENKUNG_GEBUEHR=ja` auch die Servicegebühr. |

Vorschläge für neue Gruppen in der Einstellungsdatei: `NACHBARSTROM_P2P_MODELL` (`verwaltet` = über uns, Standard, oder `selbst`), `NACHBARSTROM_EINRICHTUNG_EUR` (Euro netto, Standard 150), `NACHBARSTROM_P2P_GEBUEHR_CT_KWH` (Standard 1,5) und für GEAs `NACHBARSTROM_GEBUEHR_CT_KWH` (Standard 1,0) und `NACHBARSTROM_SCHENKUNG_GEBUEHR` (`ja` = Servicegebühr auch bei Schenkungen, Standard aus).

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
| Anmeldung fragt Zählpunkt Bezug und Einspeisung getrennt ab, GEA-Anfrage mit Zählpunkt der Anlage | fertig (Stufe 3) |
| P2P-Übersicht in der Verwaltung: Gruppen mit Zahlen, offene Beträge, nächste Abrechnung, Angemeldete ohne Gruppe | fertig (Stufe 3) |
| Import robust: verbundene Zellen, Wh/kWh/MWh, lange Tabellen mit Messcodes, CSV, Datum und Uhrzeit getrennt, UTC | fertig (Stufe 3) |
| Unbekannte Spalten einmal zuordnen, die Plattform merkt sich das für jeden weiteren Export | fertig (Stufe 3) |
| Export ohne erkennbares Ziel bleibt „geparkt“ und wird später zugeordnet, Aufbau-Bericht ohne Nummern und Namen | fertig (Stufe 3) |
| Werte je Gemeinschaft getrennt (ein Zählpunkt in GEA und P2P gleichzeitig) | fertig (Stufe 3) |
| EDA-Energiedatenreport (Gesamt- und Detailübersicht, Monatssummen je Zählpunkt): EC-ID, Stammdaten für Namen der Wohnungen, Hinweis bei unvollständigen Daten oder L3 | fertig (Stufe 3), mit echtem Bericht geprüft |
| GEA aus dem ersten Export anlegen: Anlage, Wohnungen und Gemeinschafts-ID werden erkannt | fertig (Stufe 3) |
| GEA-Seite: Zahlen je Wohnung und Monat, Tagesverlauf, Abrechnungsgrundlage als Excel | fertig (Stufe 3) |
| GEA selbst abrechnend (FIX liefert Zahlen + Gebühr) oder von FIX abgerechnet, Aufteilung dynamisch oder statisch | fertig (Stufe 3) |
| Kundenbereich Betreiber: Export hochladen, Zahlen ansehen, Grundlage herunterladen, Gebühren | fertig (Stufe 3) |
| Servicegebühr ct/kWh je Gruppe/GEA, eigene Rechnungsnummer FIX-…, Zahlungsstatus | fertig (Stufe 3), USt-Satz prüfen |
| P2P Einrichtungsgebühr je Vertrag (Rechnung FIX-…-E an den Erzeuger), je Gruppe mit oder ohne Abrechnung über FIX (dann plus Servicegebühr je kWh), Schenkung kostenlos | fertig, Beträge und Texte prüfen |
| Öffentliche Seiten `/gea` (Anfrage für ein Haus) und `/anfrage` (allgemeine Frage), Liste unter Verwaltung → Anfragen | fertig (Stufe 3) |
| Automatische EDA-Anbindung (E-Mail/KEP) | braucht Marktpartner-ID und EDA-Zugang |
| Automatischer Mailversand (SMTP), SEPA-Lastschrift | später, nach Freigabe |

## Echten GEA-Export testen

1. Plattform starten, Verwaltung öffnen, oben **GEA** → **Neue GEA**.
2. Namen eintragen (z. B. „Test GEA“), Modell und Preis wählen, den EDA-Export (Excel `.xlsx` oder CSV) auswählen und **GEA anlegen**. Der Energiedatenreport aus dem EDA-Portal (Monatssummen) reicht für Abrechnung und Monatswerte. Einen Tagesverlauf gibt es nur mit Viertelstundenwerten.
3. Kennt die Plattform eine Spalte nicht, zeigt sie die Spalte mit Beispielwerten und fragt einmal, was sie bedeutet. Die Antwort gilt für alle weiteren Exporte.
4. Danach legt sie Anlage und Wohnungen an (Namen und Adressen aus den Stammdaten des Berichts, sonst „Wohnung 1“, „Wohnung 2“ …) und zeigt die Zahlen. Mit **Excel herunterladen** gibt es die Abrechnungsgrundlage.
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

## Übergang: Dauerbetrieb am PC (bis der Server da ist)

Bis zum eigenen Server läuft die Plattform auf dem PC im Büro, mit eigenen Daten im Ordner `betrieb\` (getrennt vom Testbetrieb in `nachbarstrom.db`). Die Einstellungen stehen in `betrieb\nachbarstrom.env`, genauso aufgebaut wie später auf dem Server. Deshalb ist der Umzug ein einziger Befehl.

1. Doppelklick auf **Dauerbetrieb einrichten.bat**. Das Skript fragt nach Domain (Vorschlag `nachbarstrom.fix-pv.com`) und Verwaltungspasswort (gespeichert wird nur ein Hash). Danach fragt es einzeln, ob die Plattform bei der Anmeldung am PC von selbst startet und ob der Ruhezustand am Netzteil aus soll.
2. Die Verwaltung ist dann am PC unter `http://localhost:8010/verwaltung` erreichbar (Benutzer `admin`). Der Testbetrieb mit `Nachbarstrom starten.bat` läuft unabhängig davon weiter auf Port 8000.
3. Nach neuem Code (`git pull`) oder geänderten Einstellungen: Doppelklick auf **Dauerbetrieb neu starten.bat**. Vor jedem Start wird die Datenbank nach `betrieb\sicherung` gesichert (30 Tage).

Die Firmendaten für Impressum und Datenschutz kommen in `betrieb\nachbarstrom.env` und ziehen beim Umzug mit.

**Von außen erreichbar** ist der PC-Betrieb erst, wenn das freigeschaltet wird. Das passiert zum Launch, damit die Plattform dann so erreichbar ist wie das Dispotool: eigene Subdomain mit HTTPS, Verwaltung mit Login. Möglich ist ein Tunnel vom PC (zum Beispiel Cloudflare Tunnel, ohne offenen Port am Router) oder gleich der Server. Die persönlichen Links tragen von Anfang an die endgültige Adresse, deshalb bleiben verschickte Links beim Umzug gültig.

**Umzug auf den Server:** `powershell -ExecutionPolicy Bypass -File deploy\hochladen.ps1 -Server <IP> -Einrichten -Daten`. Das beendet den PC-Betrieb und schaltet dessen Autostart ab. Dann richtet es den Server ein und übernimmt Datenbank, Schlüssel und Firmendaten. Vorher prüft es die Datenbank, und was auf dem Server schon lag, wird gesichert. Danach den DNS-Eintrag auf den Server umstellen und `NACHBARSTROM_HOSTER` anpassen.

## Online betreiben (eigener Server in der EU)

Ein kleiner Linux-Server (Ubuntu 24.04 oder Debian 12) reicht. Die Plattform läuft dort als Dienst, Caddy davor sorgt für HTTPS mit einem Let's-Encrypt-Zertifikat. Alles liegt im Ordner `deploy/`.

**Vorher (einmalig, macht ihr selbst):**

1. Server beim Anbieter bestellen, Standort EU, eigenen SSH-Schlüssel hinterlegen. Auftragsverarbeitungsvertrag (AVV) mit dem Anbieter abschließen.
2. DNS: einen A-Eintrag (und bei IPv6 einen AAAA-Eintrag) für z. B. `nachbarstrom.fix-pv.com` auf die IP des Servers setzen.

**Einrichten (vom Windows-PC aus, im Ordner der Plattform):**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\hochladen.ps1 -Server <IP des Servers> -Einrichten
```

Das lädt den eingecheckten Stand hoch (nie Datenbank oder Schlüssel vom PC) und startet `deploy/einrichten.sh`. Das Skript fragt nach Domain und Verwaltungspasswort (gespeichert wird nur ein Hash) und fragt einzeln, ob Firewall, tägliche Sicherung und der erfundene Testbetrieb dazukommen sollen.

**Updates:** `powershell -ExecutionPolicy Bypass -File deploy\hochladen.ps1 -Server <IP>`. Vorher wird die Datenbank gesichert. Startet die neue Version nicht, bietet das Skript an, zur vorigen zurückzugehen.

**Auf dem Server:**

| Was | Wo |
|---|---|
| Code | `/opt/nachbarstrom` (vorige Version: `/opt/nachbarstrom.vorher`) |
| Datenbank, Schlüssel für persönliche Links | `/var/lib/nachbarstrom` |
| Sicherungen | `/var/lib/nachbarstrom/sicherung` (manuell: `sudo -u nachbarstrom nachbarstrom-sicherung`) |
| Einstellungen, Firmendaten | `/etc/nachbarstrom/nachbarstrom.env`, danach `systemctl restart nachbarstrom` |
| Passwort ändern | `bash /opt/nachbarstrom/deploy/passwort.sh` |
| Protokoll | `journalctl -u nachbarstrom` |

`einrichten.sh` ist für einen frischen Server gedacht. Läuft dort schon ein Webserver auf Port 80 oder 443 (etwa das Dispo-Portal mit eigenem Caddy), hört es auf, ohne etwas zu ändern. Auf so einem Server wird die Plattform hinter dem vorhandenen Webserver eingebunden. Das planen wir beim Launch gemeinsam.

Die Sicherungen liegen auf demselben Server. Für den Fall, dass der Server ausfällt, zusätzlich die Sicherung des Anbieters buchen oder die Dateien regelmäßig herunterladen.

Vom Testbetrieb in den Echtbetrieb: `systemctl stop nachbarstrom`, `/var/lib/nachbarstrom/nachbarstrom.db` wegsichern (umbenennen), `systemctl start nachbarstrom`. Die Plattform beginnt dann mit einer leeren Datenbank.

**Schutz:** Die Verwaltung ist nur mit Passwort erreichbar, nach 10 falschen Versuchen ist die Adresse 15 Minuten gesperrt. Persönliche Links und die Verwaltung erscheinen nicht in Suchmaschinen. Schriften kommen vom eigenen Server, es werden keine Inhalte von fremden Servern geladen und keine Cookies gesetzt.

**Impressum und Datenschutz** (`/impressum`, `/datenschutz`) sind Entwürfe mit Platzhaltern. Firmendaten in der Einstellungsdatei eintragen, Texte rechtlich prüfen lassen und erst dann `NACHBARSTROM_RECHTSTEXTE_GEPRUEFT=ja` setzen. Bis dahin steht auf beiden Seiten ein Entwurfshinweis.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Annahmen, die noch bestätigt werden müssen

- Frist für die Zustimmung im Netzportal: 7 Tage (Handbuch Vorarlberg Netz), Koordinationsstelle nennt 30 Tage.
- PV-Ertrag 1.050 kWh je kWp und Jahr, Lastprofile vereinfacht. Durch echte Messwerte ersetzen.
- Aufbau des EDA-Exports: gebaut nach dem bekannten Aufbau (eine Spalte je Zählpunkt und Messgröße) plus lange Tabellen. Mit dem ersten echten Export prüfen.
- Servicegebühr und Einrichtungsgebühr: 20 % USt angenommen (FIX PV GmbH an Erzeuger/Betreiber). Mit dem Steuerberater prüfen.
- P2P „Erzeuger stellt die Rechnungen selbst“: FIX bleibt laut Vertrag und Vollmacht für An- und Abmeldung und die Messdaten zuständig und bekommt die EDA-Daten weiter. Ob die Servicegebühr in diesem Fall so im Vertrag mit dem Erzeuger stehen muss, rechtlich prüfen.
- GEA, von FIX abgerechnet: Rechnung im Namen des Betreibers (Vollmacht). Rolle und Rechnungstext rechtlich prüfen.
- Statische Aufteilung in der GEA: Anteil je Wohnung in %, Rest der Wohnung bleibt beim Lieferanten. Bei dynamischer Aufteilung kommen die Anteile aus dem Export.
- Rechnungstext und Kleinunternehmer-Hinweis mit dem Steuerberater prüfen.
- Einfache elektronische Unterschrift für Vertrag und Vollmacht juristisch bestätigen lassen.

Keine Rechtsberatung.
