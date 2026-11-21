"""Benchmark raw data: an export splits the data by licence, gives the same
bytes for the same data, and loads back into a database that scores exactly
like the original. Synthetic data at public test points only."""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "bellweather"))
sys.path.insert(0, str(ROOT / "benchmark" / "app"))

import export  # noqa: E402
import report  # noqa: E402
import runner  # noqa: E402

UTC = timezone.utc
START = runner.START
POINTS = [
    {"id": "na1", "continent": "NA", "latitude": 40.7, "longitude": -74.0,
     "truth": {"kind": "metar", "id": "KNYC", "elevation_m": 47}, "mosmix": {"id": "72503", "distance_km": 10.0}},
    {"id": "eu1", "continent": "EU", "latitude": 60.45, "longitude": 22.27,
     "truth": {"kind": "esoh", "id": "0-246-0-100949"}, "mosmix": {"id": "02972", "distance_km": 50.0}},
]
OFFSETS = {"bellweather": 0.5, "control": 1.0, "ecmwf_ifs": -1.5, "met_norway": 0.0, "open_meteo": 2.0, "openweather": 0.3}


def truth_t(h: datetime) -> float:
    return 10 + 3 * math.sin(h.timestamp() / 3600 / 24 * 2 * math.pi)


def build_db(path: Path, days: int = 9) -> None:
    con = sqlite3.connect(path)
    con.executescript(runner.SCHEMA)
    iso = runner.iso
    rows, runs = [], []
    for c in range(days * 6):
        capture = START + timedelta(hours=4 * c)
        for point in POINTS:
            for provider, off in OFFSETS.items():
                step = 3 if provider == "openweather" else 1
                for k in range(0, 97):
                    target = capture + timedelta(hours=k)
                    if target.hour % step:
                        continue
                    rows.append((iso(capture), point["id"], provider, iso(target), "t", truth_t(target) + off))
                    rows.append((iso(capture), point["id"], provider, iso(target), "p", 1013.25 + off / 3))
                runs.append((iso(capture), "forecast", point["id"], provider, "ok 194"))
        runs.append((iso(capture), "method", "", "", runner.METHOD))
    con.executemany("INSERT INTO forecast VALUES (?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO run VALUES (?,?,?,?,?)", runs)
    obs = []
    for i in range(24 * days):
        h = START + timedelta(hours=i)
        rep = h - timedelta(minutes=10)  # METAR at :50
        obs += [("KNYC", iso(rep), "t", truth_t(h)), ("KNYC", iso(rep), "qnh", 1012.0),
                ("KNYC", iso(rep), "wx", json.dumps("-RA" if rep.hour in (12, 13) else "")),
                ("0-246-0-100949", iso(h), "air_temperature:2.0:point:PT0S", truth_t(h) + 273.15)]
    con.executemany("INSERT INTO observation VALUES (?,?,?,?)", obs)
    con.commit()
    con.close()


