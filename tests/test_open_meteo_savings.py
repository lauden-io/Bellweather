"""Fewer Open-Meteo calls (2026-11-11): Open-Meteo counts every point of a
request as one call, against a daily limit per IP address. Current values are
asked once per quarter hour; station heights once per station, shared by every
location and kept across restarts. Public test points only."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core.model import Location  # noqa: E402
from core.privacy import Precision  # noqa: E402
from core.sources import esoh, make_sources  # noqa: E402
from core.sources.esoh import EsohSource, StationHeights  # noqa: E402
from core.sources.open_meteo import OpenMeteoSource, quarter_hour  # noqa: E402

PARIS = Location(48.8566, 2.3522, 35.0)
NOW = datetime(2026, 11, 11, 20, 1, tzinfo=timezone.utc)
CURRENT = {"current": {"time": "2026-11-11T20:00", "temperature_2m": 9.5}, "hourly": {"time": [], "precipitation": []}}
STATIONS = {"features": [
    {"id": "0-250-0-07156", "geometry": {"coordinates": [2.3378, 48.8217]}, "properties": {"name": "PARIS_MONTSOURIS"}},
    {"id": "0-250-0-07149", "geometry": {"coordinates": [2.3841, 48.7183]}, "properties": {"name": "ORLY"}},
]}


class Http:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get_json(self, url, params=None, limit=0):
        self.calls.append((url, dict(params or {})))
        if url == esoh.LOCATIONS_URL:
            return STATIONS
        if url == esoh.URL:
            return {}
        return CURRENT


class CurrentValuesTests(unittest.TestCase):
    def test_once_per_quarter_hour(self):
        http, source = Http(), OpenMeteoSource()
        for minute in (1, 6, 11):  # three 5-minute updates in one quarter hour
            got = asyncio.run(source.fetch(http, PARIS, Precision.EXACT, NOW.replace(minute=minute)))
            self.assertTrue(got)  # the same readings every time
        self.assertEqual(len(http.calls), 1)
        asyncio.run(source.fetch(http, PARIS, Precision.EXACT, NOW.replace(minute=16)))  # a new quarter hour
        self.assertEqual(len(http.calls), 2)
        asyncio.run(source.fetch(http, Location(48.9, 2.4, 40.0), Precision.EXACT, NOW.replace(minute=17)))  # moved
        self.assertEqual(len(http.calls), 3)

    def test_a_failure_is_not_remembered(self):
        class Failing(Http):
            async def get_json(self, url, params=None, limit=0):
                raise OSError("down")

        source = OpenMeteoSource()
        with self.assertRaises(OSError):
            asyncio.run(source.fetch(Failing(), PARIS, Precision.EXACT, NOW))
        http = Http()
        asyncio.run(source.fetch(http, PARIS, Precision.EXACT, NOW + timedelta(minutes=5)))
        self.assertEqual(len(http.calls), 1)  # asked again at the next update

    def test_quarter_hour(self):
        self.assertEqual(quarter_hour(datetime(2026, 11, 11, 20, 44, 59, tzinfo=timezone.utc)),
                         datetime(2026, 11, 11, 20, 30, tzinfo=timezone.utc))


class StationHeightTests(unittest.TestCase):
    def lookup(self, asked):
        async def heights(points):
            asked.append(len(points))
            return [60.0 if i == 0 else None for i in range(len(points))]  # the second station: no height
        return heights

    def test_each_station_is_asked_once_and_shared_by_locations(self):
        store, asked, http = StationHeights(), [], Http()
        first = EsohSource(elevation_lookup=self.lookup(asked), heights=store)
        asyncio.run(first.fetch(http, PARIS, Precision.EXACT, NOW))
        asyncio.run(first.fetch(http, PARIS, Precision.EXACT, NOW + timedelta(minutes=5)))
        second = EsohSource(elevation_lookup=self.lookup(asked), heights=store)  # another location nearby
        asyncio.run(second.fetch(http, PARIS, Precision.EXACT, NOW))
        self.assertEqual(asked, [2])
        self.assertTrue(store.changed)

    def test_heights_survive_a_restart_but_missing_ones_are_asked_again(self):
        store, asked = StationHeights(), []
        asyncio.run(EsohSource(elevation_lookup=self.lookup(asked), heights=store).fetch(Http(), PARIS, Precision.EXACT, NOW))
        saved = store.dump()
        self.assertEqual(saved, {"heights": {"0-250-0-07156@48.8217,2.3378": 60.0}})
        restarted = StationHeights()
        restarted.load(saved)
        asyncio.run(EsohSource(elevation_lookup=self.lookup(asked), heights=restarted).fetch(Http(), PARIS, Precision.EXACT, NOW))
        self.assertEqual(asked, [2, 1])  # only the station without a height

    def test_a_moved_station_is_asked_again(self):
        store = StationHeights()
        store.put(StationHeights.key("0-250-0-07156", (48.8217, 2.3378)), 60.0)
        self.assertIn(StationHeights.key("0-250-0-07156", (48.8217, 2.3378)), store)
        self.assertNotIn(StationHeights.key("0-250-0-07156", (48.9, 2.3378)), store)

    def test_load_takes_only_sane_entries(self):
        store = StationHeights()
        store.load({"heights": {"a@1,2": 100, "b@1,2": 99999, "c@1,2": "x", 5: 10, "x" * 101: 10}})
        self.assertEqual(store.dump(), {"heights": {"a@1,2": 100.0}})
        for broken in (None, [], {"heights": []}, "junk"):
            StationHeights().load(broken)  # never raises
        self.assertFalse(store.changed)

    def test_make_sources_passes_the_shared_store(self):
        store = StationHeights()
        sources = make_sources(["esoh", "open_meteo"], Http(), heights=store)
        esoh_source = next(s for s in sources if s.key == "esoh")
        self.assertIs(esoh_source._heights, store)


if __name__ == "__main__":
    unittest.main()
