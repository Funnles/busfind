#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit- und HTTP-Tests für busfind (ohne Netz, Offline-Fixtures)."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import redirect_stderr
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest import mock

import server

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import import_gtfs  # noqa: E402


class I18nTests(unittest.TestCase):
    def test_default_is_german(self):
        self.assertEqual(server.DEFAULT_LANG, "de")
        self.assertEqual(server.tr("de", "from"), "Von")
        self.assertEqual(server.tr("de", "to"), "Nach")
        self.assertEqual(server.tr("de", "search"), "Suchen")
        self.assertEqual(server.tr("de", "departure"), "Abfahrt")

    def test_all_four_languages(self):
        self.assertEqual(server.tr("en", "from"), "From")
        self.assertEqual(server.tr("uk", "from"), "Звідки")
        self.assertEqual(server.tr("ru", "from"), "Откуда")
        self.assertEqual(server.tr("en", "search"), "Search")
        self.assertEqual(server.tr("uk", "search"), "Знайти")
        self.assertEqual(server.tr("ru", "search"), "Найти")

    def test_unknown_lang_falls_back_to_german(self):
        self.assertEqual(server.normalize_lang("xx"), "de")
        self.assertEqual(server.normalize_lang(""), "de")
        self.assertEqual(server.tr("xx", "from"), "Von")

    def test_lang_from_cookie(self):
        self.assertEqual(server.normalize_lang("", "lang=uk; other=1"), "uk")
        self.assertEqual(server.normalize_lang("en", "lang=uk"), "en")

    def test_duration_and_distance(self):
        self.assertEqual(server.dur_fmt("00:06", "de"), "6 Min")
        self.assertEqual(server.dur_fmt("01:15", "de"), "1 Std 15 Min")
        self.assertEqual(server.dur_fmt("01:15", "en"), "1 h 15 min")
        self.assertEqual(server.dur_fmt("01:15", "ru"), "1 ч 15 мин")
        self.assertEqual(server.dist_fmt(500, "de"), "500 m")
        self.assertIn("km", server.dist_fmt(1500, "de"))
        self.assertIn("км", server.dist_fmt(1500, "ru"))

    def test_when_fmt_german_weekday(self):
        # 19.08.2026 is a Wednesday
        dt = datetime(2026, 8, 19, 17, 30)
        self.assertEqual(server.when_fmt(dt, "de"), "Mi 19.08.2026, 17:30")
        self.assertEqual(server.when_fmt(dt, "en"), "Wed 19.08.2026, 17:30")
        self.assertIn("ср", server.when_fmt(dt, "ru"))


class ParseWhenTests(unittest.TestCase):
    def test_empty_is_now(self):
        now = datetime(2026, 8, 19, 12, 0, 5)
        got = server.parse_when("", "", now=now)
        self.assertEqual(got, now)

    def test_iso_date_and_time(self):
        got = server.parse_when("2026-08-20", "09:15",
                                now=datetime(2026, 8, 19, 12, 0))
        self.assertEqual(got, datetime(2026, 8, 20, 9, 15))

    def test_german_date(self):
        got = server.parse_when("20.08.2026", "9.15",
                                now=datetime(2026, 8, 19, 12, 0))
        self.assertEqual(got, datetime(2026, 8, 20, 9, 15))

    def test_date_only_keeps_current_time(self):
        now = datetime(2026, 8, 19, 17, 30)
        got = server.parse_when("2026-08-21", "", now=now)
        self.assertEqual(got, datetime(2026, 8, 21, 17, 30))


