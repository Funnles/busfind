#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lädt den offiziellen VGN-GTFS und schreibt fixtures/stops_vgn.tsv.

Quelle: https://www.vgn.de/opendata/GTFS.zip
Lizenz: CC BY 3.0 DE — «VGN – Verkehrsverbund Großraum Nürnberg GmbH»

    python3 scripts/import_gtfs.py              # Download + Import
    python3 scripts/import_gtfs.py GTFS.zip     # aus lokaler Datei
"""

from __future__ import print_function

import csv
import io
import os
import sys
import zipfile

try:
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import Request, urlopen

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "fixtures", "stops_vgn.tsv")
UA = "busfind/0.2 (+https://github.com/Funnles/busfind)"

GTFS_URLS = (
    "https://www.vgn.de/opendata/GTFS.zip",
    "http://www.vgn.de/opendata/GTFS.zip",
    # Mobility Database Spiegel des gleichen Feeds
    "https://storage.googleapis.com/storage/v1/b/mdb-latest/o/"
    "de-bayern-verkehrsverbund-grossraum-nurnberg-vgn-gtfs-858.zip?alt=media",
)

# location_type: 0 stop, 1 station — behalten
# 2 entrance, 3 generic, 4 boarding area — weglassen
SKIP_TYPES = {"2", "3", "4"}


def download_gtfs(dest, urls=GTFS_URLS, timeout=90):
    last = None
    for url in urls:
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 1000 or data[:2] != b"PK":
                last = ValueError("keine ZIP-Datei von %s (%d Byte)" % (
                    url, len(data)))
                continue
            with open(dest, "wb") as f:
                f.write(data)
            return dest, url, len(data)
        except Exception as e:
            last = e
    raise RuntimeError("GTFS-Download fehlgeschlagen: %s" % last)


def _open_stops_txt(zf):
    names = {n.replace("\\", "/"): n for n in zf.namelist()}
    for cand in ("stops.txt", "Stops.txt", "google_transit/stops.txt"):
        if cand in names:
            return zf.open(names[cand])
    for logical, raw in names.items():
        if logical.lower().endswith("stops.txt"):
            return zf.open(raw)
    raise KeyError("stops.txt fehlt im GTFS-Archiv: %s" % list(names)[:12])


def _cell(row, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k].strip()
        lk = k.lower()
        for rk, rv in row.items():
            if rk.lower() == lk and rv not in (None, ""):
                return rv.strip()
    return ""


def parse_stops_txt(raw_bytes):
    """GTFS stops.txt -> [{id, name}, ...] eindeutige Fahrgast-Halte."""
    text = raw_bytes.decode("utf-8-sig")
    if "\x00" in text[:200]:
        text = raw_bytes.decode("utf-16")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    parents = {}
    children = []
    standalone = []
    for row in reader:
        loc = _cell(row, "location_type") or "0"
        if loc in SKIP_TYPES:
            continue
        sid = _cell(row, "stop_id")
        name = _cell(row, "stop_name")
        if not sid or not name:
            continue
        parent = _cell(row, "parent_station")
        rec = {"id": sid, "name": name, "parent": parent, "loc": loc}
        if loc == "1":
            parents[sid] = rec
        elif parent:
            children.append(rec)
        else:
            standalone.append(rec)

    # Plattformen mit Eltern-Station weglassen — die Station reicht
    parent_ids = set(parents)
    kept = list(parents.values()) + standalone
    for rec in children:
        if rec["parent"] not in parent_ids:
            kept.append(rec)

    # gleicher Anzeigename nur einmal (erste = meist die Station)
    seen = set()
    out = []
    for rec in kept:
        key = " ".join(rec["name"].lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": rec["id"], "name": rec["name"]})
    out.sort(key=lambda r: r["name"].lower())
    return out


def stops_from_zip(path):
    with zipfile.ZipFile(path) as zf:
        with _open_stops_txt(zf) as fh:
            raw = fh.read()
    return parse_stops_txt(raw)


def write_tsv(stops, path, source=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write("# VGN-Haltestellen aus offiziellem GTFS\n")
        f.write("# Quelle: %s\n" % (source or "https://www.vgn.de/opendata/GTFS.zip"))
        f.write("# Lizenz: CC BY 3.0 DE — VGN – Verkehrsverbund Großraum Nürnberg GmbH\n")
        f.write("id\tname\n")
        for s in stops:
            name = s["name"].replace("\t", " ").replace("\n", " ")
            f.write("%s\t%s\n" % (s["id"], name))
    os.replace(tmp, path)


def load_tsv(path):
    stops = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            if line.startswith("id\t"):
                continue
            sid, sep, name = line.rstrip("\n").partition("\t")
            if not sep:
                continue
            stops.append({"id": sid, "name": name})
    return stops


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    zip_path = None
    source = GTFS_URLS[0]
    if argv:
        zip_path = argv[0]
        source = zip_path
    else:
        zip_path = os.path.join(HERE, "fixtures", "GTFS.zip")
        print("Lade %s …" % GTFS_URLS[0], file=sys.stderr)
        _, source, n = download_gtfs(zip_path)
        print("OK %s (%d Byte)" % (source, n), file=sys.stderr)
    stops = stops_from_zip(zip_path)
    write_tsv(stops, OUT, source=source)
    print("geschrieben: %s (%d Haltestellen)" % (OUT, len(stops)), file=sys.stderr)
    if os.path.basename(zip_path) == "GTFS.zip" and not argv:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