def rows_of(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return [line.rstrip("\n").split(",", 5) for line in f]


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.db = cls.dir / "bench.sqlite"
        build_db(cls.db)
        cls.points = cls.dir / "points.json"
        cls.points.write_text(json.dumps({"points": POINTS}), encoding="utf-8")
        cls.week1, cls.week2 = START + timedelta(days=7), START + timedelta(days=9)
        cls.first = export.export(cls.db, POINTS, cls.dir / "one", START, cls.week1, name="one", version="t",
                                  commit="abc", clean=True)
        cls.second = export.export(cls.db, POINTS, cls.dir / "two", cls.week1, cls.week2, name="two")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_are_split_by_licence(self):
        files = {f["name"]: f for f in self.first["files"]}
        self.assertEqual(files["forecasts-openweather.csv.gz"]["licence"], export.ODBL)
        self.assertEqual(files["forecasts.csv.gz"]["licence"], export.CC_BY)
        self.assertEqual(files["observations-metar.csv.gz"]["licence"], export.NOAA)
        providers = {r[2] for r in rows_of(self.dir / "one" / "forecasts.csv.gz")[1:]}
        self.assertEqual(providers, set(OFFSETS) - {"openweather"})
        self.assertEqual({r[2] for r in rows_of(self.dir / "one" / "forecasts-openweather.csv.gz")[1:]}, {"openweather"})
        self.assertEqual({r[0] for r in rows_of(self.dir / "one" / "observations-metar.csv.gz")[1:]}, {"KNYC"})
        self.assertEqual({r[0] for r in rows_of(self.dir / "one" / "observations-esoh.csv.gz")[1:]}, {"0-246-0-100949"})
        self.assertTrue((self.dir / "one" / "ATTRIBUTION.md").read_text(encoding="utf-8").startswith("# Sources and licences"))
        self.assertEqual(self.first["code"], {"repository": export.REPOSITORY, "commit": "abc", "clean": True,
                                              "addon_version": "t"})
        self.assertFalse((self.dir / "one.partial").exists())

    def test_forecasts_are_the_captures_of_the_period_and_add_up(self):
        captures_one = {r[0] for r in rows_of(self.dir / "one" / "forecasts.csv.gz")[1:]}
        captures_two = {r[0] for r in rows_of(self.dir / "two" / "forecasts.csv.gz")[1:]}
        self.assertEqual(min(captures_one), runner.iso(START))
        self.assertEqual(max(captures_one), runner.iso(self.week1 - timedelta(hours=4)))
        self.assertEqual(min(captures_two), runner.iso(self.week1))
        con = sqlite3.connect(self.db)
        total = con.execute("SELECT COUNT(*) FROM forecast").fetchone()[0]
        con.close()
        rows = sum(f["rows"] for m in (self.first, self.second) for f in m["files"] if f["table"] == "forecast")
        self.assertEqual(rows, total)

    def test_the_same_data_give_the_same_bytes(self):
        again = export.export(self.db, POINTS, self.dir / "again", START, self.week1, name="one", version="t",
                              commit="abc", clean=True)
        self.assertEqual([f["sha256"] for f in again["files"]], [f["sha256"] for f in self.first["files"]])

    def test_recorded_until_cuts_reports_and_outcomes(self):
        cut = START + timedelta(days=7, hours=6, minutes=16)
        m = export.export(self.db, POINTS, self.dir / "cut", START, self.week1, recorded_until=cut)
        self.assertEqual(m["recorded_until"], runner.iso(cut))
        times = [r[1] for r in rows_of(self.dir / "cut" / "observations-metar.csv.gz")[1:]]
        self.assertLessEqual(max(times), runner.iso(cut))
        runs = [r[0] for r in rows_of(self.dir / "cut" / "captures.csv.gz")[1:]]
        self.assertLessEqual(max(runs), runner.iso(cut))

    def test_loaded_exports_score_exactly_like_the_original(self):
        rebuilt = self.dir / "rebuilt.sqlite"
        counts = export.load(rebuilt, [self.dir / "one", self.dir / "two"])
        con, orig = sqlite3.connect(rebuilt), sqlite3.connect(self.db)
        for table in ("forecast", "observation"):
            q = f"SELECT * FROM {table} ORDER BY 1, 2, 3, 4"
            self.assertEqual(con.execute(q).fetchall(), orig.execute(q).fetchall(), table)
        self.assertEqual(sorted(con.execute("SELECT * FROM run").fetchall()), sorted(orig.execute("SELECT * FROM run").fetchall()))
        self.assertEqual(counts["run"], orig.execute("SELECT COUNT(*) FROM run").fetchone()[0])  # no duplicates
        con.close()
        orig.close()

        def body(db):
            text = report.build(db, self.points, 7, self.week1)
            return [line for line in text.splitlines() if "**Generated:**" not in line]

        self.assertEqual(body(rebuilt), body(self.db))

    def test_a_changed_file_is_refused(self):
        bad = self.dir / "bad"
        export.export(self.db, POINTS, bad, START, self.week1)
        with gzip.open(bad / "captures.csv.gz", "at", encoding="utf-8") as f:
            f.write("2026-11-11T03:00:00Z,forecast,na1,bellweather,ok 1\n")
        with self.assertRaises(ValueError):
            export.load(self.dir / "refused.sqlite", [bad])

    def test_the_add_on_writes_the_raw_data_with_each_report_from_the_second_on(self):
        root = self.dir / "addon"
        (root / "data").mkdir(parents=True)
        build_db(root / "data" / "benchmark.sqlite", days=15)
        (root / "points.json").write_text(json.dumps({"points": POINTS}), encoding="utf-8")
        saved = runner.DATA, runner.SHARE, runner.APP, runner.BUILD
        runner.DATA, runner.SHARE, runner.APP, runner.BUILD = root / "data", root / "share", root, {"commit": "abc", "clean": True}
        try:
            runner.write_reports(START + timedelta(days=14, hours=6))
        finally:
            runner.DATA, runner.SHARE, runner.APP, runner.BUILD = saved
        reports = sorted(p.name for p in (root / "share" / "reports").iterdir())
        self.assertEqual(reports, ["2026-11-17-1-week.md", "2026-11-24-2-weeks.md", "status.md"])
        self.assertEqual([p.name for p in (root / "share" / "exports").iterdir()], ["2026-11-24-2-weeks"])
        manifest = json.loads((root / "share" / "exports" / "2026-11-24-2-weeks" / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["forecasts"], {"captures_from": runner.iso(START + timedelta(days=7)),
                                                 "captures_before": runner.iso(START + timedelta(days=14))})
        self.assertEqual((manifest["code"]["commit"], manifest["code"]["addon_version"]), ("abc", runner.VERSION))

    def test_report_adds_an_every_hour_table_without_openweather(self):
        text = report.build(self.db, self.points, 7, self.week1)
        self.assertIn("## All addresses, every hour, without DWD and OpenWeather", text)
        rest = text.split("## All addresses, without DWD\n")[1].split("## All addresses, every hour")[0]
        hourly = text.split("## All addresses, every hour, without DWD and OpenWeather")[1]
        paired = [int(s.split(" paired hours")[0].split("· ")[-1].replace(",", "")) for s in (rest, hourly)]
        self.assertEqual(paired[1], 3 * paired[0])  # OpenWeather pairs every third hour only
        self.assertIn("| OpenWeather |", rest)
        self.assertNotIn("| OpenWeather |", hourly)


if __name__ == "__main__":
    unittest.main()