class StopSuggestTests(unittest.TestCase):
    def test_zob_lists_bamberg_and_forchheim(self):
        hits = server.suggest_stops("ZOB", demo=True, limit=10)
        names = [s["name"] for s in hits]
        self.assertTrue(any("Bamberg, ZOB" == n for n in names), names)
        self.assertTrue(any("Forchheim, ZOB" == n for n in names), names)
        self.assertGreaterEqual(len(names), 3)

    def test_prefix_forch(self):
        hits = server.suggest_stops("Forch", demo=True)
        names = [s["name"] for s in hits]
        self.assertTrue(any("Forchheim" in n for n in names), names)

    def test_empty_query(self):
        self.assertEqual(server.suggest_stops("", demo=True), [])

    def test_find_stop_unique_best(self):
        sid, name = server.find_stop("Bamberg, ZOB", demo=True)
        self.assertEqual(sid, "3020200")
        self.assertEqual(name, "Bamberg, ZOB")

    def test_find_stop_ambiguous_zob(self):
        sid, stops = server.find_stop("ZOB", demo=True)
        self.assertIsNone(sid)
        self.assertGreater(len(stops), 1)

    def test_unknown_stop(self):
        with self.assertRaises(server.NotFound):
            server.find_stop("Atlantis Hafen", demo=True)

    def test_gtfs_catalog_not_just_zob(self):
        hits = server.suggest_stops("Klinikum", demo=True)
        names = [s["name"] for s in hits]
        self.assertTrue(any("Klinikum" in n for n in names), names)
        hits = server.suggest_stops("Schönleins", demo=True)
        names = [s["name"] for s in hits]
        self.assertTrue(any("Schönleins" in n for n in names), names)

    def test_full_vgn_catalog_loaded(self):
        server.reset_catalog()
        cat = server.load_catalog()
        self.assertGreater(len(cat), 1000, "GTFS/ZHV-Katalog fehlt")
        names = [s["name"] for s in server.suggest_stops("Plärrer", demo=True)]
        self.assertTrue(any("Plärrer" in n for n in names), names)
        names = [s["name"] for s in server.suggest_stops("Gaustadt", demo=True)]
        self.assertTrue(any("Gaustadt" in n for n in names), names)

    def test_full_catalog_tsv(self):
        here_tsv = os.path.join(os.path.dirname(server.__file__),
                                "fixtures", "stops_vgn.tsv")
        prev = None
        if os.path.exists(here_tsv):
            prev = here_tsv + ".baktest"
            os.replace(here_tsv, prev)
        try:
            with open(here_tsv, "w", encoding="utf-8") as f:
                f.write("id\tname\n")
                f.write("de:09461:20999\tBamberg, Gartenstadt\n")
                f.write("de:09564:510\tNürnberg, Plärrer\n")
                f.write("de:09563:1\tFürth, Rathaus\n")
            server.reset_catalog()
            hits = server.suggest_stops("Plärrer", demo=True)
            names = [s["name"] for s in hits]
            self.assertTrue(any("Plärrer" in n for n in names), names)
            hits = server.suggest_stops("Gartenstadt", demo=True)
            names = [s["name"] for s in hits]
            self.assertTrue(any("Gartenstadt" in n for n in names), names)
            hits = server.suggest_stops("Rathaus", demo=True)
            names = [s["name"] for s in hits]
            self.assertTrue(any("Fürth" in n for n in names), names)
        finally:
            if os.path.exists(here_tsv):
                os.remove(here_tsv)
            if prev and os.path.exists(prev):
                os.replace(prev, here_tsv)
            server.reset_catalog()


class GtfsImportTests(unittest.TestCase):
    def _zip_with_stops(self, body):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("stops.txt", body)
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        return path

    def test_parse_dedupes_platforms(self):
        path = self._zip_with_stops(
            "stop_id,stop_name,location_type,parent_station\n"
            "de:09461:20200,Bamberg ZOB,1,\n"
            "de:09461:20200:1,Bamberg ZOB,0,de:09461:20200\n"
            "de:09461:20240,Bamberg Markusplatz,0,\n"
            "de:09564:x,Nürnberg Eingang,2,\n"
        )
        try:
            stops = import_gtfs.stops_from_zip(path)
            names = [s["name"] for s in stops]
            self.assertEqual(names.count("Bamberg ZOB"), 1)
            self.assertIn("Bamberg Markusplatz", names)
            self.assertFalse(any("Eingang" in n for n in names))
        finally:
            os.remove(path)


