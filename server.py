#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
busfind — frogfind для автобусов Бамберга (VGN).

Идея как у frogfind.com: сервер делает всю тяжёлую работу, а телефону
отдаётся голый HTML на 1–3 КБ, который открывается даже на 64 кбит/с.
Ноль джаваскрипта, ноль картинок, ноль зависимостей (только stdlib).

Источник данных — официальный EFA-интерфейс VGN (efa.vgn.de):
  * XML_STOPFINDER_REQUEST — поиск остановки по имени
  * XML_TRIP_REQUEST2      — маршруты от-до

Запуск:
    python3 server.py                 # http://localhost:8000
    BUSFIND_PORT=9000 python3 server.py
    BUSFIND_OFFLINE=1 python3 server.py   # демо на локальном снимке данных

Вдохновлено: https://github.com/ActionRetro/FrogFind и
             https://github.com/becheran/vgn
"""

import html as _html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# конфиг
# --------------------------------------------------------------------------

EFA_URL = os.environ.get("EFA_URL", "https://efa.vgn.de/xml/").rstrip("/")
PORT = int(os.environ.get("BUSFIND_PORT", "8000"))
OFFLINE = os.environ.get("BUSFIND_OFFLINE", "") not in ("", "0")
TIMEOUT = float(os.environ.get("BUSFIND_TIMEOUT", "12"))
UA = "busfind/0.1 (dial-up friendly)"
HERE = os.path.dirname(os.path.abspath(__file__))

# вид транспорта из EFA -> короткий русский текст
PRODUCTS = {
    "Stadtbus": "автобус",
    "Regionalbus": "рег. автобус",
    "Schnellbus": "экспресс",
    "Bürgerbus": "Bürgerbus",
    "Rufbus (liniengeb.)": "Rufbus",
    "AST (flächengebunde": "AST",
    "Straßen-/Trambahn": "трамвай",
    "U-Bahn": "метро",
    "S-Bahn": "S-Bahn",
    "Stadtbahn": "Stadtbahn",
    "Seil-/Zahnradbahn": "фуникулёр",
    "Schiff": "корабль",
    "Zug": "поезд",
    "Zug (Nahverkehr)": "поезд",
    "Zug (Fernverkehr)": "поезд",
    "SEV Schienerversatzv": "SEV",
}
WALK_PRODUCTS = ("Fussweg", "Fußweg", "footpath")

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


# --------------------------------------------------------------------------
# маленькие помощники
# --------------------------------------------------------------------------

def as_list(x):
    """EFA любит схлопывать списки из одного элемента в объект."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def h(s):
    """экранирование для HTML"""
    return _html.escape(str(s if s is not None else ""), quote=True)


def q(s):
    """кодирование для URL"""
    return urllib.parse.quote(str(s if s is not None else ""), safe="")


def dur_ru(dur):
    """'00:06' -> '6 мин', '01:15' -> '1 ч 15 мин'"""
    m = re.match(r"^(\d+):(\d+)$", str(dur or ""))
    if not m:
        return ""
    total = int(m.group(1)) * 60 + int(m.group(2))
    hh, mm = divmod(total, 60)
    if hh and mm:
        return "%d ч %d мин" % (hh, mm)
    if hh:
        return "%d ч" % hh
    return "%d мин" % mm


def dist_ru(meters):
    try:
        m = int(meters)
    except (TypeError, ValueError):
        return ""
    if m >= 1000:
        return "%.1f км" % (m / 1000.0)
    return "%d м" % m


def date_ru(d):
    """'19.08.2026' -> 'ср 19.08.2026'"""
    try:
        wd = WEEKDAYS[datetime.strptime(d, "%d.%m.%Y").weekday()]
        return "%s %s" % (wd, d)
    except (ValueError, TypeError):
        return str(d or "")


# --------------------------------------------------------------------------
# доступ к EFA (или к локальному демо-снимку, если сети нет)
# --------------------------------------------------------------------------

class EfaDown(Exception):
    """сеть до efa.vgn.de не работает"""


class EfaBad(Exception):
    """EFA ответил, но с ошибкой"""


