"""Regression tests for the follow-up review of the fixes (2026-11-09)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core import safety  # noqa: E402
from core.geo import box_around  # noqa: E402
from core.model import Location  # noqa: E402
from core.privacy import Precision  # noqa: E402
from core.sources import esoh, ims, metar, rainviewer  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
HOME = Location(48.8566, 2.3522, 35.0)


async def _chunks(sizes):
    for n in sizes:
        yield b"x" * n


class ReadCappedTest(unittest.TestCase):
    def test_whole_body_within_the_limit(self):
        data = asyncio.run(safety.read_capped(_chunks([65536] * 30 + [3]), 2_000_000))
        self.assertEqual(len(data), 65536 * 30 + 3)  # an honest 2 MB answer arrives whole

    def test_refused_past_the_limit(self):
        with self.assertRaises(safety.UnsafeResponse):
            asyncio.run(safety.read_capped(_chunks([65536] * 400), 1_000_000))


class ColourHeaderTest(unittest.TestCase):
    def test_quoted_first_column(self):
        text = '"dBZ / RGBA","Universal Blue"\n10,#00000000\n20,#112233ff\n10,#00000000\n20,#445566ff\n'
        self.assertEqual(len(rainviewer.parse_colour_table(text)), 2)


class ImsOutsideTest(unittest.TestCase):
    def test_no_download_outside_the_grid(self):
        class Http:
            calls = 0

            async def get_text(self, *a, **k):
                Http.calls += 1
                return ""

            async def get_bytes(self, *a, **k):
                Http.calls += 1
                return b""

        asyncio.run(ims.ImsSnowSource().fetch(Http(), Location(-33.9, 18.4), Precision.KM_10, NOW))
        self.assertEqual(Http.calls, 0)


class CapCountsOnlyInBoxTest(unittest.TestCase):
    def test_far_entries_do_not_push_the_nearest_out(self):
        box = box_around(48.8566, 2.3522, 50)
        far = [{"icaoId": f"F{i:03d}", "obsTime": NOW.timestamp(), "lat": 10.0, "lon": 10.0} for i in range(metar.MAX_REPORTS + 5)]
        near = {"icaoId": "LFPB", "obsTime": NOW.timestamp(), "lat": 48.97, "lon": 2.43, "temp": 8}
        got = metar.parse(far + [near], HOME, "x", box)
        self.assertIn("LFPB", {r.station_id for r in got})

        def cov(lat, lon):
            return {"metocean:wigosId": "0-1-2-3", "ranges": {"air_temperature:2.0:point:PT0S": {"values": [7.0]}},
                    "domain": {"axes": {"x": {"values": [lon]}, "y": {"values": [lat]}, "t": {"values": ["2026-11-09T11:50:00Z"]}}}}

        doc = {"parameters": {"air_temperature:2.0:point:PT0S": {"unit": {"label": {"en": "Cel"}}}},
               "coverages": [cov(10.0, 10.0)] * (esoh.MAX_COVERAGES + 5) + [cov(48.86, 2.36)]}
        self.assertTrue(esoh.parse(doc, HOME, "x", box=box))


if __name__ == "__main__":
    unittest.main()