class PageTests(unittest.TestCase):
    def test_index_is_german_by_default(self):
        html = server.page_index()
        self.assertIn("lang=de", html)
        self.assertIn("Von", html)
        self.assertIn("Nach", html)
        self.assertIn("Datum", html)
        self.assertIn("Uhrzeit", html)
        self.assertIn("Suchen", html)
        self.assertIn("type=date", html)
        self.assertIn("type=time", html)
        self.assertIn("<b>Deutsch</b>", html)
        self.assertIn("English", html)
        self.assertIn("Українська", html)
        self.assertIn("Русский", html)

    def test_index_english(self):
        html = server.page_index(lang="en")
        self.assertIn("lang=en", html)
        self.assertIn("From", html)
        self.assertIn("Search", html)
        self.assertIn("<b>English</b>", html)

    def test_index_has_datalist_hints(self):
        html = server.page_index()
        self.assertIn("<datalist id=sl>", html)
        self.assertIn('value="Bamberg, ZOB"', html)
        self.assertIn('value="Forchheim, ZOB"', html)
        self.assertIn("XMLHttpRequest", html)

    def test_route_shows_departure_datetime(self):
        trips, o, d = server.get_trips("3020200", "3020178", demo=True, lang="de")
        when = datetime(2026, 8, 19, 17, 30)
        html = server.page_route(
            "3020200", "Bamberg, ZOB", "3020178", "Bamberg, Konzerthalle",
            trips, o, d, demo=True, lang="de", when=when,
            d="2026-08-19", tm="17:30")
        self.assertIn("Abfahrt: Mi 19.08.2026, 17:30", html)
        self.assertIn("ohne Umstieg", html)
        self.assertIn("lang=de", html)

    def test_route_ukrainian(self):
        trips, o, d = server.get_trips("3020200", "3020178", demo=True, lang="uk")
        html = server.page_route(
            "3020200", "Bamberg, ZOB", "3020178", "Bamberg, Konzerthalle",
            trips, o, d, demo=True, lang="uk",
            when=datetime(2026, 8, 19, 17, 30))
        self.assertIn("Відправлення", html)
        self.assertIn("без пересадок", html)


class FooterAndDayTests(unittest.TestCase):
    def test_footer_no_frogfind(self):
        for lang, expected in [
            ("de", "busfind · Daten: efa.vgn.de"),
            ("en", "busfind · data: efa.vgn.de"),
            ("uk", "busfind · дані: efa.vgn.de"),
            ("ru", "busfind · данные: efa.vgn.de"),
        ]:
            foot = server.foot(lang)
            self.assertIn(expected, foot)
            self.assertNotIn("frogfind", foot)

    def test_day_prefix_morgen(self):
        req = datetime(2026, 8, 18)
        self.assertEqual(server.format_day_prefix("19.08.2026", req, "de"), "morgen ")
        self.assertEqual(server.format_day_prefix("19.08.2026", req, "en"), "tomorrow ")
        self.assertEqual(server.format_day_prefix("19.08.2026", req, "uk"), "завтра ")
        self.assertEqual(server.format_day_prefix("19.08.2026", req, "ru"), "завтра ")

    def test_day_prefix_same_day(self):
        req = datetime(2026, 8, 19)
        self.assertEqual(server.format_day_prefix("19.08.2026", req, "de"), "")

    def test_day_prefix_future_date(self):
        req = datetime(2026, 8, 17)  # Monday
        # 19.08.2026 is Wednesday (Mi in German)
        self.assertEqual(server.format_day_prefix("19.08.2026", req, "de"), "Mi 19.08. ")


