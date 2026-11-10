"""Stored verification, the airport archive gate and a failing archive
(2026-11-10). Public test points only."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core.forecast import engine as eng  # noqa: E402
from core.forecast import history  # noqa: E402
from core.forecast.skill import Corrections, MemberStats, Score, WetScore  # noqa: E402
from core.model import Location  # noqa: E402
from core.privacy import Precision  # noqa: E402
from core.safety import HttpStatusError  # noqa: E402

PARIS = Location(48.8566, 2.3522, 35.0)
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


def _verification(updated: datetime, airport: str | None = "LFPG") -> eng.Verification:
    stats = {"ecmwf_ifs025": MemberStats(bias=0.4, mse=1.2, mae=0.9, hours=600)}
    return eng.Verification(
        corrections=Corrections(temperature=stats, wind=dict(stats), days=25, source="airport"),
        temperature=Score(blend_mae=0.8, member_mae={"ecmwf_ifs025": 0.9}, plain_mean_mae=0.85, hours=700, days=30),
        wind=None,
        wet=WetScore(csi={"blend": 0.5, "gfs_seamless": float("nan")}, wet_hours=20, hours=700),
        updated=updated, airport=airport, home_hours=12,
    )


class StoredVerificationTests(unittest.TestCase):
    def setUp(self):
        self.key = eng.verification_key(PARIS, Precision.KM_10, eng.DEFAULT_MODELS, True)

    def test_round_trip_through_json(self):
        out = eng.verification_out(_verification(NOW - timedelta(hours=2)), self.key)
        back = eng.verification_in(json.loads(json.dumps(out)), self.key, NOW)
        self.assertIsNotNone(back)
        self.assertEqual(back.corrections.temperature["ecmwf_ifs025"].bias, 0.4)
        self.assertEqual(back.corrections.days, 25)
        self.assertEqual(back.temperature.blend_mae, 0.8)
        self.assertEqual(back.airport, "LFPG")
        self.assertEqual(back.wet.csi["blend"], 0.5)
        self.assertNotEqual(back.wet.csi["gfs_seamless"], back.wet.csi["gfs_seamless"])  # NaN survives as NaN

    def test_not_reused_for_other_settings_or_when_old(self):
        out = eng.verification_out(_verification(NOW - timedelta(hours=2)), self.key)
        other = eng.verification_key(PARIS, Precision.KM_25, eng.DEFAULT_MODELS, True)
        self.assertIsNone(eng.verification_in(out, other, NOW))
        self.assertIsNone(eng.verification_in(out, eng.verification_key(PARIS, Precision.KM_10, eng.DEFAULT_MODELS, False), NOW))
        self.assertIsNone(eng.verification_in(out, self.key, NOW + timedelta(days=1)))
        self.assertIsNone(eng.verification_in(out, self.key, NOW - timedelta(hours=3)))  # from the future
        self.assertIsNone(eng.verification_in({**out, "version": 99}, self.key, NOW))
        self.assertIsNone(eng.verification_in("junk", self.key, NOW))

    def test_the_key_does_not_contain_the_point(self):
        self.assertNotIn("48.8", self.key)
        self.assertEqual(len(self.key), 24)

    def test_damaged_stats_are_dropped(self):
        out = eng.verification_out(_verification(NOW - timedelta(hours=1)), self.key)
        out["temperature_stats"] = {"a": [1, -2, 1, 5], "b": ["x", 1, 1, 1], "c": [0.1, 0.2, 0.3, 10], "d": "junk"}
        back = eng.verification_in(out, self.key, NOW)
        self.assertEqual(set(back.corrections.temperature), {"c"})

    def test_engine_stores_and_restores_it(self):
        e = eng.ForecastEngine()
        e.key = self.key
        e.verification = _verification(NOW - timedelta(hours=1))
        data = json.loads(json.dumps(e.dump()))
        fresh = eng.ForecastEngine()
        fresh.key = self.key
        fresh.load(data, NOW)
        self.assertEqual(fresh.verification.updated, NOW - timedelta(hours=1))
        stale = eng.ForecastEngine()
        stale.key = self.key
        stale.load(data, NOW + timedelta(days=2))
        self.assertIsNone(stale.verification.updated)


class FakeClock:
    def __init__(self) -> None:
        self.now = 50.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class ArchiveTests(unittest.TestCase):
    def test_gate_keeps_requests_ten_seconds_apart(self):
        clock = FakeClock()
        gate = history.Gate(history.IEM_INTERVAL, clock=clock, sleep=clock.sleep)

        async def call():
            return "ok"

        async def run():
            await gate.run(call)
            clock.now += 2
            await gate.run(call)

        asyncio.run(run())
        self.assertEqual(clock.slept, [8.0])

    def test_airports_reporting_at_quarter_past_count(self):
        text = ("station,valid,tmpc,sknt,wxcodes\n"
                "LHPA,2026-11-03 00:15,6.00,7.00,-RA\n"
                "LHPA,2026-11-03 00:45,5.00,6.00,M\n"
                "LHPA,2026-11-03 01:15,4.00,5.00,M\n"
                "EDDF,2026-11-03 02:20,9.00,4.00,M\n"
                "EDDF,2026-11-03 02:50,8.00,4.00,M\n"
                "EDDF,2026-11-03 03:30,7.00,4.00,M\n")
        got = history.parse_iem(text)
        h = lambda hour: datetime(2026, 11, 3, hour, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(got[h(0)]["t"], 6.0)  # :15 counts for 00:00
        self.assertEqual(got[h(1)]["t"], 5.0)  # :45 and 01:15 are both 15 min away: the first stays
        self.assertEqual(got[h(3)]["t"], 8.0)  # :50 is nearer to 03:00 than :20 is to 02:00's hour
        self.assertEqual(got[h(2)]["t"], 9.0)  # :20 is 20 min from 02:00: still counts
        self.assertNotIn("t", got.get(h(4), {}))  # :30 is too far from any full hour
        self.assertTrue(got[h(0)]["wet"])

    def test_a_failing_archive_keeps_a_recent_verification_and_reports(self):
        class Http:
            async def get_json(self, url, params=None, limit=0):
                return {}

            async def get_text(self, url, params=None, limit=0):
                raise HttpStatusError(429)

        old_gate = history.IEM_GATE
        history.IEM_GATE = history.Gate(0)
        try:
            e = eng.ForecastEngine()
            kept = _verification(NOW - timedelta(days=1, hours=2))
            e.verification = kept
            airport = eng.Airport("LFPG", 49.0097, 2.5479)
            error = asyncio.run(e.verify(Http(), PARIS, Precision.KM_10, NOW, airport))
            self.assertEqual(error, "HttpStatusError (HTTP 429)")
            self.assertIs(e.verification, kept)

            e.verification = _verification(NOW - timedelta(days=5))  # too old to keep: rebuilt without the archive
            error = asyncio.run(e.verify(Http(), PARIS, Precision.KM_10, NOW, airport))
            self.assertEqual(error, "HttpStatusError (HTTP 429)")
            self.assertEqual(e.verification.updated, NOW)
            self.assertIsNone(e.verification.airport)
        finally:
            history.IEM_GATE = old_gate


if __name__ == "__main__":
    unittest.main()
