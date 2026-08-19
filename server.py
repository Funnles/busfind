#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
busfind — frogfind für Busse in Bamberg (VGN).

Idee wie frogfind.com: der Server macht die schwere Arbeit, das Telefon
bekommt nacktes HTML von 1–3 KB, das selbst bei 64 kbit/s öffnet.
Kein Framework, keine Bilder, nur stdlib. Ein winziges Stück JS füllt
die Haltestellen-Vorschläge nach; ohne JS funktioniert das Formular
weiter (HTML-Datalist + Klärungsseite).

Datenquelle — offizielle EFA-Schnittstelle VGN (efa.vgn.de):
  * XML_STOPFINDER_REQUEST — Haltestelle nach Namen
  * XML_TRIP_REQUEST2      — Verbindungen von–nach

Start:
    python3 server.py
    BUSFIND_PORT=9000 python3 server.py
    BUSFIND_OFFLINE=1 python3 server.py   # Demo mit lokalem Snapshot

Sprachen: Deutsch (Standard), English, Українська, Русский.
"""

import html as _html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# Konfig
# --------------------------------------------------------------------------

EFA_URL = os.environ.get("EFA_URL", "https://efa.vgn.de/xml/").rstrip("/")
PORT = int(os.environ.get("BUSFIND_PORT", "8000"))
OFFLINE = os.environ.get("BUSFIND_OFFLINE", "") not in ("", "0")
TIMEOUT = float(os.environ.get("BUSFIND_TIMEOUT", "12"))
UA = "busfind/0.2 (dial-up friendly)"
HERE = os.path.dirname(os.path.abspath(__file__))

LANGS = ("de", "en", "uk", "ru")
DEFAULT_LANG = "de"
LANG_NAMES = {
    "de": "Deutsch",
    "en": "English",
    "uk": "Українська",
    "ru": "Русский",
}
LANG_INDEX = {"de": 0, "en": 1, "uk": 2, "ru": 3}

WALK_PRODUCTS = ("Fussweg", "Fußweg", "footpath")

# --------------------------------------------------------------------------
# i18n  — jeder Eintrag: (de, en, uk, ru)
# --------------------------------------------------------------------------

_T = {
    "tagline": (
        "Busse in Bamberg und VGN · wenig JS · ~1 KB pro Seite",
        "Buses in Bamberg and VGN · tiny JS · ~1 KB per page",
        "Автобуси Бамберга і VGN · мало JS · ~1 КБ на сторінку",
        "автобусы Бамберга и VGN · мало JS · ~1 КБ на страницу",
    ),
    "from": ("Von", "From", "Звідки", "Откуда"),
    "to": ("Nach", "To", "Куди", "Куда"),
    "date": ("Datum", "Date", "Дата", "Дата"),
    "time": ("Uhrzeit", "Time", "Час", "Время"),
    "search": ("Suchen", "Search", "Знайти", "Найти"),
    "examples": (
        "Beispiele: ZOB · Bahnhof · Konzerthalle · Markusplatz",
        "Examples: ZOB · Bahnhof · Konzerthalle · Markusplatz",
        "Приклади: ZOB · Bahnhof · Konzerthalle · Markusplatz",
        "примеры: ZOB · Bahnhof · Konzerthalle · Markusplatz",
    ),
    "fill_both": (
        "Bitte beide Felder ausfüllen.",
        "Please fill in both fields.",
        "Заповніть обидва поля.",
        "Заполните оба поля.",
    ),
    "choose": (
        "«%s» — bitte Haltestelle wählen (%s):",
        "“%s” — please choose a stop (%s):",
        "«%s» — оберіть зупинку (%s):",
        "«%s» — выберите остановку (%s):",
    ),
    "which_from": ("von", "from", "звідки", "откуда"),
    "which_to": ("nach", "to", "куди", "куда"),
    "back": (
        "← zurück zur Suche",
        "← back to search",
        "← назад до пошуку",
        "← назад к поиску",
    ),
    "same_stop": (
        "Start und Ziel sind dieselbe Haltestelle.",
        "From and to are the same stop.",
        "Звідки і куди — одна й та сама зупинка.",
        "Откуда и куда — одно и то же место.",
    ),
    "same_stop_hint": (
        "Bitte verschiedene Haltestellen wählen.",
        "Please choose two different stops.",
        "Оберіть різні зупинки.",
        "Выберите разные остановки.",
    ),
    "not_found_page": (
        "Seite nicht gefunden.",
        "Page not found.",
        "Сторінку не знайдено.",
        "Нет такой страницы.",
    ),
    "home": ("← zur Startseite", "← home", "← на головну", "← на главную"),
    "not_in_vgn": (
        "«%s» — in VGN nicht gefunden.",
        "“%s” — not found in VGN.",
        "«%s» — у VGN не знайдено.",
        "«%s» — не найдено в VGN.",
    ),
    "not_in_vgn_hint": (
        "Das ist keine VGN-Haltestelle. Haltestellennamen eingeben "
        "(z. B. ZOB, Bahnhof, Konzerthalle).",
        "That is not a VGN stop. Type a stop name "
        "(e.g. ZOB, Bahnhof, Konzerthalle).",
        "Це не зупинка VGN. Введіть назву зупинки "
        "(наприклад: ZOB, Bahnhof, Konzerthalle).",
        "Это не остановка VGN. Пишите название остановки "
        "(например: ZOB, Bahnhof, Konzerthalle).",
    ),
    "efa_error": (
        "VGN hat mit einem Fehler geantwortet (%s).",
        "VGN returned an error (%s).",
        "VGN відповів помилкою (%s).",
        "VGN ответил ошибкой (%s).",
    ),
    "try_later": (
        "Bitte später noch einmal versuchen.",
        "Please try again in a moment.",
        "Спробуйте ще раз трохи пізніше.",
        "Попробуйте ещё раз чуть позже.",
    ),
    "refresh": ("aktualisieren", "refresh", "оновити", "обновить"),
    "new_search": ("neue Suche", "new search", "новий пошук", "новый поиск"),
    "demo": (
        "DEMO: efa.vgn.de nicht erreichbar, gezeigt wird ein Schnappschuss "
        "ZOB → Konzerthalle vom 19.08.2026",
        "DEMO: efa.vgn.de is unreachable, showing a snapshot "
        "ZOB → Konzerthalle from 19.08.2026",
        "ДЕМО: efa.vgn.de недоступний, показано знімок "
        "ZOB → Konzerthalle від 19.08.2026",
        "ДЕМО: efa.vgn.de недоступен, показан снимок "
        "ZOB → Konzerthalle от 19.08.2026",
    ),
    "no_route": (
        "Keine Verbindung gefunden.",
        "No connection found.",
        "Маршрут не знайдено.",
        "Маршрут не найден.",
    ),
    "walk": ("zu Fuß", "walk", "пішки", "пешком"),
    "no_changes": (
        "ohne Umstieg",
        "no changes",
        "без пересадок",
        "без пересадок",
    ),
    "changes": (
        "Umstiege: %s",
        "changes: %s",
        "пересадок: %s",
        "пересадок: %s",
    ),
    "zone": ("(Stufe %s)", "(zone %s)", "(рів. %s)", "(ур. %s)"),
    "plan": ("(Plan %s)", "(sched. %s)", "(план %s)", "(план %s)"),
    "departure": ("Abfahrt", "Departure", "Відправлення", "Отправление"),
    "footer": (
        "Daten: efa.vgn.de · Idee: frogfind.com",
        "data: efa.vgn.de · idea: frogfind.com",
        "дані: efa.vgn.de · ідея: frogfind.com",
        "данные: efa.vgn.de · идея: frogfind.com",
    ),
    "title_error": (
        "busfind — Fehler",
        "busfind — error",
        "busfind — помилка",
        "busfind — ошибка",
    ),
    "title_choose": (
        "busfind — bitte wählen",
        "busfind — please choose",
        "busfind — оберіть",
        "busfind — уточните",
    ),
    "walk_for": (
        "zu Fuß %s Min",
        "walk %s min",
        "пішки %s хв",
        "пешком %s мин",
    ),
}

WEEKDAYS = {
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "uk": ("пн", "вт", "ср", "чт", "пт", "сб", "нд"),
    "ru": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
}

# EFA-Produktname -> kurze Bezeichnung
PRODUCTS = {
    "Stadtbus": ("Bus", "bus", "автобус", "автобус"),
    "Regionalbus": ("Regio-Bus", "regional bus", "рег. автобус", "рег. автобус"),
    "Schnellbus": ("Express", "express", "експрес", "экспресс"),
    "Bürgerbus": ("Bürgerbus", "community bus", "Bürgerbus", "Bürgerbus"),
    "Rufbus (liniengeb.)": ("Rufbus", "on-demand bus", "Rufbus", "Rufbus"),
    "AST (flächengebunde": ("AST", "AST", "AST", "AST"),
    "Straßen-/Trambahn": ("Tram", "tram", "трамвай", "трамвай"),
    "U-Bahn": ("U-Bahn", "metro", "метро", "метро"),
    "S-Bahn": ("S-Bahn", "S-Bahn", "S-Bahn", "S-Bahn"),
    "Stadtbahn": ("Stadtbahn", "light rail", "Stadtbahn", "Stadtbahn"),
    "Seil-/Zahnradbahn": ("Bergbahn", "funicular", "фунікулер", "фуникулёр"),
    "Schiff": ("Schiff", "boat", "корабель", "корабль"),
    "Zug": ("Zug", "train", "поїзд", "поезд"),
    "Zug (Nahverkehr)": ("Zug", "train", "поїзд", "поезд"),
    "Zug (Fernverkehr)": ("Zug", "train", "поїзд", "поезд"),
    "SEV Schienerversatzv": ("SEV", "rail replacement", "SEV", "SEV"),
}


def tr(lang, key, *args):
    idx = LANG_INDEX.get(lang, 0)
    s = _T[key][idx]
    return s % args if args else s


def product_name(product, lang):
    row = PRODUCTS.get(product)
    if not row:
        return product or ""
    return row[LANG_INDEX.get(lang, 0)]


# --------------------------------------------------------------------------
# kleine Helfer
# --------------------------------------------------------------------------

def as_list(x):
    """EFA klappt einelementige Listen gern zu einem Objekt zusammen."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def h(s):
    return _html.escape(str(s if s is not None else ""), quote=True)


