# busfind 🚌

**frogfind für Busse in Bamberg.**

Dein Netz drosselt auf Dial-up? vgn.de wiegt Megabyte und lädt nicht?
busfind ist wie [frogfind.com](https://frogfind.com), nur für VGN-Verbindungen:
der Server macht die Arbeit, aufs Telefon kommt nacktes HTML von **1–3 KB**.
Keine Frameworks, keine Bilder. Auf 64 kbit/s ist die Seite in unter einer
Sekunde da.

```
Startseite   ~2 KB   (Formular, Datum/Uhrzeit, Sprachwahl, Haltestellen-Hinweise)
Verbindung   ~1.6 KB (3 Varianten, Steige, Ticketpreis)
Fehler       ~1 KB
```

Sprachen: **Deutsch** (Standard), English, Українська, Русский.

## Benutzung

```
python3 server.py
```

Öffne `http://localhost:8000/`. Felder «Von» / «Nach», **Datum und Uhrzeit
der Abfahrt**, eine Taste. Beim Tippen erscheinen Vorschläge für **alle VGN-Haltestellen**
(nicht nur ZOB/Bahnhof): `ZOB`, `Klinikum`, `Markusplatz`, `Gaustadt` …
Die Liste kommt aus dem offiziellen
[VGN-GTFS](https://www.vgn.de/opendata/GTFS.zip). Ohne JavaScript greift
eine kurze HTML-Datalist, und bei mehrdeutigen Namen eine Klärungsseite.

Direkter Link:
`http://localhost:8000/r?f=ZOB&t=Bahnhof&d=2026-08-19&tm=17:30&lang=de`
(auch Halt-IDs: `f=3020200&t=3020010`).

Sprache umschalten: Links oben auf jeder Seite, oder `?lang=en|uk|ru`.

## Start mit Docker

```
git clone https://github.com/Funnles/busfind.git
cd busfind
docker compose up --build
```

Öffne `http://localhost:8000/` (auf einem Server: `http://<server-ip>:8000/`).
Stoppen: `Strg+C`. Container entfernen: `docker compose down`.

Der Fehler `no configuration file provided: not found` bedeutet, dass im
aktuellen Verzeichnis keine `docker-compose.yml` liegt — wechsle in den
Ordner, in den das Repository geklont wurde (`cd ~/busfind`), und
wiederhole den Befehl.

Ohne Compose, direkt mit Docker:

```
docker build -t busfind .
docker run -d --name busfind -p 8000:8000 --restart unless-stopped busfind
```

Der Container läuft read-only als unprivilegierter Nutzer. Offline-Demo im
Container: `BUSFIND_OFFLINE: "1"` in `docker-compose.yml` einkommentieren.

## Technik

Verbindungen kommen von der offiziellen EFA-Schnittstelle VGN (`efa.vgn.de`):

* `XML_STOPFINDER_REQUEST` — Haltestelle nach Namen
* `XML_TRIP_REQUEST2` — Verbindungen «von → nach» zur gewählten Abfahrtszeit

Die Vorschläge nutzen den [VGN-Soll-GTFS](https://www.vgn.de/opendata/GTFS.zip)
(CC BY 3.0 DE, VGN – Verkehrsverbund Großraum Nürnberg GmbH). Katalog bauen:

```
python3 scripts/import_gtfs.py              # lädt GTFS.zip und schreibt fixtures/stops_vgn.tsv
python3 scripts/import_gtfs.py GTFS.zip     # aus schon heruntergeladener Datei
```

EFA liefert Dutzende Kilobyte JSON. busfind zieht Zeiten, Steige, Linien,
Umstiege, Ticketpreis und Echtzeit-Verspätung raus und rendert minimales HTML.
Keine Abhängigkeiten, reines Python-stdlib.

Ein paar hundert Byte JS füllen die Vorschläge nach (`GET /s?q=ZOB` → JSON).
Ohne JS bleibt alles benutzbar.

Inspiriert von:

* <https://github.com/ActionRetro/FrogFind> — Internet für langsame Leitungen
* <https://github.com/becheran/vgn> — API Verkehrsverbund Großraum Nürnberg

## Umgebungsvariablen

| Variable          | Standard                  | Bedeutung                             |
|-------------------|---------------------------|---------------------------------------|
| `BUSFIND_PORT`    | `8000`                    | Server-Port                           |
| `EFA_URL`         | `https://efa.vgn.de/xml/` | EFA-Adresse (oder Spiegel)            |
| `BUSFIND_TIMEOUT` | `12`                      | Timeout der EFA-Anfragen, Sekunden    |
| `BUSFIND_OFFLINE` | aus                       | `1` — Demo mit lokalem Snapshot       |

## Offline-Demo

In `fixtures/` liegt ein EFA-Schnappschuss vom 19.08.2026
(Verbindung ZOB → Konzerthalle, Bus 906), eine kurze Demo-Liste und
`stops_vgn.tsv` — ca. 16 000 Fahrgast-Halte im VGN-Gebiet
(aus dem VGN-GTFS bzw. ZHV, gefiltert). Ist `efa.vgn.de`
nicht erreichbar, zeigt busfind automatisch die Demo; die Vorschläge
bleiben vollständig.

```
python3 test_server.py
```
