"""Benchmark: station reports missed during a gap (the host was off, or
requests failed) are fetched again while the services keep them; reports
list missed captures; daily highs and lows use one capture for everyone.
Public test points only."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "bellweather"))
sys.path.insert(0, str(ROOT / "benchmark" / "app"))

import report  # noqa: E402
import runner  # noqa: E402
import score  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 13, 17, 48, tzinfo=UTC)


def memory_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(runner.SCHEMA)
    return con


def hourly_runs(con, source: str, first: datetime, last: datetime, recent: timedelta) -> None:
    t = first
    while t <= last:
        runner.fetched(con, source, t - recent, t)
        t += timedelta(hours=1)


class FetchFromTests(unittest.TestCase):
    def test_nothing_fetched_yet_reaches_as_far_as_the_service_keeps(self):
        con = memory_db()
        self.assertEqual(runner.fetch_from(con, "metar", NOW, runner.METAR_RECENT, runner.METAR_KEEPS), NOW - runner.METAR_KEEPS)
        early = runner.START + timedelta(hours=5)  # never before the benchmark's start
        self.assertEqual(runner.fetch_from(con, "metar", early, runner.METAR_RECENT, runner.METAR_KEEPS),
                         runner.START - timedelta(hours=1))

    def test_hourly_runs_without_a_gap_ask_for_the_recent_hours_only(self):
        con = memory_db()
        hourly_runs(con, "metar", NOW - timedelta(hours=60), NOW - timedelta(hours=1), runner.METAR_RECENT)
        self.assertEqual(runner.fetch_from(con, "metar", NOW, runner.METAR_RECENT, runner.METAR_KEEPS), NOW - runner.METAR_RECENT)

    def test_after_an_outage_the_request_reaches_back_to_the_gap(self):
        con = memory_db()
        off, back = NOW - timedelta(hours=10), NOW - timedelta(minutes=30)
        hourly_runs(con, "metar", NOW - timedelta(hours=60), off, runner.METAR_RECENT)
        runner.fetched(con, "metar", back - runner.METAR_RECENT, back)  # the first run after the restart: recent hours only
        start = runner.fetch_from(con, "metar", NOW, runner.METAR_RECENT, runner.METAR_KEEPS)
        self.assertEqual(start, off - timedelta(minutes=30))
        self.assertEqual(runner.fetch_from(con, "esoh:x", NOW, runner.ESOH_RECENT, runner.ESOH_KEEPS),
                         NOW - runner.ESOH_KEEPS)  # each source on its own

    def test_a_gap_older_than_the_service_keeps_reaches_only_that_far(self):
        con = memory_db()
        hourly_runs(con, "esoh:x", NOW - timedelta(hours=50), NOW - timedelta(hours=30), runner.ESOH_RECENT)
        hourly_runs(con, "esoh:x", NOW - timedelta(hours=3), NOW - timedelta(hours=1), runner.ESOH_RECENT)
        self.assertEqual(runner.fetch_from(con, "esoh:x", NOW, runner.ESOH_RECENT, runner.ESOH_KEEPS), NOW - runner.ESOH_KEEPS)


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_metar = False

    async def get_json(self, url, params=None, limit=0):
        self.calls.append((url, dict(params or {})))
        if "aviationweather" in url:
            if self.fail_metar:
                raise runner.HttpStatusError(503)
            return [{"icaoId": "KNYC", "reportTime": "2026-11-13T09:51:00.000Z", "temp": 8.3}]
        return None  # E-SOH: nothing for this station (404)


POINTS = [
    {"id": "na2", "truth": {"kind": "metar", "id": "KNYC"}},
    {"id": "eu4", "truth": {"kind": "esoh", "id": "0-246-0-100949", "latitude": 60.4544, "longitude": 22.1787}},
]


class RecordObservationsTests(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp()
        self.saved = runner.HTTP, runner.asyncio.sleep
        runner.HTTP = self.http

        async def no_wait(_seconds):
            return None

        runner.asyncio.sleep = no_wait

    def tearDown(self):
        runner.HTTP, runner.asyncio.sleep = self.saved

    def test_the_first_run_after_an_outage_fills_the_gap_and_then_goes_back_to_normal(self):
        con = memory_db()
        off = NOW - timedelta(hours=10)
        hourly_runs(con, "metar", NOW - timedelta(hours=60), off, runner.METAR_RECENT)
        hourly_runs(con, "esoh:0-246-0-100949", NOW - timedelta(hours=30), off, runner.ESOH_RECENT)
        asyncio.run(runner.record_observations(con, POINTS, NOW))
        metar = [p for u, p in self.http.calls if "aviationweather" in u]
        esoh = [p for u, p in self.http.calls if "meteogate" in u]
        self.assertEqual(metar[0]["hours"], "11")  # 10 h since the last run + 30 min margin, rounded up
        self.assertEqual(esoh[0]["datetime"], runner.iso(off - timedelta(minutes=30)) + "/..")
        self.assertEqual(con.execute("SELECT value FROM observation WHERE station='KNYC' AND var='t'").fetchone()[0], 8.3)

        self.http.calls.clear()
        later = NOW + timedelta(hours=1)
        asyncio.run(runner.record_observations(con, POINTS, later))
        self.assertEqual([p["hours"] for u, p in self.http.calls if "aviationweather" in u], ["3"])
        self.assertEqual([p["datetime"] for u, p in self.http.calls if "meteogate" in u],
                         [runner.iso(later - runner.ESOH_RECENT) + "/.."])

    def test_a_failed_request_is_made_up_by_the_next_one(self):
        con = memory_db()
        hourly_runs(con, "metar", NOW - timedelta(hours=60), NOW - timedelta(hours=1), runner.METAR_RECENT)
        self.http.fail_metar = True
        asyncio.run(runner.record_observations(con, POINTS, NOW))
        self.http.fail_metar = False
        self.http.calls.clear()
        later = NOW + timedelta(hours=2)
        asyncio.run(runner.record_observations(con, POINTS, later))
        self.assertEqual([p["hours"] for u, p in self.http.calls if "aviationweather" in u], ["4"])


class ReportAndDailyTests(unittest.TestCase):
    def test_report_lists_missed_captures(self):
        start = runner.START
        recorded = {runner.iso(start + timedelta(hours=4 * i)) for i in range(6) if i not in (3, 4)}
        line = report.captures_line(start, start + timedelta(days=1), recorded)[0]
        self.assertTrue(line.startswith("**Captures:** 4 of 6 made (missed: 11 Nov 11:00, 11 Nov 15:00 UTC)"))
        self.assertEqual(report.captures_line(start, start + timedelta(days=1), {runner.iso(start + timedelta(hours=4 * i))
                                                                              for i in range(6)})[0], "**Captures:** 6 of 6 made.")

    def test_daily_extremes_use_the_latest_capture_every_provider_answered(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(Path(tmp) / "d.sqlite")
            con.executescript(runner.SCHEMA)
            day = datetime(2026, 11, 14, tzinfo=UTC)
            iso = runner.iso
            rows = []
            for capture, provider, value in ((day - timedelta(hours=33), "a", 11.0), (day - timedelta(hours=9), "a", 15.0),
                                             (day - timedelta(hours=33), "b", 10.0)):  # b failed at the later capture
                for k in range(24):
                    rows.append((iso(capture), "p1", provider, iso(day + timedelta(hours=k)), "t", value))
            con.executemany("INSERT INTO forecast VALUES (?,?,?,?,?,?)", rows)
            con.executemany("INSERT INTO observation VALUES (?,?,?,?)",
                            [("KXYZ", iso(day + timedelta(hours=k)), "t", 10.0) for k in range(24)])
            con.commit()
            point = {"id": "p1", "continent": "EU", "latitude": 51.5, "longitude": 0.0,
                     "truth": {"kind": "metar", "id": "KXYZ"}, "mosmix": {"distance_km": 5.0}}
            res = score.score(con, [point], day, day + timedelta(days=1), ["a", "b"])
            con.close()
        self.assertEqual(res.days, 1)
        self.assertAlmostEqual(res.daily_max.mae("a"), 1.0)  # the shared earlier capture, not its own later one
        self.assertAlmostEqual(res.daily_max.mae("b"), 0.0)


if __name__ == "__main__":
    unittest.main()