def q(s):
    return urllib.parse.quote(str(s if s is not None else ""), safe="")


def href(path="/", **params):
    items = []
    for k, v in params.items():
        if v is None:
            continue
        v = str(v).strip()
        if v == "":
            continue
        items.append((k, v))
    if not items:
        return path
    return path + "?" + urllib.parse.urlencode(items)


def dur_fmt(dur, lang):
    """'00:06' -> '6 Min' / '1 Std 15 Min' / …"""
    m = re.match(r"^(\d+):(\d+)$", str(dur or ""))
    if not m:
        return ""
    total = int(m.group(1)) * 60 + int(m.group(2))
    hh, mm = divmod(total, 60)
    idx = LANG_INDEX.get(lang, 0)
    if hh and mm:
        return (
            "%d Std %d Min",
            "%d h %d min",
            "%d год %d хв",
            "%d ч %d мин",
        )[idx] % (hh, mm)
    if hh:
        return ("%d Std", "%d h", "%d год", "%d ч")[idx] % hh
    return ("%d Min", "%d min", "%d хв", "%d мин")[idx] % mm


def dist_fmt(meters, lang):
    try:
        m = int(meters)
    except (TypeError, ValueError):
        return ""
    idx = LANG_INDEX.get(lang, 0)
    if m >= 1000:
        val = m / 1000.0
        if idx == 1:
            return "%.1f km" % val
        if idx == 0:
            return ("%.1f km" % val).replace(".", ",")
        return ("%.1f км" % val).replace(".", ",")
    return ("%d m", "%d m", "%d м", "%d м")[idx] % m