class TripOrderTests(unittest.TestCase):
    def _make_trip(self, time_str, date_str="19.08.2026"):
        return {
            "date": date_str,
            "legs": [{
                "dep": ("17:00", time_str, date_str),
                "arr": ("17:10", "17:10", date_str),
            }]
        }

    def test_order_trips_one_before_when(self):
        trips = [
            self._make_trip("17:00"),
            self._make_trip("17:15"),
            self._make_trip("17:45"),
            self._make_trip("18:00"),
        ]
        when = datetime(2026, 8, 19, 17, 30)
        ordered = server.order_trips(trips, when)
        dep_times = [t["legs"][0]["dep"][1] for t in ordered]
        self.assertEqual(dep_times, ["17:15", "17:45", "18:00"])

    def test_order_trips_all_after(self):
        trips = [self._make_trip("17:35"), self._make_trip("18:00")]
        when = datetime(2026, 8, 19, 17, 30)
        ordered = server.order_trips(trips, when)
        self.assertEqual(len(ordered), 2)

    def test_order_trips_all_before(self):
        trips = [self._make_trip("17:00"), self._make_trip("17:15")]
        when = datetime(2026, 8, 19, 17, 30)
        ordered = server.order_trips(trips, when)
        dep_times = [t["legs"][0]["dep"][1] for t in ordered]
        self.assertEqual(dep_times, ["17:15"])

    def test_order_trips_none_when(self):
        trips = [self._make_trip("17:00"), self._make_trip("17:15")]
        ordered = server.order_trips(trips, None)
        self.assertEqual(len(ordered), 2)


class CssAndLayoutTests(unittest.TestCase):
    def test_responsive_css_media_query(self):
        self.assertIn("@media(max-width:600px)", server.CSS)
        self.assertIn("flex-direction:column", server.CSS)


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class CurrentVgnTimeTests(unittest.TestCase):
    def test_current_time_uses_berlin_wall_clock(self):
        expected = datetime.now(server.VGN_TIMEZONE).replace(tzinfo=None)
        actual = server.current_vgn_time()
        self.assertIsNone(actual.tzinfo)
        self.assertLess(abs((actual - expected).total_seconds()), 2)

    def test_trip_request_sends_current_vgn_date_and_time(self):
        current = datetime(2026, 8, 20, 7, 4)
        captured = {}

        def fake_efa_get(endpoint, params, timeout=None):
            captured["endpoint"] = endpoint
            captured["params"] = params
            return {"trips": []}

        with mock.patch.object(server, "current_vgn_time", return_value=current), \
             mock.patch.object(server, "efa_get", side_effect=fake_efa_get):
            trips, _, _ = server.get_trips("3020200", "3020178")

        self.assertEqual(trips, [])
        self.assertEqual(captured["endpoint"], "XML_TRIP_REQUEST2")
        params = captured["params"]
        self.assertEqual(params["itdTripDateTimeDepArr"], "dep")
        self.assertEqual(params["itdDateDay"], "20")
        self.assertEqual(params["itdDateMonth"], "08")
        self.assertEqual(params["itdDateYear"], "2026")
        self.assertEqual(params["itdTimeHour"], "07")
        self.assertEqual(params["itdTimeMinute"], "04")
        self.assertNotIn("itdDateDayOfMonth", params)

    def test_index_defaults_to_current_vgn_time(self):
        current = datetime(2026, 8, 20, 7, 4)
        with mock.patch.object(server, "current_vgn_time", return_value=current):
            html = server.page_index()
        self.assertIn('type=date name=d value="2026-08-20"', html)
        self.assertIn('type=time name=tm value="07:04"', html)


