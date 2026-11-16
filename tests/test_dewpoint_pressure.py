"""Dew point and sea-level pressure are verified and corrected like
temperature (2026-11-10). Public test points only."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core.derive import sea_level_pressure  # noqa: E402
from core.forecast import blend, history, skill  # noqa: E402
from core.forecast.members import Hour, parse_previous  # noqa: E402
from core.model import Field, Location  # noqa: E402
from core.privacy import Precision  # noqa: E402
from core.sources import metar  # noqa: E402

PARIS = Location(48.8566, 2.3522, 35.0)
START = datetime(2026, 10, 1, tzinfo=timezone.utc)


class DewPointPressureTests(unittest.TestCase):
    def test_archive_gives_dew_point_and_pressure(self):
        text = ("station,valid,elevation,tmpc,sknt,dwpc,mslp,alti,wxcodes\n"
                "KORD,2026-11-03 00:51,205.00,6.00,7.00,2.00,1015.2,29.98,M\n"
                "LFPB,2026-11-03 02:00,66.00,8.00,5.00,4.00,M,30.00,M\n"
                "LFPB,2026-11-03 03:00,66.00,8.00,5.00,99.00,M,M,M\n")
        got = history.parse_iem(text)
        h = lambda hour: datetime(2026, 11, 3, hour, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(got[h(1)]["d"], 2.0)
        self.assertEqual(got[h(1)]["pr"], 1015.2)  # sea-level pressure from the remarks wins
        # otherwise QNH converted with the airport's height and temperature
        self.assertEqual(got[h(2)]["pr"], round(sea_level_pressure(30.00 * 33.8639, 66.0, 8.0), 1))
        self.assertNotIn("d", got[h(3)])  # 99 °C dew point is broken data
        self.assertNotIn("pr", got[h(3)])

    def test_archive_qnh_needs_the_height_and_uses_12_hours_of_temperature(self):
        rows = ["station,valid,elevation,tmpc,sknt,dwpc,mslp,alti,wxcodes",
                "SKMD,2026-11-03 00:00,1499.00,16.00,3.00,12.00,M,30.09,M",
                "SKMD,2026-11-03 12:00,1499.00,26.00,3.00,12.00,M,30.09,M"]
        got = history.parse_iem("\n".join(rows) + "\n")
        noon = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        self.assertAlmostEqual(got[noon]["pr"], round(sea_level_pressure(30.09 * 33.8639, 1499.0, 26.0, 16.0), 1))
        self.assertLess(got[noon]["pr"], 30.09 * 33.8639 - 8)  # QNH as it is would be about 10 hPa too high here
        no_height = history.parse_iem("station,valid,tmpc,sknt,dwpc,mslp,alti,wxcodes\nSKMD,2026-11-03 00:00,16.00,3.00,12.00,M,30.09,M\n")
        self.assertNotIn("pr", no_height[datetime(2026, 11, 3, 0, tzinfo=timezone.utc)])

    def test_sea_level_pressure_from_qnh(self):
        self.assertAlmostEqual(sea_level_pressure(1013.25, 0.0, 15.0), 1013.25)  # at sea level QNH is sea-level pressure
        self.assertGreater(sea_level_pressure(1013.25, 150.0, -10.0), 1014.5)  # a cold column weighs more
        self.assertLess(sea_level_pressure(1013.25, 150.0, 30.0), 1012.5)
        self.assertAlmostEqual(sea_level_pressure(1019.0, 1499.0, 26.0, 18.0), sea_level_pressure(1019.0, 1499.0, 22.0))
        for broken in ((800.0, 0.0, 10.0), (1013.0, 6000.0, 10.0), (1013.0, 100.0, 80.0)):
            self.assertIsNone(sea_level_pressure(*broken))

    def test_metar_converts_qnh_and_remembers_12_hours(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        rep = {"icaoId": "SKMD", "lat": 6.22, "lon": -75.59, "elev": 1499, "name": "Medellin/Herrera, ANT, CO",
               "temp": 26, "dewp": 12, "altim": 1019.0}
        here = Location(6.24, -75.58, 1500.0)
        pressure = lambda readings: next(r.value for r in readings if r.field is Field.PRESSURE)  # noqa: E731
        got = metar.parse([{**rep, "obsTime": int(now.timestamp())}], here, "x")
        self.assertAlmostEqual(pressure(got), sea_level_pressure(1019.0, 1499.0, 26.0))
        self.assertEqual(pressure(metar.parse([{**rep, "slp": 1009.4, "obsTime": int(now.timestamp())}], here, "x")), 1009.4)
        self.assertFalse([r for r in metar.parse([{**rep, "elev": None, "obsTime": int(now.timestamp())}], here, "x")
                          if r.field is Field.PRESSURE])  # no height: no pressure rather than raw QNH
        source = metar.MetarSource()
        source._remember(metar.parse([{**rep, "temp": 16, "obsTime": int((now - timedelta(hours=12)).timestamp())}], here, "x"),
                         now - timedelta(hours=12))
        got = metar.parse([{**rep, "obsTime": int(now.timestamp())}], here, "x", None, source._temps)
        self.assertAlmostEqual(pressure(got), sea_level_pressure(1019.0, 1499.0, 26.0, 16.0))
        source._remember(got, now + timedelta(hours=3))
        self.assertEqual(sorted(source._temps["SKMD"]), [now])  # older than 14 hours: forgotten

    def test_metar_memory_holds_a_report_at_20_past_until_it_is_needed(self):
        """A station reporting at :20 files each report under the nearest full
        hour, 00:20 under 00:00. At 12:15 the newest report is still 11:20,
        which needs 23:20 of the day before, filed under 23:00. Fetches every
        5 minutes for a day and a half (in the past: future reports are
        refused): the 12-hour temperature is always there once 12 hours are in."""
        start = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
        here = Location(6.24, -75.58, 1500.0)
        source = metar.MetarSource()
        missing = []
        for step in range(0, 36 * 12):
            now = start + timedelta(minutes=5 * step)
            report = now.replace(minute=20) if now.minute >= 20 else now.replace(minute=20) - timedelta(hours=1)
            rep = {"icaoId": "SKMD", "lat": 6.22, "lon": -75.59, "elev": 1499, "temp": 20 + report.hour % 5,
                   "dewp": 12, "altim": 1019.0, "obsTime": int(report.timestamp())}
            if report - start >= timedelta(hours=12):
                key = metar._full_hour(report - timedelta(hours=12))
                if key not in source._temps.get("SKMD", {}):
                    missing.append(now)
            source._remember(metar.parse([rep], here, "x", None, source._temps), now)
        self.assertEqual(missing, [])

    def test_metar_fetch_remembers(self):
        import asyncio
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

        class Http:
            async def get_json(self, url, params=None, limit=0):
                return [{"icaoId": "SKMD", "lat": 6.22, "lon": -75.59, "elev": 1499, "temp": 21, "dewp": 12,
                         "altim": 1019.0, "obsTime": int(now.timestamp())}]

        source = metar.MetarSource()
        asyncio.run(source.fetch(Http(), Location(6.24, -75.58, 1500.0), Precision.KM_10, now))
        self.assertEqual(list(source._temps["SKMD"].values()), [21.0])

    def test_previous_runs_carry_dew_point_and_pressure(self):
        data = {"hourly": {"time": ["2026-11-03T00:00", "2026-11-03T01:00"],
                           "temperature_2m_previous_day1": [5, 6], "dew_point_2m_previous_day1": [1, 2],
                           "pressure_msl_previous_day1": [1012, 2000], "wind_speed_10m_previous_day1": [10, 11],
                           "precipitation_previous_day1": [0, 0]}}
        out = parse_previous(data, ["ecmwf_ifs025"])["ecmwf_ifs025"]
        first, second = sorted(out)
        self.assertEqual(out[first]["d"], 1)
        self.assertEqual(out[first]["pr"], 1012)
        self.assertNotIn("pr", out[second])  # 2000 hPa is outside the limits

    def test_corrections_start_after_20_days_and_remove_the_bias(self):
        obs, prev = {}, {"a": {}, "b": {}, "c": {}}
        for i in range(24 * 30):
            t = START + timedelta(hours=i)
            obs[t] = {"t": 10.0, "w": 10.0, "d": 5.0, "pr": 1010.0, "t_own": True, "w_own": True, "d_own": True, "pr_own": True}
            for name, offset in (("a", 1.0), ("b", -1.0), ("c", 2.0)):
                prev[name][t] = {"t": 10.0 + offset, "w": 10.0, "d": 5.0 + offset, "pr": 1010.0 + offset}
        corr = skill.corrections(obs, prev, START + timedelta(days=30))
        self.assertAlmostEqual(corr.dew_point["c"].bias, 2.0)
        self.assertAlmostEqual(corr.pressure["a"].bias, 1.0)
        self.assertIs(corr.active("pr"), corr.pressure)
        early = skill.corrections(obs, prev, START + timedelta(days=10))
        self.assertEqual(early.dew_point, {})  # under 20 days: plain mean, nothing corrected

        hours = {name: Hour(temperature=10.0 + o, dew_point=5.0 + o, pressure=1010.0 + o, humidity=70.0, wind_speed=10.0)
                 for name, o in (("a", 1.0), ("b", -1.0), ("c", 2.0))}
        fh = blend.blend_hour(START + timedelta(days=31), hours, corr, PARIS)
        self.assertEqual(fh.dew_point, 5.0)
        self.assertEqual(fh.pressure, 1010.0)
        plain = blend.blend_hour(START + timedelta(days=31), hours, skill.Corrections(), PARIS)
        self.assertEqual(plain.dew_point, round(5.0 + 2.0 / 3.0, 1))
        # measured at an airport instead of at the home: only a share of each bias goes
        away = {t: {k: v for k, v in row.items() if not k.endswith("_own")} for t, row in obs.items()}
        corr_away = skill.corrections(away, prev, START + timedelta(days=30))
        self.assertAlmostEqual(corr_away.dew_point["c"].removed, 2.0 * skill.ELSEWHERE_SHARE["d"])
        self.assertAlmostEqual(corr_away.pressure["a"].removed, 1.0 * skill.ELSEWHERE_SHARE["pr"])


if __name__ == "__main__":
    unittest.main()