def date_fmt(d, lang):
    """'19.08.2026' -> 'Mi 19.08.2026'"""
    try:
        wd = WEEKDAYS.get(lang, WEEKDAYS["de"])[
            datetime.strptime(d, "%d.%m.%Y").weekday()
        ]
        return "%s %s" % (wd, d)
    except (ValueError, TypeError):
        return str(d or "")


def when_fmt(when, lang):
    if not isinstance(when, datetime):
        return ""
    wd = WEEKDAYS.get(lang, WEEKDAYS["de"])[when.weekday()]
    return "%s %s, %s" % (wd, when.strftime("%d.%m.%Y"), when.strftime("%H:%M"))


def parse_when(d, tm, now=None):
    """HTML-date/time oder Freitext -> datetime. Leer/ungültig = now."""
    now = now or datetime.now()
    date_ok = None
    time_ok = None
    d = (d or "").strip()
    tm = (tm or "").strip()
    if d:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                date_ok = datetime.strptime(d, fmt)
                break
            except ValueError:
                pass
    if tm:
        for fmt in ("%H:%M", "%H:%M:%S", "%H.%M"):
            try:
                time_ok = datetime.strptime(tm, fmt)
                break
            except ValueError:
                pass
    if date_ok is None and time_ok is None and not d and not tm:
        return now
    if date_ok is None:
        date_ok = now
    if time_ok is None:
        time_ok = now
    return date_ok.replace(
        hour=time_ok.hour, minute=time_ok.minute, second=0, microsecond=0
    )