class EfaLoggingTests(unittest.TestCase):
    def setUp(self):
        server._efa_skip_until = 0.0
        server._efa_skip_reason = ""

    def tearDown(self):
        server._efa_skip_until = 0.0
        server._efa_skip_reason = ""

    def test_network_failure_is_logged_with_reason(self):
        error = urllib.error.URLError(ConnectionRefusedError("connection refused"))
        stderr = io.StringIO()
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=error), \
             redirect_stderr(stderr):
            with self.assertRaises(server.EfaDown) as caught:
                server.efa_get("XML_STOPFINDER_REQUEST", {"name_sf": "ZOB"})

        self.assertIn("connection refused", str(caught.exception))
        output = stderr.getvalue()
        self.assertIn("ERROR", output)
        self.assertIn("EFA unavailable", output)
        self.assertIn("XML_STOPFINDER_REQUEST", output)
        self.assertIn("connection refused", output)
        self.assertNotIn("name_sf", output)

    def test_http_503_is_treated_as_unavailable_and_logged(self):
        error = urllib.error.HTTPError(
            "https://efa.invalid/", 503, "Service Unavailable", {}, None)
        stderr = io.StringIO()
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=error), \
             redirect_stderr(stderr):
            with self.assertRaises(server.EfaDown) as caught:
                server.efa_get("XML_TRIP_REQUEST2", {})

        self.assertEqual(str(caught.exception), "HTTP 503")
        self.assertIn("HTTP 503", stderr.getvalue())

    def test_invalid_json_is_bad_response_not_network_outage(self):
        stderr = io.StringIO()
        with mock.patch.object(server.urllib.request, "urlopen",
                               return_value=_Response(b"not json")), \
             redirect_stderr(stderr):
            with self.assertRaises(server.EfaBad) as caught:
                server.efa_get("XML_TRIP_REQUEST2", {})

        self.assertIn("JSON", str(caught.exception))
        self.assertIn("invalid JSON", stderr.getvalue())
        self.assertEqual(server._efa_skip_until, 0.0)


class ServerStartupTests(unittest.TestCase):
    def test_bind_failure_has_clear_log_and_nonzero_result(self):
        stderr = io.StringIO()
        with mock.patch.object(server, "ThreadingHTTPServer",
                               side_effect=OSError(98, "Address already in use")), \
             redirect_stderr(stderr):
            result = server.main()

        self.assertEqual(result, 1)
        output = stderr.getvalue()
        self.assertIn("ERROR", output)
        self.assertIn("HTTP server could not bind", output)
        self.assertIn("Address already in use", output)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.OFFLINE = True
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path, headers=None):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)

    def test_home_german(self):
        code, body, _ = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("Von", body)
        self.assertIn("Datum", body)
        self.assertIn("Uhrzeit", body)

    def test_home_lang_cookie(self):
        code, body, hdrs = self.get("/?lang=ru")
        self.assertEqual(code, 200)
        self.assertIn("Откуда", body)
        self.assertIn("lang=ru", hdrs.get("Set-Cookie", ""))

    def test_suggest_zob(self):
        code, body, hdrs = self.get("/s?q=ZOB")
        self.assertEqual(code, 200)
        self.assertIn("json", hdrs.get("Content-Type", ""))
        data = json.loads(body)
        names = [x["name"] for x in data]
        self.assertTrue(any("Bamberg, ZOB" in n for n in names), names)
        self.assertTrue(any("Forchheim, ZOB" in n for n in names), names)

    def test_route_with_datetime(self):
        qs = urllib.parse.urlencode({
            "f": "Bamberg, ZOB", "t": "Konzerthalle",
            "d": "2026-08-19", "tm": "17:30", "lang": "de",
        })
        code, body, _ = self.get("/r?" + qs)
        self.assertEqual(code, 200)
        self.assertIn("Abfahrt", body)
        self.assertIn("19.08.2026", body)
        self.assertIn("17:30", body)
        self.assertIn("Konzerthalle", body)

    def test_missing_fields(self):
        code, body, _ = self.get("/r?f=ZOB&lang=en")
        self.assertEqual(code, 200)
        self.assertIn("Please fill in both fields.", body)

    def test_route_yesterday_shows_morgen(self):
        qs = urllib.parse.urlencode({
            "f": "Bamberg, ZOB", "t": "Konzerthalle",
            "d": "2026-08-18", "tm": "17:30", "lang": "de",
        })
        code, body, _ = self.get("/r?" + qs)
        self.assertEqual(code, 200)
        self.assertIn("morgen", body)
        self.assertIn("17:15→17:21", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
