"""Regression tests for the findings of the independent security review (2026-11-09)."""

from __future__ import annotations

import struct
import sys
import time
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core import nowcast as nc  # noqa: E402
from core import png, safety  # noqa: E402
from core.forecast import history  # noqa: E402
from core.geo import box_around  # noqa: E402
from core.model import Location  # noqa: E402
from core.sources import esoh, ims, metar, rainviewer  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
HOME = Location(48.8566, 2.3522, 35.0)


class CsvTest(unittest.TestCase):
    def test_wide_header_is_refused_fast(self):
        text = ",".join(f"c{i}" for i in range(2000)) + "\n" + ("x\n" * 3000)
        start = time.perf_counter()
        with self.assertRaises(safety.UnsafeResponse):
            rainviewer.parse_colour_table(text)
        with self.assertRaises(safety.UnsafeResponse):
            history.parse_iem(text)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_normal_tables_still_parse(self):
        table = rainviewer.parse_colour_table("dBZ / RGBA,Universal Blue\n10,#00000000\n20,#112233ff\n10,#00000000\n20,#445566ff\n")
        self.assertEqual(len(table), 2)


class EsohTest(unittest.TestCase):
    def _doc(self, n_cov: int, n_top: int, lat: float = 48.86, lon: float = 2.36) -> dict:
        cov = {
            "metocean:wigosId": "0-1-2-3",
            "domain": {"axes": {"x": {"values": [lon]}, "y": {"values": [lat]}, "t": {"values": ["2026-11-09T11:50:00Z"]}}},
            "ranges": {"air_temperature:2.0:point:PT0S": {"values": [7.0]}},
        }
        top = {f"p{i}": {} for i in range(n_top)}
        top["air_temperature:2.0:point:PT0S"] = {"unit": {"label": {"en": "Cel"}}}
        return {"parameters": top, "coverages": [cov] * n_cov}

    def test_no_quadratic_merge(self):
        start = time.perf_counter()
        got = esoh.parse(self._doc(2000, 50_000), HOME, "x")
        self.assertLess(time.perf_counter() - start, 2.0)
        self.assertLessEqual(len(got), esoh.MAX_COVERAGES)

    def test_outside_the_box_is_dropped(self):
        box = box_around(48.8566, 2.3522, 40)
        self.assertEqual(esoh.parse(self._doc(1, 1, lat=10.0, lon=10.0), HOME, "x", box=box), [])
        self.assertTrue(esoh.parse(self._doc(1, 1), HOME, "x", box=box))

    def test_one_broken_coverage_does_not_kill_the_rest(self):
        doc = self._doc(1, 1)
        doc["coverages"] = [{"domain": "not a dict"}, 42, doc["coverages"][0]]
        self.assertTrue(esoh.parse(doc, HOME, "x"))

    def test_locations_capped_and_boxed(self):
        box = box_around(48.8566, 2.3522, 40)
        feats = [{"id": f"0-1-2-{i}", "geometry": {"coordinates": [2.35, 48.86]}, "properties": {"name": "N"}} for i in range(1000)]
        feats.append({"id": "far", "geometry": {"coordinates": [100.0, 10.0]}, "properties": {"name": "F"}})
        got = esoh.parse_locations({"features": feats}, box)
        self.assertLessEqual(len(got), esoh.MAX_LOCATIONS)
        self.assertNotIn("far", got)


class MetarTest(unittest.TestCase):
    def test_huge_time_or_bad_clouds_skip_only_that_report(self):
        good = {"icaoId": "LFPB", "obsTime": NOW.timestamp(), "lat": 48.97, "lon": 2.43, "temp": 8}
        reports = [
            {"icaoId": "AAAA", "obsTime": 1e20, "lat": 48.9, "lon": 2.4},
            {"icaoId": "BBBB", "obsTime": NOW.timestamp(), "lat": 48.9, "lon": 2.4, "clouds": "OVC"},
            {"icaoId": "CCCC", "obsTime": NOW.timestamp(), "lat": 48.9, "lon": 2.4, "clouds": [1, 2, {"cover": "BKN"}]},
            good,
        ]
        ids = {r.station_id for r in metar.parse(reports, HOME, "x")}
        self.assertIn("LFPB", ids)
        self.assertNotIn("AAAA", ids)

    def test_outside_the_box_is_dropped(self):
        box = box_around(48.8566, 2.3522, 50)
        rep = [{"icaoId": "KORD", "obsTime": NOW.timestamp(), "lat": 41.98, "lon": -87.9, "temp": 8}]
        self.assertEqual(metar.parse(rep, HOME, "x", box), [])


class RadarTest(unittest.TestCase):
    def test_index_with_overflowing_time(self):
        maps = {"host": rainviewer.ALLOWED_HOST, "radar": {"past": [{"time": 1e20, "path": "/v2/radar/abcdef"}]}}
        self.assertEqual(rainviewer.valid_frames(maps, NOW), [])

    def test_tile_size_is_enforced(self):
        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        w, h = 4096, 2
        raw = (b"\x00" + b"\x00" * (w * 4)) * h
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        with self.assertRaises(png.PngError):
            png.pixel(data, 0, 0, (256, 256))

    def test_neighbour_tiles_do_not_depend_on_the_position_in_the_tile(self):
        a = nc.block_3x3(64 * 256 + 5, 44 * 256 + 5)
        b = nc.block_3x3(64 * 256 + 250, 44 * 256 + 250)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 9)


class ImsTest(unittest.TestCase):
    def test_outside_the_grid(self):
        self.assertIsNone(ims.cell(-10.0, 0.0))

    def test_year_zero_in_the_index(self):
        self.assertIsNone(ims.newest_file('<a href="ims0000001_4km_v1.3.asc.gz"></a>'))


class TextTest(unittest.TestCase):
    def test_format_characters_removed(self):
        self.assertEqual(safety.clean_text("Pa‮ris​"), "Paris")


class MetGatingTest(unittest.TestCase):
    def test_gate_uses_the_shared_point(self):
        import asyncio
        from core.forecast.met_nowcast import MetNowcast
        from core.privacy import Precision

        class NoHttp:
            calls = 0

            async def get_json(self, *a, **k):
                NoHttp.calls += 1
                return None

        # 53.505 N lies in the 10 km cell 53.449-53.539 whose centre (53.494) is south of the gate:
        # the home is north of 53.5, but the shared point decides, so nothing is asked.
        asyncio.run(MetNowcast().fetch(NoHttp(), Location(53.505, 10.0), Precision.KM_10, NOW))
        self.assertEqual(NoHttp.calls, 0)


if __name__ == "__main__":
    unittest.main()