def normalize_lang(raw, cookie_header=""):
    raw = (raw or "").strip().lower()
    if raw in LANGS:
        return raw
    if cookie_header:
        for part in cookie_header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "lang" and v in LANGS:
                return v
    return DEFAULT_LANG


# --------------------------------------------------------------------------
# EFA (oder lokaler Demo-Snapshot)
# --------------------------------------------------------------------------

class EfaDown(Exception):
    """Netz zu efa.vgn.de liegt."""


class EfaBad(Exception):
    """EFA hat geantwortet, aber mit Fehler."""


_efa_skip_until = 0.0


def efa_get(endpoint, params, timeout=None):
    global _efa_skip_until
    if time.time() < _efa_skip_until:
        raise EfaDown()
    url = "%s/%s?%s" % (EFA_URL, endpoint, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise EfaBad("HTTP %d" % e.code) from None
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError):
        _efa_skip_until = time.time() + 45
        raise EfaDown() from None


_offline_cache = {}


def _offline(name):
    if name not in _offline_cache:
        with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
            _offline_cache[name] = json.load(f)
    return _offline_cache[name]


_umlaut = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _norm(s):
    s = (s or "").lower().translate(_umlaut)
    return " ".join(re.findall(r"[a-z0-9]+", s, re.UNICODE))


def _point_from_local(s):
    return {
        "usage": "sf", "type": "any", "anyType": "stop",
        "name": s["name"],
        "stateless": str(s["id"]),
        "quality": str(s.get("quality", 0)),
        "best": "1" if s.get("best") else "0",
        "ref": {"id": str(s["id"])},
    }


def offline_find_stop(query):
    """grobe stopfinder-Imitation über die lokale Haltestellenliste"""
    qtoks = _norm(query).split()
    if not qtoks:
        return None
    points = []
    for s in _offline("stops_demo.json"):
        ntoks = _norm(s["name"]).split()
        if all(any(nt.startswith(qt) for nt in ntoks) for qt in qtoks):
            points.append(_point_from_local(s))
    points.sort(key=lambda p: -int(p.get("quality", 0)))
    return points or None


def offline_stop_name(stop_id):
    for s in _offline("stops_demo.json"):
        if str(s["id"]) == str(stop_id):
            return s["name"]
    return None


def local_stop_names():
    return [s["name"] for s in _offline("stops_demo.json")]


# --------------------------------------------------------------------------
# Haltestellensuche + Vorschläge
# --------------------------------------------------------------------------

class NotFound(Exception):
    def __init__(self, query):
        super().__init__(query)
        self.query = query


def _efa_stops(query, limit=10):
    data = efa_get("XML_STOPFINDER_REQUEST", {
        "outputFormat": "JSON", "language": "de", "stateless": 1,
        "locationServerActive": 1, "type_sf": "any", "name_sf": query,
        "anyObjFilter_sf": 2, "anyMaxSizeHitList": limit,
        "anyMaxSize": limit,
    }, timeout=min(TIMEOUT, 6))
    sf = data.get("stopFinder") or {}
    return [p for p in as_list(sf.get("points")) if p.get("anyType") == "stop"]


def search_stops(query, demo=False, limit=8):
    """Liste von EFA-ähnlichen stop-Punkten, evtl. leer."""
    query = (query or "").strip()
    if not query:
        return []
    local = (offline_find_stop(query) or [])[:limit]
    if demo:
        return local
    try:
        remote = _efa_stops(query, limit=limit)
    except (EfaDown, EfaBad):
        return local
    if not remote:
        return local
    seen = set()
    out = []
    for p in remote + local:
        name = (p.get("name") or "").strip()
        key = _norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def suggest_stops(query, demo=False, limit=8):
    """[{name, id}, ...] für Autovervollständigung."""
    out = []
    for s in search_stops(query, demo=demo, limit=limit):
        sid = ""
        ref = s.get("ref") or {}
        if isinstance(ref, dict):
            sid = str(ref.get("id") or "")
        out.append({"name": s.get("name") or "", "id": sid})
    return out


