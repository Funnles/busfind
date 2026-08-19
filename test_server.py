#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit- und HTTP-Tests für busfind (ohne Netz, Offline-Fixtures)."""

import json
import os
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

import server


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
