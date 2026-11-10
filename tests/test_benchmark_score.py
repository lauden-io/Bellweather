"""Benchmark scoring and report on synthetic data (no network)."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark" / "app"))

import report  # noqa: E402
import score  # noqa: E402

START = datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)
OFFSETS = {"bellweather": 0.5, "control": 1.0, "ecmwf_ifs": -1.5, "dwd_mosmix": 0.2, "met_norway": 0.0, "open_meteo": 2.0}
POINTS = [
    {"id": "na1", "continent": "NA", "latitude": 40.7, "longitude": -74.0,
     "truth": {"kind": "metar", "id": "KNYC"}, "mosmix": {"id": "72503", "distance_km": 10.0}},
    {"id": "eu1", "continent": "EU", "latitude": 60.45, "longitude": 22.27,
     "truth": {"kind": "esoh", "id": "0-246-0-100949"}, "mosmix": {"id": "02972", "distance_km": 50.0}},
]


def truth_t(h: datetime) -> float:
    return 10 + 3 * math.sin(h.timestamp() / 3600 / 24 * 2 * math.pi)


def build_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE forecast (capture TEXT, point TEXT, provider TEXT, target TEXT, var TEXT, value REAL,
            PRIMARY KEY (capture, point, provider, target, var));
        CREATE TABLE observation (station TEXT, time TEXT, var TEXT, value REAL, PRIMARY KEY (station, time, var));
    """)
    iso = score_iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    rows = []
    for c in range(0, 8 * 6):  # 8 days of captures every 4 h
        capture = START + timedelta(hours=4 * c)
        for point in POINTS:
            for provider, off in OFFSETS.items():
                for k in range(0, 97):
                    target = capture + timedelta(hours=k)
                    rows.append((iso(capture), point["id"], provider, iso(target), "t", truth_t(target) + off))
                    rows.append((iso(capture), point["id"], provider, iso(target), "r", 1.0 if target.hour in (12, 13, 14) else 0.0))
    con.executemany("INSERT INTO forecast VALUES (?,?,?,?,?,?)", rows)
    obs = []
    for i in range(0, 24 * 9):
        h = START + timedelta(hours=i)
        rep = h - timedelta(minutes=10)  # METAR at :50
        obs.append(("KNYC", score_iso(rep), "t", truth_t(h)))
        obs.append(("KNYC", score_iso(rep), "wx", json.dumps("-RA" if rep.hour in (12, 13) else "")))
        obs.append(("0-246-0-100949", score_iso(h), "air_temperature:2.0:point:PT0S", truth_t(h) + 273.15))
        obs.append(("0-246-0-100949", score_iso(h + timedelta(hours=1)), "precipitation_amount:1.5:sum:PT1H",
                    0.5 if h.hour in (12, 13) else 0.0))
    con.executemany("INSERT INTO observation VALUES (?,?,?,?)", obs)
    con.commit()
    con.close()


class BenchmarkScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "b.sqlite"
        build_db(cls.db)
        cls.points = Path(cls.tmp.name) / "points.json"
        cls.points.write_text(json.dumps({"points": POINTS}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_lead_selection_takes_the_latest_capture_in_the_band(self):
        t = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
        rows = [(t - timedelta(hours=h), float(h)) for h in (31, 27, 23, 19)]
        self.assertEqual(score.at_lead(sorted(rows), t, 24, 28), 27.0)
        self.assertIsNone(score.at_lead(sorted(rows), t, 72, 76))

    def test_truth_from_both_kinds_of_station(self):
        con = sqlite3.connect(self.db)
        self.addCleanup(con.close)
        hourly, wet = score.truth(con, "KNYC", "metar")
        h = datetime(2026, 11, 12, 6, tzinfo=timezone.utc)
        self.assertAlmostEqual(hourly[h]["t"], truth_t(h))  # the :50 report counts for the next full hour
        self.assertTrue(wet[datetime(2026, 11, 12, 12, tzinfo=timezone.utc)])
        hourly, wet = score.truth(con, "0-246-0-100949", "esoh")
        self.assertAlmostEqual(hourly[h]["t"], truth_t(h), places=6)  # kelvin converted
        self.assertTrue(wet[datetime(2026, 11, 12, 12, tzinfo=timezone.utc)])  # the hourly sum belongs to the hour before its stamp

    def test_scores_are_the_known_offsets_and_dwd_counts_only_near_its_point(self):
        con = sqlite3.connect(self.db)
        self.addCleanup(con.close)
        end, start = START + timedelta(days=8), START + timedelta(days=4)
        res = score.score(con, POINTS, start, end, ["bellweather", "control", "ecmwf_ifs", "met_norway", "open_meteo"])
        day1 = res.mae["day 1"]["t"]
        self.assertAlmostEqual(day1.mae("bellweather"), 0.5, places=6)
        self.assertAlmostEqual(day1.mae("ecmwf_ifs"), 1.5, places=6)
        self.assertEqual(set(res.by_group), {"NA", "EU"})
        near = [p for p in POINTS if p["mosmix"]["distance_km"] <= score.DWD_MAX_KM]
        res_dwd = score.score(con, near, start, end, ["bellweather", "dwd_mosmix"])
        self.assertEqual(set(res_dwd.by_group), {"NA"})
        self.assertAlmostEqual(res_dwd.mae["day 1"]["t"].mae("dwd_mosmix"), 0.2, places=6)
        self.assertGreater(res.blocks, 0)
        self.assertEqual(score.csi(res.csi["bellweather"]), 1.0)  # forecast wet 12-15 h, observed wet 12-14 h: same blocks
        self.assertAlmostEqual(res.daily_max.mae("met_norway"), 0.0, places=6)

    def test_report_renders(self):
        text = report.build(self.db, self.points, 7, START + timedelta(days=8))
        self.assertIn("# Benchmark report: 1 week", text)
        self.assertIn("All addresses, without DWD", text)
        self.assertIn("| **Bellweather** | 0.50 °C |", text)
        self.assertIn("| MET Norway (Yr) | **0.00 °C** |", text)  # the best value is bold, whoever it is
        self.assertIn("One week is too short", text)


if __name__ == "__main__":
    unittest.main()