def find_stop(query, demo=False):
    """
    Liefert (id, Name) der gewählten Haltestelle oder (None, [Kandidaten]).
    Eine reine Ziffernfolge gilt als fertige VGN-Haltestellen-id.
    """
    query = (query or "").strip()
    if not query:
        raise NotFound("")
    if query.isdigit():
        return query, offline_stop_name(query) or query

    stops = search_stops(query, demo=demo, limit=8)
    if not stops:
        raise NotFound(query)

    best = [s for s in stops
            if s.get("best") == "1" and int(s.get("quality", 0)) >= 900]
    if len(best) == 1:
        s = best[0]
        return str(s["ref"]["id"]), s.get("name", "")
    if len(stops) == 1:
        s = stops[0]
        return str(s["ref"]["id"]), s.get("name", "")
    return None, stops[:8]


# --------------------------------------------------------------------------
# Verbindungssuche
# --------------------------------------------------------------------------

def parse_trips(data, lang="de"):
    """EFA-JSON -> kompakte Fahrt-Dicts"""
    out = []
    for tr in as_list((data or {}).get("trips")):
        legs = []
        for leg in as_list(tr.get("legs")):
            pts = as_list(leg.get("points"))
            dep, arr = pts[0], pts[-1]
            if dep.get("usage") == "arrival" and arr.get("usage") == "departure":
                dep, arr = arr, dep

            def t(p):
                d = p.get("dateTime") or {}
                return d.get("time"), (d.get("rtTime") or d.get("time")), d.get("date")

            mode = leg.get("mode") or {}
            product = mode.get("product") or ""
            is_walk = product in WALK_PRODUCTS
            legs.append({
                "dep": t(dep), "dep_name": dep.get("name", ""),
                "dep_plat": dep.get("platformName") or "",
                "arr": t(arr), "arr_name": arr.get("name", ""),
                "walk": is_walk,
                "line": mode.get("symbol") or mode.get("number") or "",
                "kind": product_name(product, lang) if not is_walk else "",
                "dest": mode.get("destination") or "",
                "min": leg.get("timeMinute"),
                "dist": leg.get("distance") if is_walk else None,
            })
        fares = as_list((tr.get("itdFare") or {}).get("fares"))
        fare = as_list(fares[0].get("fare")) if fares else []
        f = fare[0] if fare else {}
        first_date = ""
        if legs:
            first_date = legs[0]["dep"][2] or ""
        out.append({
            "duration": dur_fmt(tr.get("duration"), lang),
            "changes": tr.get("interchange", "0"),
            "walk_only": bool(legs) and all(l["walk"] for l in legs),
            "walk_m": legs[0]["dist"] if legs else None,
            "fare": (f.get("fareAdult"), f.get("unitsAdult")),
            "legs": legs,
            "date": first_date,
        })
    return out


def endpoint_names(data):
    """EFA liefert die bestätigten Namen origin/destination"""
    names = []
    for key in ("origin", "destination"):
        pts = ((data or {}).get(key) or {}).get("points") or {}
        p = pts.get("point") if isinstance(pts, dict) else None
        names.append(p.get("name", "") if isinstance(p, dict) else "")
    return names


def get_trips(o_id, d_id, demo=False, when=None, lang="de"):
    """-> (trips, Name_von, Name_nach)"""
    if demo:
        data = _offline("trip_zob_konzerthalle.json")
    else:
        when = when or datetime.now()
        data = efa_get("XML_TRIP_REQUEST2", {
            "outputFormat": "JSON", "language": "de", "stateless": 1,
            "calcNumberOfTrips": 3, "routeType": "LEASTTIME",
            "itdTripDateTimeDepArr": "dep",
            "itdDateDayOfMonth": when.strftime("%d"),
            "itdDateMonth": when.strftime("%m"),
            "itdDateYear": when.strftime("%Y"),
            "itdTimeHour": when.strftime("%H"),
            "itdTimeMinute": when.strftime("%M"),
            "type_origin": "stop", "name_origin": o_id,
            "type_destination": "stop", "name_destination": d_id,
        })
    o_name, d_name = endpoint_names(data)
    return parse_trips(data, lang), o_name, d_name


# --------------------------------------------------------------------------
# HTML (Seiten à 1–3 KB)
# --------------------------------------------------------------------------

