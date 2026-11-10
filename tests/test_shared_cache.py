"""Shared cache, place search and the duplicate-member filter (2026-11-10)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core import geocode  # noqa: E402
from core.cache import DEFAULT_TTL, CachingHttp, ttl_for  # noqa: E402
from core.forecast.members import Hour, drop_copies  # noqa: E402
from core.safety import HttpStatusError  # noqa: E402


class FakeHttp:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, str, tuple]] = []
        self.delay = delay
        self.fail = False

    async def _answer(self, kind, url, params):
        self.calls.append((kind, url, tuple(sorted((params or {}).items()))))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise HttpStatusError(503)
        return {"url": url, "n": len(self.calls)} if kind == "json" else (b"x" * 10 if kind == "bytes" else "text")

    async def get_json(self, url, params=None, limit=0):
        return await self._answer("json", url, params)

    async def get_bytes(self, url, params=None, limit=0):
        return await self._answer("bytes", url, params)

    async def get_text(self, url, params=None, limit=0):
        return await self._answer("text", url, params)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class CacheTests(unittest.TestCase):
    def test_same_request_is_sent_once_within_its_lifetime(self):
        inner, clock = FakeHttp(), Clock()
        http = CachingHttp(inner, clock)

        async def run():
            a = await http.get_json("https://api.example/x", {"a": "1"})
            b = await http.get_json("https://api.example/x", {"a": "1"})
            return a, b

        a, b = asyncio.run(run())
        self.assertEqual(len(inner.calls), 1)
        self.assertEqual(a, b)

    def test_different_parameters_are_different_requests(self):
        inner = FakeHttp()
        http = CachingHttp(inner, Clock())

        async def run():
            await http.get_json("https://api.example/x", {"lat": "47.5"})
            await http.get_json("https://api.example/x", {"lat": "47.6"})

        asyncio.run(run())
        self.assertEqual(len(inner.calls), 2)

    def test_expired_answer_is_asked_again(self):
        inner, clock = FakeHttp(), Clock()
        http = CachingHttp(inner, clock)

        async def run():
            await http.get_text("https://api.example/t")
            clock.t += DEFAULT_TTL + 1
            await http.get_text("https://api.example/t")

        asyncio.run(run())
        self.assertEqual(len(inner.calls), 2)

    def test_overlapping_requests_are_merged(self):
        inner = FakeHttp(delay=0.05)
        http = CachingHttp(inner, Clock())

        async def run():
            return await asyncio.gather(*(http.get_bytes("https://tilecache.rainviewer.com/v2/radar/abc/256/7/1/2/2/0_1.png")
                                          for _ in range(5)))

        results = asyncio.run(run())
        self.assertEqual(len(inner.calls), 1)
        self.assertTrue(all(r == results[0] for r in results))

    def test_errors_are_not_cached(self):
        inner = FakeHttp()
        http = CachingHttp(inner, Clock())

        async def run():
            inner.fail = True
            with self.assertRaises(HttpStatusError):
                await http.get_json("https://api.example/e")
            inner.fail = False
            return await http.get_json("https://api.example/e")

        self.assertIsNotNone(asyncio.run(run()))
        self.assertEqual(len(inner.calls), 2)

    def test_json_answers_are_copies(self):
        http = CachingHttp(FakeHttp(), Clock())

        async def run():
            a = await http.get_json("https://api.example/j")
            a["url"] = "changed"
            return await http.get_json("https://api.example/j")

        self.assertEqual(asyncio.run(run())["url"], "https://api.example/j")

    def test_memory_is_bounded(self):
        http = CachingHttp(FakeHttp(), Clock(), max_bytes=100)

        async def run():
            for i in range(50):
                await http.get_bytes(f"https://api.example/b{i}")

        asyncio.run(run())
        self.assertLessEqual(http.stats()["bytes"], 100)

    def test_lifetimes(self):
        self.assertGreater(ttl_for("https://tilecache.rainviewer.com/v2/radar/abc/256/7/1/2/2/0_1.png"), 3000)
        self.assertGreater(ttl_for("https://noaadata.apps.nsidc.org/NOAA/G02156/4km/2026/ims2026313_4km_v1.3.asc.gz"), 80000)
        self.assertEqual(ttl_for("https://api.rainviewer.com/public/weather-maps.json"), DEFAULT_TTL)
        self.assertEqual(ttl_for("https://noaadata.apps.nsidc.org/NOAA/G02156/4km/2026/"), DEFAULT_TTL)


class GeocodeTests(unittest.TestCase):
    def test_query_is_cleaned_and_short_queries_send_nothing(self):
        self.assertEqual(geocode.clean_query("  Bergen \n\t  Norway\x00 "), "Bergen Norway")
        self.assertEqual(len(geocode.clean_query("x" * 500)), geocode.MAX_QUERY)
        inner = FakeHttp()
        self.assertEqual(asyncio.run(geocode.search_places(inner, " a ")), [])
        self.assertEqual(inner.calls, [])

    def test_only_the_text_and_fixed_parameters_are_sent(self):
        inner = FakeHttp()
        asyncio.run(geocode.search_places(inner, "Bergen", "nb-NO"))
        kind, url, params = inner.calls[0]
        self.assertEqual(url, geocode.URL)
        self.assertEqual(dict(params), {"name": "Bergen", "count": "8", "format": "json", "language": "nb"})
        asyncio.run(geocode.search_places(inner, "Bergen", "../x"))
        self.assertEqual(dict(inner.calls[1][2])["language"], "en")

    def test_parse_keeps_only_sane_places(self):
        data = {"results": [
            {"name": "Bergen", "admin1": "Vestland", "country": "Norway", "latitude": 60.39299, "longitude": 5.32415, "elevation": 20.0},
            {"name": "Nowhere", "latitude": 95, "longitude": 0},
            {"name": "", "latitude": 1, "longitude": 1},
            {"name": "Deep", "latitude": 1, "longitude": 1, "elevation": 99999},
            "junk",
        ]}
        places = geocode.parse(data)
        self.assertEqual([p.name for p in places], ["Bergen", "Deep"])
        self.assertEqual(places[0].label, "Bergen, Vestland, Norway · 20 m")
        self.assertIsNone(places[1].elevation)
        self.assertEqual(geocode.parse({"results": "x"}), [])
        self.assertEqual(geocode.parse(None), [])


class DuplicateMemberTests(unittest.TestCase):
    def test_copies_of_one_model_count_once_per_hour(self):
        t0 = datetime(2026, 11, 10, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=1)
        same = Hour(temperature=16.7, wind_speed=10.0, precipitation=0.0, cloud_cover=40, humidity=70, pressure=1012)
        own = Hour(temperature=15.0, wind_speed=12.0, precipitation=0.2, cloud_cover=80, humidity=75, pressure=1011)
        members = {
            "ecmwf_ifs025": {t0: Hour(temperature=16.8, wind_speed=9.0, precipitation=0.0)},
            "knmi_seamless": {t0: same, t1: same},
            "dmi_seamless": {t0: same, t1: own},  # inside its area at t1
            "metno_seamless": {t0: same},
        }
        out = drop_copies(members)
        self.assertIn(t0, out["knmi_seamless"])
        self.assertNotIn(t0, out["dmi_seamless"])
        self.assertIn(t1, out["dmi_seamless"])
        self.assertNotIn("metno_seamless", out)  # nothing of its own left
        self.assertIn(t0, out["ecmwf_ifs025"])

    def test_sparse_hours_are_never_merged(self):
        t0 = datetime(2026, 11, 10, tzinfo=timezone.utc)
        thin = Hour(temperature=10.0)
        out = drop_copies({"a": {t0: thin}, "b": {t0: Hour(temperature=10.0)}})
        self.assertIn(t0, out["b"])


if __name__ == "__main__":
    unittest.main()