def efa_get(endpoint, params):
    url = "%s/%s?%s" % (EFA_URL, endpoint, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise EfaBad("HTTP %d" % e.code) from None
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError):
        raise EfaDown() from None


# ---- офлайн-демо: снимок реальных ответов EFA от 19.08.2026 --------------

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


def offline_find_stop(query):
    """грубая имитация stopfinder поверх фикстуры остановок"""
    qtoks = _norm(query).split()
    if not qtoks:
        return None
    points = []
    for s in _offline("stops_demo.json"):
        ntoks = _norm(s["name"]).split()
        if all(any(nt.startswith(qt) for nt in ntoks) for qt in qtoks):
            # форма как у настоящего EFA
            points.append({
                "usage": "sf", "type": "any", "anyType": "stop",
                "name": s["name"],
                "stateless": str(s["id"]),
                "quality": str(s.get("quality", 0)),
                "best": "1" if s.get("best") else "0",
                "ref": {"id": str(s["id"])},
            })
    points.sort(key=lambda p: -int(p.get("quality", 0)))
    return points or None


def offline_stop_name(stop_id):
    for s in _offline("stops_demo.json"):
        if str(s["id"]) == str(stop_id):
            return s["name"]
    return None


# --------------------------------------------------------------------------
# поиск остановки
# --------------------------------------------------------------------------

class NotFound(Exception):
    def __init__(self, query):
        super().__init__(query)
        self.query = query


def find_stop(query, demo=False):
    """
    Возвращает (id, имя) выбранной остановки либо (None, [кандидаты]).
    query из одних цифр считается готовым id остановки VGN.
    """
    query = (query or "").strip()
    if not query:
        raise NotFound("")
    if query.isdigit():
        return query, offline_stop_name(query) or query

    if demo:
        stops = offline_find_stop(query) or []
    else:
        data = efa_get("XML_STOPFINDER_REQUEST", {
            "outputFormat": "JSON", "language": "de", "stateless": 1,
            "locationServerActive": 1, "type_sf": "any", "name_sf": query,
            "anyObjFilter_sf": 2, "anyMaxSize": 8,
        })
        sf = data.get("stopFinder") or {}
        stops = [p for p in as_list(sf.get("points"))
                 if p.get("anyType") == "stop"]

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
    # неоднозначно — отдаём список на выбор
    return None, stops[:8]


# --------------------------------------------------------------------------
# поиск маршрута
# --------------------------------------------------------------------------

def parse_trips(data):
    """EFA JSON -> компактные словари поездок"""
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
                "kind": PRODUCTS.get(product, product),
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
            "duration": dur_ru(tr.get("duration")),
            "changes": tr.get("interchange", "0"),
            "walk_only": bool(legs) and all(l["walk"] for l in legs),
            "walk_m": legs[0]["dist"] if legs else None,
            "fare": (f.get("fareAdult"), f.get("unitsAdult")),
            "legs": legs,
            "date": first_date,
        })
    return out


def endpoint_names(data):
    """EFA возвращает подтверждённые имена остановок origin/destination"""
    names = []
    for key in ("origin", "destination"):
        pts = ((data or {}).get(key) or {}).get("points") or {}
        p = pts.get("point") if isinstance(pts, dict) else None
        names.append(p.get("name", "") if isinstance(p, dict) else "")
    return names


def get_trips(o_id, d_id, demo=False):
    """-> (trips, имя_откуда, имя_куда)"""
    if demo:
        data = _offline("trip_zob_konzerthalle.json")
    else:
        now = datetime.now()
        data = efa_get("XML_TRIP_REQUEST2", {
            "outputFormat": "JSON", "language": "de", "stateless": 1,
            "calcNumberOfTrips": 3, "routeType": "LEASTTIME",
            "itdDateDayOfMonth": now.strftime("%d"),
            "itdDateMonth": now.strftime("%m"),
            "itdDateYear": now.strftime("%Y"),
            "itdTimeHour": now.strftime("%H"),
            "itdTimeMinute": now.strftime("%M"),
            "type_origin": "stop", "name_origin": o_id,
            "type_destination": "stop", "name_destination": d_id,
        })
    o_name, d_name = endpoint_names(data)
    return parse_trips(data), o_name, d_name