CSS = (
    "body{font:16px/1.45 system-ui,sans-serif;max-width:36em;margin:1em auto;"
    "padding:0 .6em}input{font:inherit;padding:.2em;width:100%;box-sizing:border-box}"
    "button{font:inherit;padding:.3em 1em}pre{white-space:pre-wrap;background:#f4f4f4;"
    "padding:.4em .6em;margin:.2em 0}.t{margin:1.2em 0}.e{color:#b00}small{color:#666}"
    ".r{display:flex;gap:.6em}.r>p{flex:1;margin:.4em 0}.lang a{white-space:nowrap}"
)

HINT_JS = (
    "(function(){function b(i,l){if(!i||!l)return;var t;"
    "i.addEventListener('input',function(){var q=i.value.trim();"
    "if(q.length<2)return;clearTimeout(t);t=setTimeout(function(){"
    "var x=new XMLHttpRequest();x.open('GET','/s?q='+encodeURIComponent(q));"
    "x.onload=function(){try{var a=JSON.parse(x.responseText),h='',j;"
    "for(j=0;j<a.length;j++)h+='<option value=\"'+String(a[j].name)"
    ".replace(/\"/g,'&quot;')+'\">';if(h)l.innerHTML=h}catch(e){}};"
    "x.send()},180)})}var l=document.getElementById('sl');"
    "b(document.getElementsByName('f')[0],l);"
    "b(document.getElementsByName('t')[0],l)})();"
)


def head(title, lang):
    return (
        "<!doctype html><html lang=%s><meta charset=utf-8>"
        "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
        "<link rel=icon href=data:,><title>%s</title><style>%s</style>"
        % (h(lang), h(title), CSS)
    )


def foot(lang):
    return "<p><small>busfind · %s</small></p>" % tr(lang, "footer")


def lang_bar(lang, path="/", **keep):
    parts = []
    keep = dict(keep)
    for code in LANGS:
        label = LANG_NAMES[code]
        if code == lang:
            parts.append("<b>%s</b>" % h(label))
        else:
            parts.append('<a href="%s">%s</a>' % (
                h(href(path, lang=code, **keep)), h(label)))
    return '<p class=lang><small>%s</small></p>' % " · ".join(parts)


def datalist_html():
    opts = []
    for name in local_stop_names():
        opts.append('<option value="%s">' % h(name))
    return '<datalist id=sl>%s</datalist>' % "".join(opts)


def keep_params(f="", t="", d="", tm=""):
    return {"f": f, "t": t, "d": d, "tm": tm}


def page_index(f="", t="", d="", tm="", lang="de", msg=""):
    now = datetime.now()
    d = d or now.strftime("%Y-%m-%d")
    tm = tm or now.strftime("%H:%M")
    parts = [
        head("busfind", lang),
        "<h1>busfind</h1>",
        "<p><small>%s</small></p>" % tr(lang, "tagline"),
        lang_bar(lang, "/", **keep_params(f, t, d, tm)),
    ]
    if msg:
        parts.append('<p class=e>%s</p>' % msg)
    parts.append(
        '<form action=/r>'
        '<input type=hidden name=lang value="%s">'
        '<p>%s<br><input name=f value="%s" list=sl autofocus '
        "autocomplete=off spellcheck=false></p>"
        '<p>%s<br><input name=t value="%s" list=sl '
        "autocomplete=off spellcheck=false></p>"
        '<div class=r><p>%s<br><input type=date name=d value="%s"></p>'
        '<p>%s<br><input type=time name=tm value="%s"></p></div>'
        "%s"
        "<p><button>%s</button></p></form>" % (
            h(lang),
            tr(lang, "from"), h(f),
            tr(lang, "to"), h(t),
            tr(lang, "date"), h(d),
            tr(lang, "time"), h(tm),
            datalist_html(),
            tr(lang, "search"),
        )
    )
    parts.append("<p><small>%s</small></p>" % tr(lang, "examples"))
    parts.append("<script>%s</script>" % HINT_JS)
    parts.append(foot(lang))
    return "".join(parts)


def page_error(title, text, f="", t="", d="", tm="", lang="de"):
    return "".join([
        head(tr(lang, "title_error"), lang), "<h1>busfind</h1>",
        lang_bar(lang, "/", **keep_params(f, t, d, tm)),
        '<p class=e>%s</p>' % title,
        "<p>%s</p>" % text,
        '<p><a href="%s">%s</a></p>' % (
            h(href("/", lang=lang, **keep_params(f, t, d, tm))),
            tr(lang, "back")),
        foot(lang),
    ])


