"""Dew point and sea-level pressure are verified and corrected like
temperature (2026-11-10). Public test points only."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core.forecast import blend, history, skill  # noqa: E402
from core.forecast.members import Hour, parse_previous  # noqa: E402
from core.model import Location  # noqa: E402

PARIS = Location(48.8566, 2.3522, 35.0)
START = datetime(2026, 10, 1, tzinfo=timezone.utc)


class DewPointPressureTests(unittest.TestCase):
    def test_archive_gives_dew_point_and_pressure(self):
        text = ("station,valid,tmpc,sknt,dwpc,mslp,alti,wxcodes\n"
                "KORD,2026-11-03 00:51,6.00,7.00,2.00,1015.2,29.98,M\n"
                "LFPB,2026-11-03 02:00,8.00,5.00,4.00,M,30.00,M\n"
                "LFPB,2026-11-03 03:00,8.00,5.00,99.00,M,M,M\n")
        got = history.parse_iem(text)
        h = lambda hour: datetime(2026, 11, 3, hour, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(got[h(1)]["d"], 2.0)
        self.assertEqual(got[h(1)]["pr"], 1015.2)  # sea-level pressure from the remarks wins
        self.assertEqual(got[h(2)]["pr"], round(30.00 * 33.8639, 1))  # otherwise QNH
        self.assertNotIn("d", got[h(3)])  # 99 °C dew point is broken data
        self.assertNotIn("pr", got[h(3)])

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
            obs[t] = {"t": 10.0, "w": 10.0, "d": 5.0, "pr": 1010.0}
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


if __name__ == "__main__":
    unittest.main()