# --------------------------------------------------------------------------
# HTML (страницы по 1–3 КБ)
# --------------------------------------------------------------------------

CSS = ("body{font:16px/1.45 system-ui,sans-serif;max-width:36em;margin:1em auto;"
       "padding:0 .6em}input{font:inherit;padding:.2em;width:100%}button{font:inherit;"
       "padding:.3em 1em}pre{white-space:pre-wrap;background:#f4f4f4;padding:.4em .6em;"
       "margin:.2em 0}.t{margin:1.2em 0}.e{color:#b00}small{color:#666}")

HEAD = ('<!doctype html><html lang=ru><meta charset=utf-8>'
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        '<link rel=icon href=data:,><title>%s</title><style>%s</style>')

FOOT = '<p><small>busfind · данные: efa.vgn.de · идея: frogfind.com</small></p>'


def page_index(f="", t="", msg=""):
    parts = [HEAD % ("busfind", CSS),
             "<h1>busfind</h1>",
             "<p><small>автобусы Бамберга и VGN · без JS · ~1 КБ на страницу</small></p>"]
    if msg:
        parts.append('<p class=e>%s</p>' % msg)
    parts.append('<form action=/r>'
                 '<p>Откуда<br><input name=f value="%s" autofocus></p>'
                 '<p>Куда<br><input name=t value="%s"></p>'
                 "<p><button>Найти</button></p></form>" % (h(f), h(t)))
    parts.append("<p><small>примеры: ZOB · Bahnhof · Konzerthalle · Markusplatz</small></p>")
    parts.append(FOOT)
    return "".join(parts)


def page_error(title, text, f="", t=""):
    return "".join([
        HEAD % ("busfind — ошибка", CSS), "<h1>busfind</h1>",
        '<p class=e>%s</p>' % title,
        "<p>%s</p>" % text,
        '<p><a href="/?f=%s&amp;t=%s">← назад к поиску</a></p>' % (q(f), q(t)),
        FOOT,
    ])


def page_choices(which, query, stops, f="", t=""):
    """уточнение остановки списком ссылок"""
    parts = [HEAD % ("busfind — уточните", CSS), "<h1>busfind</h1>",
             "<p>«%s» — выберите остановку (%s):</p><ul>" % (h(query), which)]
    for s in stops:
        sid = s["ref"]["id"]
        nf, nt = (sid, t) if which == "откуда" else (f, sid)
        parts.append('<li><a href="/r?f=%s&amp;t=%s">%s</a></li>'
                     % (q(nf), q(nt), h(s.get("name", ""))))
    parts.append('</ul><p><a href="/?f=%s&amp;t=%s">← назад к поиску</a></p>' % (q(f), q(t)))
    parts.append(FOOT)
    return "".join(parts)


def render_trip(tr):
    """один вариант маршрута"""
    head = []
    if tr["legs"]:
        head.append("<b>%s→%s</b>" % (h(tr["legs"][0]["dep"][1]),
                                      h(tr["legs"][-1]["arr"][1])))
    if tr["walk_only"]:
        bits = ["пешком"]
        if tr.get("walk_m") is not None:
            bits.append(dist_ru(tr["walk_m"]))
        if tr["duration"]:
            bits.append(tr["duration"])
        head.append(" · ".join(bits))
    else:
        if tr["duration"]:
            head.append(h(tr["duration"]))
        head.append("без пересадок" if str(tr["changes"]) == "0"
                    else "пересадок: %s" % h(tr["changes"]))
    fare, unit = tr["fare"]
    if fare:
        head.append("%s €%s" % (h(fare), " (ур. %s)" % h(unit) if unit else ""))

    lines = []
    for leg in tr["legs"]:
        dt, drt, _ = leg["dep"]
        plat = " · %s" % h(leg["dep_plat"]) if leg["dep_plat"] else ""
        extra = " (план %s)" % h(dt) if dt and drt and dt != drt else ""
        lines.append("%s  %s%s%s" % (h(drt), h(leg["dep_name"]), plat, extra))
        if leg["walk"]:
            w = "пешком"
            if leg.get("min"):
                w += " %s мин" % h(leg["min"])
            if leg.get("dist") is not None:
                w += " (%s)" % dist_ru(leg["dist"])
            lines.append(w)
        else:
            label = " ".join(x for x in (leg["line"], leg["kind"]) if x) or "→"
            if leg["dest"]:
                label += " → %s" % leg["dest"]
            lines.append(h(label))
    last = tr["legs"][-1]
    at, art, _ = last["arr"]
    extra = " (план %s)" % h(at) if at and art and at != art else ""
    lines.append("%s  %s%s" % (h(art), h(last["arr_name"]), extra))

    return '<div class=t><p>%s</p><pre>%s</pre></div>' % (
        " · ".join(head), h("\n".join(lines)))