def page_choices(which, query, stops, f="", t="", d="", tm="", lang="de"):
    """Haltestelle per Linkliste klären"""
    parts = [
        head(tr(lang, "title_choose"), lang), "<h1>busfind</h1>",
        lang_bar(lang, "/", **keep_params(f, t, d, tm)),
        "<p>%s</p><ul>" % tr(lang, "choose", h(query), which),
    ]
    for s in stops:
        sid = s["ref"]["id"]
        nf, nt = (sid, t) if which == tr(lang, "which_from") else (f, sid)
        parts.append('<li><a href="%s">%s</a></li>' % (
            h(href("/r", f=nf, t=nt, d=d, tm=tm, lang=lang)),
            h(s.get("name", ""))))
    parts.append('</ul><p><a href="%s">%s</a></p>' % (
        h(href("/", lang=lang, **keep_params(f, t, d, tm))),
        tr(lang, "back")))
    parts.append(foot(lang))
    return "".join(parts)


def render_trip(trip, lang="de"):
    """eine Verbindungsvariante"""
    head_bits = []
    if trip["legs"]:
        head_bits.append("<b>%s→%s</b>" % (
            h(trip["legs"][0]["dep"][1]), h(trip["legs"][-1]["arr"][1])))
    if trip["walk_only"]:
        bits = [tr(lang, "walk")]
        if trip.get("walk_m") is not None:
            bits.append(dist_fmt(trip["walk_m"], lang))
        if trip["duration"]:
            bits.append(trip["duration"])
        head_bits.append(" · ".join(bits))
    else:
        if trip["duration"]:
            head_bits.append(h(trip["duration"]))
        head_bits.append(
            tr(lang, "no_changes") if str(trip["changes"]) == "0"
            else tr(lang, "changes", h(trip["changes"])))
    fare, unit = trip["fare"]
    if fare:
        extra = " " + tr(lang, "zone", h(unit)) if unit else ""
        head_bits.append("%s €%s" % (h(fare), extra))

    lines = []
    for leg in trip["legs"]:
        dt, drt, _ = leg["dep"]
        plat = " · %s" % h(leg["dep_plat"]) if leg["dep_plat"] else ""
        extra = " %s" % tr(lang, "plan", h(dt)) if dt and drt and dt != drt else ""
        lines.append("%s  %s%s%s" % (h(drt), h(leg["dep_name"]), plat, extra))
        if leg["walk"]:
            w = tr(lang, "walk")
            if leg.get("min"):
                w = tr(lang, "walk_for", h(leg["min"]))
            if leg.get("dist") is not None:
                w += " (%s)" % dist_fmt(leg["dist"], lang)
            lines.append(w)
        else:
            label = " ".join(x for x in (leg["line"], leg["kind"]) if x) or "→"
            if leg["dest"]:
                label += " → %s" % leg["dest"]
            lines.append(h(label))
    last = trip["legs"][-1]
    at, art, _ = last["arr"]
    extra = " %s" % tr(lang, "plan", h(at)) if at and art and at != art else ""
    lines.append("%s  %s%s" % (h(art), h(last["arr_name"]), extra))

    return '<div class=t><p>%s</p><pre>%s</pre></div>' % (
        " · ".join(head_bits), h("\n".join(lines)))


def page_route(f_id, f_name, t_id, t_name, trips, o_name, d_name,
               demo=False, lang="de", when=None, d="", tm=""):
    f_show = f_name or o_name or f_id
    t_show = t_name or d_name or t_id
    title = "%s → %s" % (f_show, t_show)
    keep = keep_params(f_show, t_show, d, tm)
    parts = [
        head("busfind — " + title, lang),
        "<h1>busfind</h1>",
        lang_bar(lang, "/r", f=f_id, t=t_id, d=d, tm=tm),
        "<h2>%s → %s</h2>" % (h(f_show), h(t_show)),
    ]
    when_s = when_fmt(when, lang) if when else ""
    if not when_s and trips:
        when_s = date_fmt(trips[0]["date"], lang)
    meta = []
    if when_s:
        meta.append("%s: %s" % (tr(lang, "departure"), h(when_s)))
    meta.append('<a href="%s">%s</a>' % (
        h(href("/r", f=f_id, t=t_id, d=d, tm=tm, lang=lang)),
        tr(lang, "refresh")))
    meta.append('<a href="%s">%s</a>' % (
        h(href("/", lang=lang, **keep)), tr(lang, "new_search")))
    parts.append("<p><small>%s</small></p>" % " · ".join(meta))
    if demo:
        parts.append('<p class=e><b>DEMO</b>: %s</p>' % tr(lang, "demo"))
    if not trips:
        parts.append('<p class=e>%s</p>' % tr(lang, "no_route"))
    for trip in trips:
        parts.append(render_trip(trip, lang))
    parts.append(foot(lang))
    return "".join(parts)


# --------------------------------------------------------------------------
# /r
# --------------------------------------------------------------------------

def build_route(f, t, demo, lang="de", when=None, d="", tm=""):
    """
    -> fertiges HTML. demo=True arbeitet auf dem lokalen Snapshot.
    Wirft EfaDown/EfaBad, wenn die Live-Anfrage scheitert.
    """
    when = when or datetime.now()
    fr = find_stop(f, demo)
    if fr[0] is None:
        return page_choices(tr(lang, "which_from"), f, fr[1],
                            f, t, d, tm, lang)
    f_id, f_name = fr

    tr_ = find_stop(t, demo)
    if tr_[0] is None:
        return page_choices(tr(lang, "which_to"), t, tr_[1],
                            f_id, t, d, tm, lang)
    t_id, t_name = tr_

    if f_id == t_id:
        return page_error(tr(lang, "same_stop"), tr(lang, "same_stop_hint"),
                          f, t, d, tm, lang)

    trips, o_name, d_name = get_trips(f_id, t_id, demo, when, lang)
    return page_route(f_id, f_name, t_id, t_name, trips, o_name, d_name,
                      demo, lang, when, d, tm)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "busfind/0.2"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, body, code=200, content_type="text/html; charset=utf-8",
              extra_headers=None):
        if isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            raw = body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _set_lang_cookie(self, lang, qs_had_lang):
        if not qs_had_lang:
            return None
        return [("Set-Cookie",
                 "lang=%s; Path=/; Max-Age=31536000; SameSite=Lax" % lang)]

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        f = (qs.get("f", [""])[0] or "").strip()
        t = (qs.get("t", [""])[0] or "").strip()
        d = (qs.get("d", [""])[0] or "").strip()
        tm = (qs.get("tm", [""])[0] or "").strip()
        qs_lang = (qs.get("lang", [""])[0] or "").strip()
        cookie = self.headers.get("Cookie", "")
        lang = normalize_lang(qs_lang, cookie)
        lang_headers = self._set_lang_cookie(lang, bool(qs_lang))
        when = parse_when(d, tm)

        if u.path == "/s":
            qtext = (qs.get("q", [""])[0] or "").strip()
            payload = json.dumps(
                suggest_stops(qtext, demo=OFFLINE, limit=8),
                ensure_ascii=False).encode("utf-8")
            self._send(payload, content_type="application/json; charset=utf-8",
                       extra_headers=[("Cache-Control", "max-age=30")])
            return

        if u.path == "/":
            self._send(page_index(f, t, d, tm, lang), extra_headers=lang_headers)
            return

        if u.path != "/r":
            self._send(page_error(tr(lang, "not_found_page"),
                                  '<a href="%s">%s</a>' % (h(href("/", lang=lang)),
                                                           tr(lang, "home")),
                                  f, t, d, tm, lang), 404,
                       extra_headers=lang_headers)
            return

        if not f or not t:
            self._send(page_index(f, t, d, tm, lang, tr(lang, "fill_both")),
                       extra_headers=lang_headers)
            return

        try:
            self._send(build_route(f, t, OFFLINE, lang, when, d, tm),
                       extra_headers=lang_headers)
        except NotFound as e:
            self._send(page_error(
                tr(lang, "not_in_vgn", h(e.query)),
                tr(lang, "not_in_vgn_hint"), f, t, d, tm, lang),
                extra_headers=lang_headers)
        except EfaDown:
            try:
                self._send(build_route(f, t, True, lang, when, d, tm),
                           extra_headers=lang_headers)
            except NotFound as e:
                self._send(page_error(
                    tr(lang, "not_in_vgn", h(e.query)),
                    tr(lang, "not_in_vgn_hint"), f, t, d, tm, lang),
                    extra_headers=lang_headers)
        except EfaBad as e:
            self._send(page_error(
                tr(lang, "efa_error", h(e)),
                tr(lang, "try_later"), f, t, d, tm, lang),
                extra_headers=lang_headers)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("busfind: http://0.0.0.0:%d/  (Modus: %s, Sprache: %s)" % (
        PORT, "Offline-Demo" if OFFLINE else "live efa.vgn.de", DEFAULT_LANG),
        flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