def page_route(f_id, f_name, t_id, t_name, trips, o_name, d_name, demo=False):
    f_show = f_name or o_name or f_id
    t_show = t_name or d_name or t_id
    title = "%s → %s" % (f_show, t_show)
    parts = [HEAD % (h("busfind — " + title), CSS), "<h1>busfind</h1>",
             "<h2>%s → %s</h2>" % (h(f_show), h(t_show))]
    when = trips[0]["date"] if trips else ""
    parts.append('<p><small>%s · <a href=/r?f=%s&amp;t=%s>обновить</a> · '
                 '<a href="/?f=%s&amp;t=%s">новый поиск</a></small></p>'
                 % (h(date_ru(when)), q(f_id), q(t_id), q(f_show), q(t_show)))
    if demo:
        parts.append('<p class=e><b>ДЕМО</b>: efa.vgn.de недоступен, показан снимок '
                     "ZOB → Konzerthalle от 19.08.2026</p>")
    if not trips:
        parts.append('<p class=e>Маршрут не найден.</p>')
    for tr in trips:
        parts.append(render_trip(tr))
    parts.append(FOOT)
    return "".join(parts)


# --------------------------------------------------------------------------
# логика страницы /r
# --------------------------------------------------------------------------

def build_route(f, t, demo):
    """
    -> готовый HTML. В demo=True работает на локальном снимке.
    Кидает EfaDown/EfaBad, если живой запрос не удался.
    """
    fr = find_stop(f, demo)
    if fr[0] is None:
        return page_choices("откуда", f, fr[1], f, t)
    f_id, f_name = fr

    tr_ = find_stop(t, demo)
    if tr_[0] is None:
        return page_choices("куда", t, tr_[1], f_id, t)
    t_id, t_name = tr_

    if f_id == t_id:
        return page_error("Откуда и куда — одно и то же место.",
                          "Выберите разные остановки.", f, t)

    trips, o_name, d_name = get_trips(f_id, t_id, demo)
    return page_route(f_id, f_name, t_id, t_name, trips, o_name, d_name, demo)


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "busfind/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, body, code=200):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        f = (qs.get("f", [""])[0] or "").strip()
        t = (qs.get("t", [""])[0] or "").strip()

        if u.path == "/":
            self._send(page_index(f, t))
            return

        if u.path != "/r":
            self._send(page_error("Нет такой страницы.",
                                  '<a href="/">← на главную</a>'), 404)
            return

        if not f or not t:
            self._send(page_index(f, t, "Заполните оба поля."))
            return

        try:
            self._send(build_route(f, t, OFFLINE))
        except NotFound as e:
            self._send(page_error(
                "«%s» — не найдено в VGN." % h(e.query),
                "Это не остановка VGN. Пишите название остановки "
                "(например: ZOB, Bahnhof, Konzerthalle).", f, t))
        except EfaDown:
            # сети нет — если есть локальный снимок, показываем демо
            try:
                self._send(build_route(f, t, True))
            except NotFound as e:
                self._send(page_error("«%s» — не найдено в VGN." % h(e.query),
                                      "Это не остановка VGN.", f, t))
        except EfaBad as e:
            self._send(page_error("VGN ответил ошибкой (%s)." % h(e),
                                  "Попробуйте ещё раз чуть позже.", f, t))


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("busfind: http://0.0.0.0:%d/  (режим: %s)" % (PORT,
          "офлайн-демо" if OFFLINE else "реальный efa.vgn.de"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
